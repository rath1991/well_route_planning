"""FastAPI application — ESP Route Planner.

Voice agent via OpenAI Realtime API + ElevenLabs webhook fallback.
NL query -> LLM-to-SQL or route planning.
"""

import logging
import os
import shutil
import traceback
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root before any other imports that need env vars
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from esp_route_planner.database import DB_PATH, db_exists, refresh_view, seed_database
from esp_route_planner.intent import is_routing_intent
from esp_route_planner.schemas import Location
from esp_route_planner.security import admin_guard, webhook_guard
from esp_route_planner.utils import OUTPUTS_DIR, ensure_output_dirs
from esp_route_planner.realtime_relay import relay_session
from esp_route_planner.webhook import handle_data_query, handle_route_from_cached, handle_route_query

logger = logging.getLogger(__name__)

IS_PROD = os.environ.get("ENV", "dev").lower() == "prod"

# ── Startup: auto-load DB from committed data/ if missing ────────────────────

_DATA_DB = Path(__file__).resolve().parents[1] / "data" / "esp_delaware.duckdb"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not db_exists():
        if _DATA_DB.exists():
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(_DATA_DB, DB_PATH)
            logger.info("Loaded database from data/esp_delaware.duckdb")
        else:
            seed_database()
            logger.info("Seeded fresh database")
    # Always refresh the view so schema changes take effect on existing DBs
    refresh_view()
    yield

# In-memory store for the latest data query result (updated on every data query)
_latest_result: dict = {}
# In-memory store for the latest route map (updated on every route query)
_latest_map: dict = {}
# Full data_preview from the most recent data query.
# ElevenLabs sends two separate HTTP requests (data, then route); this bridges them.
# The route endpoint uses this directly, supplementing from DB only for missing lat/lon.
_last_data_preview: list[dict] = []
_last_well_ids: list[str] = []  # kept for realtime relay compatibility

ensure_output_dirs()

STATIC_DIR = Path(__file__).resolve().parent / "esp_route_planner" / "static"

