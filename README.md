# ESP Route Planner

Priority-maximizing route optimizer for ESP well visits. Given a set of wells with production data and operational signals, computes an optimized day-plan that maximizes total **priority score** captured under time and stop constraints.

Uses OR-Tools (orienteering / prize-collecting VRP) for optimization and Folium for interactive animated map generation.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
uvicorn src.app:app --reload
```

Server runs at `http://127.0.0.1:8000`. Interactive docs at `http://127.0.0.1:8000/docs`.

## Priority Scoring

Each well receives a deterministic priority score (0–100) based on weighted operational signals:

```
priority_score = 0.30 * prod_score      (production criticality)
               + 0.25 * uplift_score    (potential bpd improvement)
               + 0.25 * urgency_score   (issues + freq gap + water cut)
               + 0.10 * confidence_score (data quality)
               + 0.10 * recency_score   (days since last visit)
```

The optimizer maximizes total priority score captured, not revenue.

## API Endpoints

### Health check

```bash
curl http://127.0.0.1:8000/health
```

### Generate mock wells (Chevron-style)

```bash
curl -X POST http://127.0.0.1:8000/mock/generate \
  -H "Content-Type: application/json" \
  -d '{
    "n": 20,
    "center_lat": 31.95,
    "center_lon": -102.1,
    "radius_km": 60,
    "seed": 42
  }'
```

### Plan a route

#### Example 1: CMC corridor day route

```bash
curl -X POST http://127.0.0.1:8000/plan/route \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Plan ESP optimization route for CMC corridor wells",
    "start": {"lat": 31.95, "lon": -102.1, "name": "Midland Field Office"},
    "wells": [
      {
        "well_id": "CMC-4844CL", "name": "CMC RANGER 4844CL",
        "lat": 32.01, "lon": -102.05,
        "current_oil_bpd": 420, "current_liquid_bpd": 1050,
        "water_cut_pct": 60.0, "uplift_oil_bpd": 28,
        "issues": ["frequency_suboptimal", "well_test_quality_issue"],
        "action_required": "Confirm VSD frequency change window",
        "freq_current_hz": 52.0, "freq_optimal_hz": 56.0,
        "service_minutes": 35, "confidence": 0.85,
        "days_since_last_visit": 21, "days_since_last_well_test": 45,
        "asset": "Permian Basin", "corridor": "Central Midland Corridor",
        "ctb": "CTB 17", "pad_name": "CMC 48 Pad A"
      },
      {
        "well_id": "VLT-0451WA", "name": "VLT VULCAN 0451WA",
        "lat": 32.08, "lon": -101.95,
        "current_oil_bpd": 310, "current_liquid_bpd": 885,
        "water_cut_pct": 65.0, "uplift_oil_bpd": 35,
        "issues": ["model_calibration_issue"],
        "action_required": "Recalibrate model inputs; check fluid properties",
        "freq_current_hz": 48.0, "freq_optimal_hz": 50.0,
        "service_minutes": 40, "confidence": 0.72,
        "days_since_last_visit": 35, "days_since_last_well_test": 60,
        "asset": "Permian Basin", "corridor": "Violet",
        "ctb": "CTB 6", "pad_name": "VLT 4 Pad B"
      },
      {
        "well_id": "CMC-0325WA", "name": "CMC SNAPDRAGON 0325WA",
        "lat": 31.88, "lon": -102.2,
        "current_oil_bpd": 550, "current_liquid_bpd": 2200,
        "water_cut_pct": 75.0, "uplift_oil_bpd": 15,
        "issues": ["high_water_cut_trend", "sensor_drift_suspected"],
        "action_required": "Investigate rising water cut; confirm separators",
        "service_minutes": 30, "confidence": 0.68,
        "days_since_last_visit": 14, "days_since_last_well_test": 30,
        "asset": "Permian Basin", "corridor": "Central Midland Corridor",
        "ctb": "CTB 17", "pad_name": "CMC 3 Pad C"
      },
      {
        "well_id": "NMC-1122DL", "name": "NMC FALCON 1122DL",
        "lat": 32.15, "lon": -101.85,
        "current_oil_bpd": 180, "current_liquid_bpd": 450,
        "water_cut_pct": 60.0, "uplift_oil_bpd": 38,
        "issues": ["pump_efficiency_drop"],
        "action_required": "Inspect pump stages; review vibration data",
        "service_minutes": 45, "confidence": 0.90,
        "days_since_last_visit": 42, "days_since_last_well_test": 75,
        "asset": "Permian Basin", "corridor": "North Midland Corridor",
        "ctb": "CTB 12", "pad_name": "NMC 11 Pad A"
      },
      {
        "well_id": "SMC-2200SL", "name": "SMC MUSTANG 2200SL",
        "lat": 31.78, "lon": -102.3,
        "current_oil_bpd": 290, "current_liquid_bpd": 725,
        "water_cut_pct": 60.0, "uplift_oil_bpd": 22,
        "issues": [],
        "action_required": "",
        "service_minutes": 30, "confidence": 0.55,
        "days_since_last_visit": 7,
        "asset": "Permian Basin", "corridor": "South Midland Corridor",
        "ctb": "CTB 21", "pad_name": "SMC 22 Pad B"
      }
    ],
    "constraints": {
      "time_budget_minutes": 420,
      "max_stops": 4,
      "seed": 42
    }
  }'
```

