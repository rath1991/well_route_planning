"""FastAPI application — ESP Route Planner."""

import logging
import traceback
import webbrowser

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from esp_route_planner.mock_data import generate_mock_wells
from esp_route_planner.planner import plan_route
from esp_route_planner.schemas import (
    MockGenerateRequest,
    MockGenerateResponse,
    RoutePlanRequest,
    RoutePlanResponse,
)
from esp_route_planner.utils import OUTPUTS_DIR, ensure_output_dirs

logger = logging.getLogger(__name__)

ensure_output_dirs()

app = FastAPI(
    title="ESP Route Planner",
    version="0.1.0",
    description="Priority-maximizing route optimizer for ESP well visits.",
)

# Serve generated artifacts (maps, plans, mock data) at /outputs/...
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    tb = traceback.format_exc()
    logger.error("Unhandled error: %s\n%s", exc, tb)
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "traceback": tb},
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/mock/generate", response_model=MockGenerateResponse)
def mock_generate(req: MockGenerateRequest) -> MockGenerateResponse:
    """Generate a synthetic well dataset and save to disk."""
    return generate_mock_wells(req)


@app.post("/plan/route", response_model=RoutePlanResponse)
def route_plan(req: RoutePlanRequest) -> RoutePlanResponse:
    """Compute an optimized route plan for ESP well visits."""
    # Validate must-visit IDs exist
    well_ids = {w.well_id for w in req.wells}
    missing = set(req.constraints.must_visit_ids) - well_ids
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"must_visit_ids not found in wells: {sorted(missing)}",
        )

    if not req.wells:
        raise HTTPException(status_code=400, detail="No wells provided.")

    result = plan_route(req, base_url="http://127.0.0.1:8000")

    if result is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No feasible route found. Try increasing time_budget_minutes, "
                "reducing max_stops, or removing must_visit constraints."
            ),
        )

    # Auto-open the map in the default browser
    if result.artifacts.map_view_url:
        webbrowser.open(result.artifacts.map_view_url)

    return result
