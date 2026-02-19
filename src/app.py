"""FastAPI application — ESP Route Planner.

Voice agent via OpenAI Realtime API + ElevenLabs webhook fallback.
NL query -> LLM-to-SQL or route planning.
"""

import logging
import traceback
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root before any other imports that need env vars
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from esp_route_planner.database import db_exists, seed_database
from esp_route_planner.intent import is_routing_intent
from esp_route_planner.schemas import Location
from esp_route_planner.utils import OUTPUTS_DIR, ensure_output_dirs
from esp_route_planner.realtime_relay import relay_session
from esp_route_planner.webhook import handle_data_query, handle_route_query

logger = logging.getLogger(__name__)

ensure_output_dirs()

STATIC_DIR = Path(__file__).resolve().parent / "esp_route_planner" / "static"

app = FastAPI(
    title="ESP Route Planner",
    version="0.4.0",
    description="Voice-enabled ESP field assistant via OpenAI Realtime API + ElevenLabs webhook fallback.",
)

# CORS — ElevenLabs may need it
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated artifacts at /outputs/...
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    tb = traceback.format_exc()
    logger.error("Unhandled error: %s\n%s", exc, tb)
    return JSONResponse(status_code=500, content={"detail": str(exc), "traceback": tb})


# ── Health ───────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "db_seeded": db_exists()}


# ── Admin: Seed DB ───────────────────────────────────────────────────────


class SeedRequest(BaseModel):
    seed: int = 42
    force_recreate: bool = False


@app.post("/admin/seed")
def admin_seed(req: SeedRequest) -> dict:
    """Seed or recreate the DuckDB database (~20 Delaware Basin wells)."""
    result = seed_database(seed=req.seed, force_recreate=req.force_recreate)
    return {
        "total_wells": result.total_wells,
        "with_issues": result.with_issues,
        "sample_wells": result.sample_wells,
        "valid": result.valid,
        "errors": result.errors,
    }


# ── Main ElevenLabs Webhook ──────────────────────────────────────────────


class StartLocation(BaseModel):
    name: str = "Field Office"
    lat: float = 31.95
    lon: float = -103.10


class WebhookContext(BaseModel):
    start_location: StartLocation = Field(default_factory=StartLocation)
    time_budget_minutes: int = Field(default=360, gt=0)
    max_stops: int = Field(default=8, gt=0)
    top_n_candidates: int = Field(default=12, gt=0)
    must_visit_ids: list[str] = Field(default_factory=list)


class WebhookQueryRequest(BaseModel):
    query: str
    context: WebhookContext = Field(default_factory=WebhookContext)


@app.post("/webhook/elevenlabs/query")
def webhook_query(req: WebhookQueryRequest) -> dict:
    """Main ElevenLabs entrypoint — detects intent and routes accordingly."""
    if not db_exists():
        raise HTTPException(status_code=400, detail="Database not seeded. Call POST /admin/seed first.")

    base_url = "http://127.0.0.1:8000"

    if is_routing_intent(req.query):
        start = Location(
            lat=req.context.start_location.lat,
            lon=req.context.start_location.lon,
            name=req.context.start_location.name,
        )
        return handle_route_query(
            query=req.query,
            start=start,
            time_budget_minutes=req.context.time_budget_minutes,
            max_stops=req.context.max_stops,
            top_n_candidates=req.context.top_n_candidates,
            must_visit_ids=req.context.must_visit_ids,
            base_url=base_url,
        )
    else:
        return handle_data_query(query=req.query, base_url=base_url)


# ── Direct Route Webhook (for testing) ───────────────────────────────────


class DirectRouteRequest(BaseModel):
    query: str = "Plan my visits"
    start_location: StartLocation = Field(default_factory=StartLocation)
    time_budget_minutes: int = Field(default=360, gt=0)
    max_stops: int = Field(default=8, gt=0)
    top_n_candidates: int = Field(default=12, gt=0)
    must_visit_ids: list[str] = Field(default_factory=list)


@app.post("/webhook/elevenlabs/route")
def webhook_route(req: DirectRouteRequest) -> dict:
    """Direct routing hook — bypasses intent detection."""
    if not db_exists():
        raise HTTPException(status_code=400, detail="Database not seeded. Call POST /admin/seed first.")

    start = Location(
        lat=req.start_location.lat,
        lon=req.start_location.lon,
        name=req.start_location.name,
    )

    return handle_route_query(
        query=req.query,
        start=start,
        time_budget_minutes=req.time_budget_minutes,
        max_stops=req.max_stops,
        top_n_candidates=req.top_n_candidates,
        must_visit_ids=req.must_visit_ids,
        base_url="http://127.0.0.1:8000",
    )


# ── OpenAI Realtime Voice Agent ──────────────────────────────────────────


@app.get("/realtime")
def realtime_page():
    """Serve the voice assistant HTML client."""
    return FileResponse(STATIC_DIR / "realtime.html")


@app.websocket("/ws/realtime")
async def websocket_realtime(ws: WebSocket):
    """WebSocket relay between browser and OpenAI Realtime API."""
    await ws.accept()
    logger.info("Realtime WebSocket client connected")
    try:
        await relay_session(ws)
    except WebSocketDisconnect:
        logger.info("Realtime WebSocket client disconnected")
    except Exception as e:
        logger.error("Realtime WebSocket error: %s", e, exc_info=True)


# Mount static files last (after all route definitions)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