app = FastAPI(
    title="ESP Route Planner",
    version="0.4.0",
    description="Voice-enabled ESP field assistant via OpenAI Realtime API + ElevenLabs webhook fallback.",
    docs_url=None if IS_PROD else "/docs",
    redoc_url=None if IS_PROD else "/redoc",
    openapi_url=None if IS_PROD else "/openapi.json",
    lifespan=lifespan,
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


@app.post("/admin/seed", dependencies=[Depends(admin_guard)])
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


@app.get("/results")
def results_page():
    """Serve the persistent query-results window."""
    return FileResponse(STATIC_DIR / "results.html")


@app.get("/api/latest-result")
def latest_result():
    """Return the most recent data query result for the results page to poll."""
    return _latest_result


@app.get("/map")
def map_page():
    """Serve the persistent route-map window."""
    return FileResponse(STATIC_DIR / "map.html")


@app.get("/api/latest-map")
def latest_map():
    """Return the most recent route map URL for the map page to poll."""
    return _latest_map


@app.post("/webhook/elevenlabs/query", dependencies=[Depends(webhook_guard)])
def webhook_query(req: WebhookQueryRequest, request: Request) -> dict:
    """Main ElevenLabs entrypoint — detects intent and routes accordingly."""
    global _latest_result, _latest_map, _last_data_preview, _last_well_ids

    if not db_exists():
        raise HTTPException(status_code=400, detail="Database not seeded. Call POST /admin/seed first.")

    # Build base_url from the incoming request so ngrok / custom domains work
    base_url = str(request.base_url).rstrip("/")

    if is_routing_intent(req.query):
        start = Location(
            lat=req.context.start_location.lat,
            lon=req.context.start_location.lon,
            name=req.context.start_location.name,
        )
        # If the caller explicitly provides must_visit_ids, honour them.
        # Otherwise use the full cached data_preview from the previous data query —
        # coordinates are fetched from DB inside handle_route_from_cached if missing.
        if req.context.must_visit_ids:
            route_result = handle_route_query(
                query=req.query,
                start=start,
                time_budget_minutes=req.context.time_budget_minutes,
                max_stops=req.context.max_stops,
                top_n_candidates=req.context.top_n_candidates,
                must_visit_ids=req.context.must_visit_ids,
                base_url=base_url,
            )
        elif _last_data_preview:
            logger.info("Using cached data_preview (%d rows) for routing", len(_last_data_preview))
            route_result = handle_route_from_cached(
                cached_rows=_last_data_preview,
                start=start,
                time_budget_minutes=req.context.time_budget_minutes,
                base_url=base_url,
            )
        else:
            route_result = handle_route_query(
                query=req.query,
                start=start,
                time_budget_minutes=req.context.time_budget_minutes,
                max_stops=req.context.max_stops,
                top_n_candidates=req.context.top_n_candidates,
                base_url=base_url,
            )

        # Store latest map for the persistent /map polling page
        map_url = route_result.get("artifacts", {}).get("map_url", "")
        if map_url:
            _latest_map = {
                "map_url": map_url,
                "spoken_text": route_result.get("spoken_text", ""),
            }
        route_result["map_page_url"] = f"{base_url}/map"
        return route_result
    else:
        result = handle_data_query(query=req.query, base_url=base_url)

        # Cache the full data_preview for follow-up route queries
        try:
            preview = result.get("data_preview") or []
            if preview:
                _last_data_preview = preview
                _last_well_ids = [r["well_id"] for r in preview if "well_id" in r]
                logger.info("Cached %d rows (%d with well_id) from data query",
                            len(preview), len(_last_well_ids))
        except Exception:
            pass

        results_url = f"{base_url}/results"

        # Store for the polling page
        _latest_result = {"query": req.query, **result}

        # Open the results window locally — skipped in prod (server has no browser)
        if not IS_PROD:
            webbrowser.open(results_url)

        # Include URL in response so ngrok / remote clients can open it too
        result["results_url"] = results_url
        return result


# ── Direct Route Webhook (for testing) ───────────────────────────────────


class DirectRouteRequest(BaseModel):
    query: str = "Plan my visits"
    start_location: StartLocation = Field(default_factory=StartLocation)
    time_budget_minutes: int = Field(default=360, gt=0)
    max_stops: int = Field(default=8, gt=0)
    top_n_candidates: int = Field(default=12, gt=0)
    must_visit_ids: list[str] = Field(default_factory=list)


@app.post("/webhook/elevenlabs/route", dependencies=[Depends(webhook_guard)])
def webhook_route(req: DirectRouteRequest, request: Request) -> dict:
    """Direct routing hook — bypasses intent detection."""
    global _latest_map

    if not db_exists():
        raise HTTPException(status_code=400, detail="Database not seeded. Call POST /admin/seed first.")

    base_url = str(request.base_url).rstrip("/")

    start = Location(
        lat=req.start_location.lat,
        lon=req.start_location.lon,
        name=req.start_location.name,
    )

    if req.must_visit_ids:
        route_result = handle_route_query(
            query=req.query,
            start=start,
            time_budget_minutes=req.time_budget_minutes,
            max_stops=req.max_stops,
            top_n_candidates=req.top_n_candidates,
            must_visit_ids=req.must_visit_ids,
            base_url=base_url,
        )
    elif _last_data_preview:
        logger.info("Route endpoint: using cached data_preview (%d rows)", len(_last_data_preview))
        route_result = handle_route_from_cached(
            cached_rows=_last_data_preview,
            start=start,
            time_budget_minutes=req.time_budget_minutes,
            base_url=base_url,
        )
    else:
        route_result = handle_route_query(
            query=req.query,
            start=start,
            time_budget_minutes=req.time_budget_minutes,
            max_stops=req.max_stops,
            top_n_candidates=req.top_n_candidates,
            base_url=base_url,
        )

    map_url = route_result.get("artifacts", {}).get("map_url", "")
    if map_url:
        _latest_map = {
            "map_url": map_url,
            "spoken_text": route_result.get("spoken_text", ""),
        }

    if not IS_PROD:
        webbrowser.open(map_url)

    route_result["map_page_url"] = f"{base_url}/map"
    return route_result


# ── OpenAI Realtime Voice Agent ──────────────────────────────────────────


@app.get("/realtime", dependencies=[Depends(admin_guard)])
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
