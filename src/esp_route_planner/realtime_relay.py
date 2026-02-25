"""OpenAI Realtime API relay server.

Proxies WebSocket connections between a browser client and OpenAI's
Realtime API.  Intercepts function-call events to execute data queries
and route planning locally, then feeds results back so the model can
speak the answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import websockets

from .database import db_exists
from .schemas import Location
from .webhook import handle_data_query, handle_route_query

logger = logging.getLogger(__name__)

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"

SYSTEM_INSTRUCTIONS = """\
You are George, an expert ESP (Electric Submersible Pump) field engineer assistant \
for the Delaware Basin in West Texas and Southeast New Mexico.

You help field engineers with:
- Querying well production data, reliability issues, water cut, priority scores
- Planning optimized field visit routes that maximize priority while respecting time budgets

TOOL SELECTION — follow these rules strictly:

1. Use data_query for ALL analytical questions: production numbers, well counts, issue \
   lists, water cut percentages, priority rankings, comparisons, any "how many", \
   "which wells", "what is", "show me", "list", "top N" type questions.

2. Use route_query ONLY when the user explicitly asks to plan a route, schedule field \
   visits, optimize travel for the day, or create a visit itinerary.

3. After completing a route plan, if the user asks an analytical question \
   (e.g., "how many wells have high water cut?", "what are production numbers?", \
   "which wells have motor issues?"), always use data_query — NOT route_query.

4. When the user says "plan my visit to these wells" or "route me to those wells" \
   after a data_query showed specific wells, call route_query DIRECTLY — do NOT \
   call data_query again first. Extract the well_id values from the previous \
   data_query result and pass them in the well_ids parameter of route_query. \
   This ensures the route visits exactly those wells and nothing extra.

Always speak results conversationally — summarize numbers, highlight the most important \
wells, and keep responses concise for voice. If a tool returns an error, explain it \
simply and suggest the user rephrase.
"""

TOOLS = [
    {
        "type": "function",
        "name": "data_query",
        "description": (
            "Query ESP well database for production data, reliability issues, "
            "water cut percentages, priority scores, well counts, issue categories, "
            "geographic distribution, and any analytical question about the wells."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The natural language data question to answer via SQL",
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "route_query",
        "description": (
            "Plan an optimized field visit route to ESP wells. Maximizes total "
            "priority score under a time budget using real driving times. "
            "Returns a schedule with ETAs and generates an animated map. "
            "Use ONLY for explicit route/visit planning requests — never for analytics."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The route planning request in natural language",
                },
                "time_budget_minutes": {
                    "type": "number",
                    "description": "Total time budget in minutes (default 360 = 6 hours)",
                },
                "max_stops": {
                    "type": "number",
                    "description": "Maximum number of well stops (default 8)",
                },
                "well_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Specific well IDs to visit, extracted from a previous data_query "
                        "result. When the user says 'these wells' or 'those wells' referring "
                        "to wells shown in a prior data result, pass those well_id values here."
                    ),
                },
            },
            "required": ["query"],
        },
    },
]


def _execute_function(name: str, arguments: dict, session_context: dict) -> str:
    """Execute a tool function and return the JSON result string.

    session_context is a shared mutable dict used to pass well IDs across turns:
      - After a data_query, well_ids from data_preview are stored here.
      - On route_query, cached IDs are injected when the model doesn't supply them.
    """
    if not db_exists():
        return json.dumps({"error": "Database not seeded. Seed it first via /admin/seed."})

    try:
        if name == "data_query":
            result = handle_data_query(
                query=arguments.get("query", ""),
                base_url="http://127.0.0.1:8000",
            )
            # Store well_ids from this result for potential use in a follow-up route query
            try:
                preview = result.get("data_preview") or []
                ids = [r["well_id"] for r in preview if "well_id" in r]
                if ids:
                    session_context["last_well_ids"] = ids
                    logger.info("Session context updated with %d well IDs", len(ids))
                else:
                    logger.info("data_query returned no well_id column — session context not updated")
            except Exception:
                pass

        elif name == "route_query":
            start = Location(lat=31.95, lon=-103.10, name="Field Office")

            # Use the SNAPSHOT taken at the end of the previous model turn.
            # This is immune to intra-turn intermediate data_queries the model may
            # fire before route_query — those overwrite last_well_ids but NOT the
            # snapshot, which is only updated at response.done (turn boundary).
            # Fall back to last_well_ids for the very first turn (no snapshot yet).
            stable_ids = (
                session_context.get("well_ids_snapshot")
                or session_context.get("last_well_ids")
                or []
            )
            model_ids = arguments.get("well_ids") or []
            if stable_ids:
                well_ids = stable_ids
                source = "snapshot" if session_context.get("well_ids_snapshot") else "last_well_ids"
                logger.info("Using %d well IDs from %s (model passed: %s)", len(well_ids), source, model_ids)
            else:
                well_ids = model_ids
                logger.info("No server context — using model-provided well_ids: %s", well_ids)

            result = handle_route_query(
                query=arguments.get("query", ""),
                start=start,
                time_budget_minutes=int(arguments.get("time_budget_minutes", 360)),
                max_stops=int(arguments.get("max_stops", 8)),
                must_visit_ids=well_ids,
                base_url="http://127.0.0.1:8000",
            )
        else:
            result = {"error": f"Unknown function: {name}"}
    except Exception as e:
        logger.error("Function %s failed: %s", name, e, exc_info=True)
        result = {"error": str(e)}

    return json.dumps(result, default=str)


async def relay_session(client_ws, base_url: str = "http://127.0.0.1:8000") -> None:
    """Run a full relay session between *client_ws* and OpenAI Realtime API."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        await client_ws.send_json({"error": "OPENAI_API_KEY not configured"})
        await client_ws.close()
        return

    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Beta": "realtime=v1",
    }

    try:
        async with websockets.connect(
            OPENAI_REALTIME_URL,
            additional_headers=headers,
            max_size=2**24,  # 16 MB for audio chunks
        ) as openai_ws:
            logger.info("Connected to OpenAI Realtime API")

            # Configure session with tools and instructions
            session_config = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": SYSTEM_INSTRUCTIONS,
                    "voice": "ash",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 500,
                    },
                    "tools": TOOLS,
                    "tool_choice": "auto",
                    "temperature": 0.7,
                },
            }
            await openai_ws.send(json.dumps(session_config))
            logger.info("Session configured with tools and instructions")

            # Shared mutable context for this session (well IDs persist across turns)
            session_context: dict = {}

            # Run two forwarding tasks in parallel
            await asyncio.gather(
                _forward_client_to_openai(client_ws, openai_ws),
                _forward_openai_to_client(openai_ws, client_ws, base_url, session_context),
            )

    except websockets.exceptions.ConnectionClosed as e:
        logger.info("OpenAI WebSocket closed: %s", e)
    except Exception as e:
        logger.error("Relay session error: %s", e, exc_info=True)
    finally:
        logger.info("Relay session ended")


