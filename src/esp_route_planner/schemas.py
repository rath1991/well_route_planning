"""Pydantic v2 models for the ESP route planning service."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Well record ──────────────────────────────────────────────────────────


class Well(BaseModel):
    """Single ESP well with production data and operational metadata."""

    well_id: str
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    current_oil_bpd: float = Field(ge=0)
    current_liquid_bpd: float = Field(ge=0)
    water_cut_pct: float = Field(ge=0, le=100)
    uplift_oil_bpd: float = Field(ge=0)
    issues: list[str] = Field(default_factory=list)
    action_required: str = ""
    freq_current_hz: float | None = None
    freq_optimal_hz: float | None = None
    service_minutes: int = 35
    confidence: float = Field(ge=0, le=1)
    days_since_last_visit: int | None = None
    days_since_last_well_test: int | None = None
    # Chevron-specific context
    asset: str = ""
    corridor: str = ""
    ctb: str = ""
    pad_name: str = ""


# ── Location ─────────────────────────────────────────────────────────────


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    name: str = "Base"


# ── Request ──────────────────────────────────────────────────────────────


class Constraints(BaseModel):
    time_budget_minutes: int = Field(gt=0)
    max_stops: int = Field(gt=0)
    must_visit_ids: list[str] = Field(default_factory=list)
    min_priority_threshold: float = 0.0
    seed: int | None = None
    avg_speed_kmph: float = Field(default=45, gt=0)


class Options(BaseModel):
    travel_time_mode: str = Field(default="haversine", pattern="^(haversine|google)$")


class RoutePlanRequest(BaseModel):
    """Full route-plan request from field engineer."""

    query: str = Field(description="Natural language query for traceability")
    start: Location
    end: Location | None = None
    wells: list[Well]
    constraints: Constraints
    options: Options = Field(default_factory=Options)


# ── Scoring breakdown ───────────────────────────────────────────────────


class ScoreBreakdown(BaseModel):
    """Per-well scoring components (each 0–100)."""

    well_id: str
    prod_score: float
    uplift_score: float
    urgency_score: float
    confidence_score: float
    recency_score: float
    priority_score: float  # weighted total (0–100)


# ── Response ─────────────────────────────────────────────────────────────


class ScheduleStop(BaseModel):
    stop_id: str
    name: str = ""
    stop_number: int = 0
    eta: str
    depart: str
    drive_minutes: float
    service_minutes: float
    priority_score: float
    action_required: str = ""
    why_selected: str = ""


class Totals(BaseModel):
    drive_minutes: float
    service_minutes: float
    total_minutes: float
    total_priority_score: float
    wells_visited: int
    wells_skipped: int


class Artifacts(BaseModel):
    map_html_path: str
    map_view_url: str | None = None
    google_maps_url: str | None = None
    plan_json_path: str


class RoutePlanResponse(BaseModel):
    selected_well_ids: list[str]
    route_order: list[str]
    schedule: list[ScheduleStop]
    scoring_breakdown: list[ScoreBreakdown]
    totals: Totals
    rationale: str
    artifacts: Artifacts


# ── Mock generate ────────────────────────────────────────────────────────


class MockGenerateRequest(BaseModel):
    n: int = Field(default=20, gt=0, le=500)
    center_lat: float = Field(default=31.95, ge=-90, le=90)
    center_lon: float = Field(default=-102.1, ge=-180, le=180)
    radius_km: float = Field(default=60, gt=0)
    seed: int = 42


class MockGenerateResponse(BaseModel):
    dataset_id: str
    saved_path: str
    wells_preview: list[Well]
