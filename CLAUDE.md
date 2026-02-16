# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

ESP Route Planner — a FastAPI microservice that selects optimal ESP wells to visit and computes priority-maximizing routes under time/stop constraints. Uses OR-Tools (prize-collecting VRP with disjunction penalties) and Folium for animated map generation.

The objective is **maximize total priority_score captured** (dimensionless 0–100, not money). Priority is derived from production criticality, uplift potential, operational urgency, data confidence, and visit recency.

## Build & Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn src.app:app --reload
```

## Architecture

Entry point: `src/app.py` (FastAPI). Domain logic in `src/esp_route_planner/`:

- **schemas.py** — Pydantic v2 models. Well includes Chevron-specific fields (asset, corridor, ctb, pad_name, action_required, days_since_last_visit/test).
- **scoring.py** — Deterministic priority formula: `0.30*prod + 0.25*uplift + 0.25*urgency + 0.10*confidence + 0.10*recency`. Returns `ScoreBreakdown` per well.
- **mock_data.py** — Chevron-style well names (CMC RANGER 4844CL), realistic issues (well_test_quality_issue, pump_efficiency_drop, etc.), operator-phrased actions.
- **travel_time.py** — (N+2)x(N+2) haversine travel-time matrix.
- **optimizer.py** — OR-Tools solver. Disjunction penalty = priority_score * 1000 (integer). Time dimension = drive + service. Count dimension = max_stops.
- **planner.py** — Orchestrates scoring -> filtering -> matrix -> optimizer -> schedule -> artifacts. Generates per-stop `why_selected` explanations.
- **visualize.py** — Folium map with AntPath animation, numbered stop markers (DivIcon), commentary panel, skipped wells as gray dots.
- **utils.py** — Haversine, time formatting, output dirs.

## Key design decisions

- Priority score replaces reward/money. Each component (prod, uplift, urgency, confidence, recency) is 0–100; weighted sum gives final 0–100 score.
- Optimizer: minimizing "lost priority" via disjunction penalties = maximizing captured priority under constraints.
- `scoring_breakdown` in response gives full transparency into why each well scored as it did.
- Map auto-opens in browser on route plan. Static files served at `/outputs/`.
- Deterministic: same inputs always produce same outputs (PATH_CHEAPEST_ARC + GUIDED_LOCAL_SEARCH).

## Endpoints

- `GET /health`
- `POST /mock/generate` — Chevron-style synthetic wells (Permian Basin)
- `POST /plan/route` — main optimizer (returns schedule, scoring breakdown, rationale, animated map)
