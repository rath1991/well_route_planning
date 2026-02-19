# ESP Route Planner — ElevenLabs Webhook API

Priority-maximizing route optimizer for ESP well visits with **LLM-to-SQL analytics** and **animated transit maps**. Designed as a webhook backend for ElevenLabs voice agents — no ElevenLabs coding required, just configure it to call our endpoints.

## Architecture

```
User (voice) -> ElevenLabs -> POST /webhook/elevenlabs/query -> Intent Detection
                                |
                  +--------------+--------------+
                  |                             |
              Data Intent                  Route Intent
                  |                             |
          NL -> SQL (OpenAI)           Fetch top wells from DB
                  |                             |
          Execute on DuckDB            OR-Tools route solver
                  |                             |
          Spoken summary               Animated transit map
```

- **Data questions**: LLM converts natural language to SQL, executes on DuckDB, returns voice-friendly summary
- **Route questions**: Fetches prioritized wells, solves OR-Tools routing, generates animated Folium map

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Set your OpenAI API key (required for data queries):
```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="gpt-4o-mini"  # optional, defaults to gpt-4o-mini
```

## Run

```bash
uvicorn src.app:app --reload
```

Server runs at `http://127.0.0.1:8000`. Interactive docs at `http://127.0.0.1:8000/docs`.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| POST | `/admin/seed` | Seed DuckDB (~20 Delaware Basin wells) |
| POST | `/webhook/elevenlabs/query` | Main webhook (auto-detects data vs route) |
| POST | `/webhook/elevenlabs/route` | Direct route planning (testing) |

## Example curl Commands

### 1. Seed the database

```bash
curl -X POST http://127.0.0.1:8000/admin/seed \
  -H "Content-Type: application/json" \
  -d '{"seed": 42, "force_recreate": true}'
```

### 2. Data question (analytics)

```bash
curl -X POST http://127.0.0.1:8000/webhook/elevenlabs/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Which high producing wells have reliability issues?"
  }'
```

More data question examples:
```bash
# How many wells have issues?
curl -X POST http://127.0.0.1:8000/webhook/elevenlabs/query \
  -H "Content-Type: application/json" \
  -d '{"query": "How many ESP wells have reliability issues?"}'

# Most common issues
curl -X POST http://127.0.0.1:8000/webhook/elevenlabs/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What are the most common ESP reliability issues?"}'

# Wells needing workover
curl -X POST http://127.0.0.1:8000/webhook/elevenlabs/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Which wells need workover?"}'

# Top priority wells
curl -X POST http://127.0.0.1:8000/webhook/elevenlabs/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Which wells should I focus on first?"}'
```

### 3. Route question (auto-detected)

```bash
curl -X POST http://127.0.0.1:8000/webhook/elevenlabs/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Plan my visits for 6 hours. Max 8 stops.",
    "context": {
      "start_location": {"name": "Field Office", "lat": 31.95, "lon": -103.10},
      "time_budget_minutes": 360,
      "max_stops": 8
    }
  }'
```

### 4. Direct route endpoint (testing)

```bash
curl -X POST http://127.0.0.1:8000/webhook/elevenlabs/route \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Plan my visits for 6 hours. Max 8 stops.",
    "start_location": {"name": "Field Office", "lat": 31.95, "lon": -103.10},
    "time_budget_minutes": 360,
    "max_stops": 8,
    "top_n_candidates": 12
  }'
```

## Response Format

### Data mode
```json
{
  "mode": "data",
  "spoken_text": "There are 14 ESP wells with reliability issues...",
  "sql": "SELECT COUNT(*) ...",
  "data_preview": [...]
}
```

### Route mode
```json
{
  "mode": "route",
  "spoken_text": "I planned a route visiting 6 wells with total priority 385...",
  "route_order": ["START", "BEXAR-P12121WA", ..., "END"],
  "schedule": [...],
  "artifacts": {
    "map_url": "http://127.0.0.1:8000/outputs/maps/map_20250101_080000.html",
    "plan_url": "http://127.0.0.1:8000/outputs/plans/plan_20250101_080000.json"
  }
}
```

## Animated Transit Map

Route maps use **TimestampedGeoJson** + **AntPath** for segment-by-segment animated progression:
- Timeline slider shows travel progression over time
- Each segment shows approximate drive time
- Numbered stop markers with priority breakdown popups
- Skipped wells shown as gray dots
- Route commentary panel (top-right)

## DuckDB Schema

~20 wells in Delaware Basin (TX/NM). Tables: `wells`, `production_latest`, `reliability_flags_latest`, `ops_recommendations_latest`. View: `well_priority_vw` computes priority_score (0-100).

## SQL Safety

LLM-generated SQL passes through `sql_guard.py`:
- Blocks INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, etc.
- Enforces single SELECT/WITH statement only
- Auto-adds LIMIT 50 if missing

## ElevenLabs Integration

No ElevenLabs coding required. Configure ElevenLabs to call:
- **Tool URL**: `POST http://your-server/webhook/elevenlabs/query`
- **Request schema**: `{ "query": "string", "context": { ... } }`
- The `spoken_text` field in the response is designed for voice readback.

## Outputs

- `outputs/plans/` — route plan results (JSON)
- `outputs/maps/` — animated Folium maps (HTML)
- `outputs/db/` — DuckDB database file
