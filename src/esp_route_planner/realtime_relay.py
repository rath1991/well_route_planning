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

When the user asks a DATA question (production stats, well info, issues, counts, etc.), \
call the data_query tool with their question.

When the user asks to PLAN A ROUTE, schedule visits, or optimize their day, \
call the route_query tool.

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
            "Returns a schedule with ETAs and generates an animated map."
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
            },
            "required": ["query"],
        },
    },
]


def _execute_function(name: str, arguments: dict) -> str:
    """Execute a tool function and return the JSON result string."""
    if not db_exists():
        return json.dumps({"error": "Database not seeded. Seed it first via /admin/seed."})

    try:
        if name == "data_query":
            result = handle_data_query(
                query=arguments.get("query", ""),
                base_url="http://127.0.0.1:8000",
            )
        elif name == "route_query":
            start = Location(lat=31.95, lon=-103.10, name="Field Office")
            result = handle_route_query(
                query=arguments.get("query", ""),
                start=start,
                time_budget_minutes=int(arguments.get("time_budget_minutes", 360)),
                max_stops=int(arguments.get("max_stops", 8)),
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

            # Run two forwarding tasks in parallel
            await asyncio.gather(
                _forward_client_to_openai(client_ws, openai_ws),
                _forward_openai_to_client(openai_ws, client_ws, base_url),
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


async def _forward_openai_to_client(openai_ws, client_ws, base_url: str) -> None:
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
                    None, _execute_function, fn_name, fn_args
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

            # Forward all events to client (including function call events for transcript)
            await client_ws.send_text(raw_message)

    except websockets.exceptions.ConnectionClosed:
        logger.info("OpenAI WebSocket closed")
    except Exception as e:
        logger.error("Error forwarding from OpenAI: %s", e, exc_info=True)