#### Example 2: Must-visit with tight budget

```bash
curl -X POST http://127.0.0.1:8000/plan/route \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Urgent route - must visit CMC RANGER and NMC FALCON today",
    "start": {"lat": 31.95, "lon": -102.1, "name": "Midland Field Office"},
    "end": {"lat": 31.95, "lon": -102.1, "name": "Midland Field Office"},
    "wells": [
      {
        "well_id": "CMC-4844CL", "name": "CMC RANGER 4844CL",
        "lat": 32.01, "lon": -102.05,
        "current_oil_bpd": 420, "current_liquid_bpd": 1050,
        "water_cut_pct": 60.0, "uplift_oil_bpd": 28,
        "issues": ["frequency_suboptimal", "well_test_quality_issue"],
        "action_required": "Confirm VSD frequency change window",
        "freq_current_hz": 52.0, "freq_optimal_hz": 56.0,
        "service_minutes": 35, "confidence": 0.85,
        "days_since_last_visit": 21,
        "asset": "Permian Basin", "corridor": "Central Midland Corridor",
        "ctb": "CTB 17", "pad_name": "CMC 48 Pad A"
      },
      {
        "well_id": "NMC-1122DL", "name": "NMC FALCON 1122DL",
        "lat": 32.15, "lon": -101.85,
        "current_oil_bpd": 180, "current_liquid_bpd": 450,
        "water_cut_pct": 60.0, "uplift_oil_bpd": 38,
        "issues": ["pump_efficiency_drop"],
        "action_required": "Inspect pump stages; review vibration data",
        "service_minutes": 45, "confidence": 0.90,
        "days_since_last_visit": 42,
        "asset": "Permian Basin", "corridor": "North Midland Corridor",
        "ctb": "CTB 12", "pad_name": "NMC 11 Pad A"
      },
      {
        "well_id": "VLT-0451WA", "name": "VLT VULCAN 0451WA",
        "lat": 32.08, "lon": -101.95,
        "current_oil_bpd": 310, "current_liquid_bpd": 885,
        "water_cut_pct": 65.0, "uplift_oil_bpd": 35,
        "issues": ["model_calibration_issue"],
        "action_required": "Recalibrate model inputs; check fluid properties",
        "service_minutes": 40, "confidence": 0.72,
        "days_since_last_visit": 35,
        "asset": "Permian Basin", "corridor": "Violet",
        "ctb": "CTB 6", "pad_name": "VLT 4 Pad B"
      }
    ],
    "constraints": {
      "time_budget_minutes": 240,
      "max_stops": 3,
      "must_visit_ids": ["CMC-4844CL", "NMC-1122DL"],
      "seed": 42
    }
  }'
```

