# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn src.app:app --reload          # http://127.0.0.1:8000
```

Environment variables are loaded from `.env` at project root (gitignored).

## Architecture: ElevenLabs Webhook API

Single entrypoint (`POST /webhook/elevenlabs/query`) with intent detection:

### Data Intent (analytics questions)
1. OpenAI LLM converts NL -> SQL (`llm_sql.py`)
2. `sql_guard.py` validates SQL is read-only
3. Execute on DuckDB, format voice-friendly response

### Route Intent (visit planning questions)
1. Fetch top-N wells from `well_priority_vw` in DuckDB
2. OR-Tools solver maximizes priority_score under time/stop constraints (`optimizer.py`)
3. Generate animated Folium map with TimestampedGeoJson (`visualize.py`)

### Key: Routing agent never computes priority — it only optimizes based on DB scores.

## Key Modules

- `src/app.py` — FastAPI app with webhook endpoints + CORS
- `src/esp_route_planner/database.py` — DuckDB seeding (~20 Delaware Basin wells), `well_priority_vw` view
- `src/esp_route_planner/llm_sql.py` — NL-to-SQL via OpenAI (few-shot + schema)
- `src/esp_route_planner/sql_guard.py` — SQL safety guard (blocks writes/DDL, enforces SELECT only)
- `src/esp_route_planner/intent.py` — Route vs data intent detection via keyword matching
- `src/esp_route_planner/webhook.py` — Orchestrates data/route handlers
- `src/esp_route_planner/optimizer.py` — OR-Tools solver (disjunction penalty = priority_score * 1000)
- `src/esp_route_planner/visualize.py` — Folium map with TimestampedGeoJson + AntPath + drive time labels
- `src/esp_route_planner/schemas.py` — Pydantic v2 models
- `src/esp_route_planner/travel_time.py` — Haversine travel time matrix
- `src/esp_route_planner/scoring.py` — Priority formula (used by legacy /plan/route)

## DuckDB Schema

Tables: `wells`, `production_latest`, `reliability_flags_latest`, `ops_recommendations_latest`
View: `well_priority_vw` — priority_score = 0.30*prod + 0.25*uplift + 0.25*urgency + 0.10*confidence + 0.10*recency

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| POST | `/admin/seed` | Seed DuckDB |
| POST | `/webhook/elevenlabs/query` | Main webhook (data or route) |
| POST | `/webhook/elevenlabs/route` | Direct route (testing) |

## Environment Variables

- `OPENAI_API_KEY` — required for data queries (LLM-to-SQL)
- `OPENAI_MODEL` — optional, defaults to `gpt-4o-mini`

## Git

Do not add Co-Authored-By trailers to commit messages.