async def _forward_client_to_openai(client_ws, openai_ws) -> None:
    """Forward messages from browser client to OpenAI."""
    try:
        while True:
            data = await client_ws.receive_text()
            await openai_ws.send(data)
    except Exception:
        # Client disconnected
        pass


async def _forward_openai_to_client(
    openai_ws, client_ws, base_url: str, session_context: dict
) -> None:
    """Forward messages from OpenAI to browser client, intercepting function calls."""
    try:
        async for raw_message in openai_ws:
            event = json.loads(raw_message)
            event_type = event.get("type", "")

            # Intercept completed function calls
            if event_type == "response.function_call_arguments.done":
                call_id = event.get("call_id", "")
                fn_name = event.get("name", "")
                fn_args_str = event.get("arguments", "{}")

                logger.info("Function call: %s(%s)", fn_name, fn_args_str)

                try:
                    fn_args = json.loads(fn_args_str)
                except json.JSONDecodeError:
                    fn_args = {}

                # Execute function in a thread to avoid blocking the event loop
                loop = asyncio.get_event_loop()
                result_str = await loop.run_in_executor(
                    None, _execute_function, fn_name, fn_args, session_context
                )

                logger.info("Function result length: %d chars", len(result_str))

                # Send function output back to OpenAI
                fn_output_event = {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result_str,
                    },
                }
                await openai_ws.send(json.dumps(fn_output_event))

                # Trigger response generation from the function result
                await openai_ws.send(json.dumps({"type": "response.create"}))

                # Forward structured result to browser for visual display in data panel
                try:
                    await client_ws.send_text(json.dumps({
                        "type": "tool.result",
                        "name": fn_name,
                        "query": fn_args.get("query", ""),
                        "result": json.loads(result_str),
                    }))
                except Exception as panel_err:
                    logger.warning("Could not send tool.result to browser: %s", panel_err)

            # At the end of a model turn, snapshot the current well IDs.
            # route_query uses the snapshot (not last_well_ids) so that
            # model-internal data_queries fired *within* the same turn
            # (before route_query) cannot overwrite the user-visible result.
            elif event_type == "response.done":
                if session_context.get("last_well_ids"):
                    session_context["well_ids_snapshot"] = list(session_context["last_well_ids"])
                    logger.info(
                        "Snapshotted %d well IDs at response.done",
                        len(session_context["well_ids_snapshot"]),
                    )

            # Forward all events to client (including function call events for transcript)
            await client_ws.send_text(raw_message)

    except websockets.exceptions.ConnectionClosed:
        logger.info("OpenAI WebSocket closed")
    except Exception as e:
        logger.error("Error forwarding from OpenAI: %s", e, exc_info=True)
