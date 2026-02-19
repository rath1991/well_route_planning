"""Pydantic v2 models for Data & Intelligence Agent responses."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Shared ───────────────────────────────────────────────────────────────


class WellSummary(BaseModel):
    well_id: str
    name: str
    oil_bpd: float
    priority_score: float
    issues: list[str]
    action_required: str
    corridor: str
    field: str
    county: str


class ScoreBreakdownItem(BaseModel):
    well_id: str
    prod_score: float
    uplift_score: float
    urgency_score: float
    confidence_score: float
    recency_score: float
    priority_score: float


# ── A) Reliability Summary ───────────────────────────────────────────────


class ReliabilitySummaryResponse(BaseModel):
    total_esp: int
    reliability_issue_count: int
    pct_impacted: float
    methodology: str


# ── B) Common Issues ─────────────────────────────────────────────────────


class IssueCategoryCount(BaseModel):
    category: str
    count: int
    description: str


class CommonIssuesResponse(BaseModel):
    issue_counts: list[IssueCategoryCount]
    top_issue_category: str
    summary: str


# ── C) High Priority Wells ──────────────────────────────────────────────


class HighPriorityWell(BaseModel):
    well_id: str
    name: str
    oil_bpd: float
    priority_score: float
    scoring_breakdown: ScoreBreakdownItem
    issues: list[str]
    action_required: str
    corridor: str
    field: str


class HighPriorityRequest(BaseModel):
    top_n: int = Field(default=10, gt=0, le=50)


class HighPriorityResponse(BaseModel):
    wells: list[HighPriorityWell]
    summary: str


# ── D) High Producing Impacted ──────────────────────────────────────────


class HighProducingRequest(BaseModel):
    oil_threshold_bpd: float = Field(default=300, gt=0)


class HighProducingResponse(BaseModel):
    threshold_bpd: float
    count: int
    wells: list[WellSummary]
    summary: str


# ── E) Workover ─────────────────────────────────────────────────────────


class WorkoverWell(BaseModel):
    well_id: str
    name: str
    oil_bpd: float
    issues: list[str]
    action_required: str
    workover_horizon: str
    corridor: str


class WorkoverResponse(BaseModel):
    immediate: list[WorkoverWell]
    three_to_six_months: list[WorkoverWell]
    summary: str


# ── F) Location Summary ─────────────────────────────────────────────────


class LocationGroup(BaseModel):
    group_key: str
    count: int
    example_wells: list[str]


class LocationSummaryRequest(BaseModel):
    group_by: str = Field(default="corridor", pattern="^(corridor|county|field)$")


class LocationSummaryResponse(BaseModel):
    group_by: str
    groups: list[LocationGroup]
    summary: str


# ── G) Not Observed ─────────────────────────────────────────────────────


class NotObservedResponse(BaseModel):
    not_observed_categories: list[str]
    summary: str


# ── Admin Seed ──────────────────────────────────────────────────────────


class SeedRequest(BaseModel):
    seed: int = 42
    force_recreate: bool = False


class SeedResponse(BaseModel):
    total_esp: int
    reliability_issue_count: int
    restart_vsd_count: int
    gauge_flatlined_count: int
    motor_current_spiky_count: int
    amps_rising_flat_prod_count: int
    vibration_high_count: int
    workover_immediate_count: int
    workover_3_6_months_count: int
    valid: bool
    errors: list[str]


# ── CandidateWellSet (bridge between Intelligence → Routing) ────────────


class CandidateWell(BaseModel):
    """A well scored by the intelligence agent, ready for routing."""
    well_id: str
    name: str
    lat: float
    lon: float
    oil_bpd: float
    uplift_oil_bpd: float
    water_cut_pct: float
    priority_score: float
    scoring_breakdown: ScoreBreakdownItem
    issues: list[str]
    action_required: str
    service_minutes: int
    confidence: float
    days_since_last_visit: int | None
    asset: str
    corridor: str
    ctb: str
    pad_name: str


class CandidateWellSet(BaseModel):
    """Output of the Data & Intelligence Agent, input to the Routing Agent."""
    wells: list[CandidateWell]
    total_scored: int
    filtered_count: int
    methodology: str