#### Example 3: High-priority-only filter

```bash
curl -X POST http://127.0.0.1:8000/plan/route \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Only visit wells with priority score above 50",
    "start": {"lat": 31.95, "lon": -102.1, "name": "Midland Field Office"},
    "wells": [
      {
        "well_id": "CMC-4844CL", "name": "CMC RANGER 4844CL",
        "lat": 32.01, "lon": -102.05,
        "current_oil_bpd": 420, "current_liquid_bpd": 1050,
        "water_cut_pct": 60.0, "uplift_oil_bpd": 28,
        "issues": ["frequency_suboptimal", "well_test_quality_issue"],
        "action_required": "Confirm VSD frequency change window",
        "freq_current_hz": 52.0, "freq_optimal_hz": 56.0,
        "service_minutes": 35, "confidence": 0.85,
        "days_since_last_visit": 21,
        "asset": "Permian Basin", "corridor": "Central Midland Corridor",
        "ctb": "CTB 17", "pad_name": "CMC 48 Pad A"
      },
      {
        "well_id": "SMC-2200SL", "name": "SMC MUSTANG 2200SL",
        "lat": 31.78, "lon": -102.3,
        "current_oil_bpd": 290, "current_liquid_bpd": 725,
        "water_cut_pct": 60.0, "uplift_oil_bpd": 22,
        "issues": [],
        "action_required": "",
        "service_minutes": 30, "confidence": 0.55,
        "days_since_last_visit": 7,
        "asset": "Permian Basin", "corridor": "South Midland Corridor",
        "ctb": "CTB 21", "pad_name": "SMC 22 Pad B"
      },
      {
        "well_id": "NMC-1122DL", "name": "NMC FALCON 1122DL",
        "lat": 32.15, "lon": -101.85,
        "current_oil_bpd": 180, "current_liquid_bpd": 450,
        "water_cut_pct": 60.0, "uplift_oil_bpd": 38,
        "issues": ["pump_efficiency_drop"],
        "action_required": "Inspect pump stages; review vibration data",
        "service_minutes": 45, "confidence": 0.90,
        "days_since_last_visit": 42,
        "asset": "Permian Basin", "corridor": "North Midland Corridor",
        "ctb": "CTB 12", "pad_name": "NMC 11 Pad A"
      }
    ],
    "constraints": {
      "time_budget_minutes": 480,
      "max_stops": 8,
      "min_priority_threshold": 50,
      "seed": 42
    }
  }'
```

## Response Structure

The `/plan/route` response includes:

- **route_order**: Ordered list of stop IDs (`["START", "CMC-4844CL", ..., "END"]`)
- **schedule**: Per-stop ETA, departure, drive time, service time, priority score, action required, and why it was selected
- **scoring_breakdown**: Full scoring components for every candidate well (prod, uplift, urgency, confidence, recency)
- **totals**: Aggregate drive/service/total minutes, total priority score, wells visited/skipped
- **rationale**: Human-readable explanation of the optimizer's decisions
- **artifacts**: Paths/URLs to the generated map and plan JSON

## Map Visualization

When `/plan/route` is hit, the server automatically opens an interactive Folium map in your browser. The map shows:

- **Numbered stop markers** (1, 2, 3...) in visit order
- **Animated route** (AntPath) showing the driving path
- **Route commentary panel** with per-stop reasoning
- **Skipped wells** shown as gray dots
- Click any marker for full details (priority breakdown, issues, action required)

Maps are also saved at `outputs/maps/` and served at `http://127.0.0.1:8000/outputs/maps/`.

## Outputs

- `outputs/mock/` — mock well datasets (JSON)
- `outputs/plans/` — route plan results (JSON)
- `outputs/maps/` — interactive Folium maps (HTML)

## Travel time modes

- `haversine` (default) — great-circle distance with configurable `avg_speed_kmph`
- `google` — stub for Google Maps API integration (not implemented)
