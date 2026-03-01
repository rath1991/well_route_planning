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
1. OpenAI/Azure OpenAI LLM converts NL -> SQL (`llm_sql.py`)
2. `sql_guard.py` validates SQL is read-only
3. Execute on DuckDB, format voice-friendly response

### Route Intent (visit planning questions)
1. Fetch top-N wells from `well_priority_vw` in DuckDB
2. OR-Tools solver maximizes priority_score under time/stop constraints (`optimizer.py`)
3. Generate animated Folium map with TimestampedGeoJson (`visualize.py`)

### Key: Routing agent never computes priority — it only optimizes based on DB scores.

## Key Modules

- `src/app.py` — FastAPI app with webhook endpoints + CORS + security dependencies
- `src/esp_route_planner/database.py` — DuckDB seeding (~20 Delaware Basin wells), `well_priority_vw` view
- `src/esp_route_planner/llm_sql.py` — NL-to-SQL via Azure OpenAI (preferred) or OpenAI fallback
- `src/esp_route_planner/azure_config.py` — Azure OpenAI config, AzureCliCredential client, Realtime WSS URL builder
- `src/esp_route_planner/security.py` — webhook_guard (rate limit + size cap + x-webhook-secret), admin_guard (x-admin-secret)
- `src/esp_route_planner/sql_guard.py` — SQL safety guard (blocks writes/DDL, enforces SELECT only)
- `src/esp_route_planner/intent.py` — Route vs data intent detection via keyword matching
- `src/esp_route_planner/webhook.py` — Orchestrates data/route handlers
- `src/esp_route_planner/optimizer.py` — OR-Tools solver (disjunction penalty = priority_score * 1000)
- `src/esp_route_planner/visualize.py` — Folium map with TimestampedGeoJson + AntPath + drive time labels
- `src/esp_route_planner/schemas.py` — Pydantic v2 models
- `src/esp_route_planner/travel_time.py` — Haversine travel time matrix
- `src/esp_route_planner/realtime_relay.py` — OpenAI/Azure Realtime API WebSocket relay

## DuckDB Schema

Tables: `wells`, `production_latest`, `reliability_flags_latest`, `ops_recommendations_latest`
View: `well_priority_vw` — priority_score = 0.30*prod + 0.25*uplift + 0.25*urgency + 0.10*confidence + 0.10*recency

All wells always have an `issue_category` — no NULLs in top priority queries.
`wells.avg_repair_hours` — historical average hours to diagnose and fix issues at each well (HIGH: 5-10h, MEDIUM: 2-5h, LOW: 1-3h).

Well name prefixes are generic Delaware Basin operator names: APEX, FRONTIER, SUMMIT, BASIN, PLAINS, VECTOR, HORIZON, TRINITY.

Seeded databases are committed under `data/` (esp_delaware.duckdb, esp_mock.duckdb).
Re-seed via `POST /admin/seed` with `{"force_recreate": true}`.

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | Public | Health check |
| GET | `/results` | Public | Query results display page |
| GET | `/api/latest-result` | Public | Polled by results page |
| POST | `/admin/seed` | x-admin-secret | Seed DuckDB |
| GET | `/realtime` | x-admin-secret | Voice agent HTML client |
| POST | `/webhook/elevenlabs/query` | x-webhook-secret | Main webhook (data or route) |
| POST | `/webhook/elevenlabs/route` | x-webhook-secret | Direct route (testing) |
| WS | `/ws/realtime` | — | Realtime API relay |

## Security

- `webhook_guard` — applied to webhook routes: 30 req/min/IP rate limit, 256 KB body cap, `x-webhook-secret` header check (403 on mismatch). Secret never logged.
- `admin_guard` — applied to admin/internal routes: `x-admin-secret` header check (401 on mismatch).
- Docs (`/docs`, `/redoc`, `/openapi.json`) disabled when `ENV=prod`.
- Guards are no-ops in dev if secrets are not set in `.env`.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Fallback | Standard OpenAI (used if Azure not configured) |
| `OPENAI_MODEL` | No | Chat model name, defaults to gpt-4o-mini |
| `AZURE_OPENAI_ENDPOINT` | Azure | Azure OpenAI resource endpoint |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Azure | Chat completion deployment name |
| `AZURE_OPENAI_API_VERSION` | Azure | Azure OpenAI API version |
| `AZURE_OPENAI_REALTIME_DEPLOYMENT` | Azure | Realtime deployment name |
| `ELEVENLABS_WEBHOOK_SECRET` | Prod | From ElevenLabs dashboard — header: `x-webhook-secret` |
| `ADMIN_SECRET` | Prod | For `/admin/*` and `/realtime` — header: `x-admin-secret` |
| `ENV` | No | Set to `prod` to disable docs and enforce all guards |

## Agents

- `duckdb-schema-manager` — use for any DuckDB schema changes, new tables/views, or synthetic data seeding.

## Git

Do not add Co-Authored-By trailers to commit messages.
