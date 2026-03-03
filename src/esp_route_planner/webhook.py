"""Webhook handler — orchestrates data queries and route planning.

Single entrypoint for ElevenLabs: detects intent, runs LLM-to-SQL or routing,
returns voice-friendly JSON.
"""

from __future__ import annotations

import logging
import os
import webbrowser
from datetime import datetime
from pathlib import Path

import duckdb

from .database import get_connection, get_schema_text
from .intent import is_routing_intent
from .llm_sql import nl_to_sql
from .optimizer import solve_route
from .schemas import Location, ScoreBreakdown, ScheduleStop
from .sql_guard import SQLGuardError, validate_sql
from .travel_time import build_time_matrix
from .utils import OUTPUTS_DIR, ensure_output_dirs, haversine_km, minutes_from_start
from .visualize import build_transit_map

logger = logging.getLogger(__name__)

SERVICE_MINUTES_DEFAULT = 35


# ── Data intent handler ───────────────────────────────────────────────────


def handle_data_query(query: str, base_url: str = "http://127.0.0.1:8000") -> dict:
    """Handle a data/analytics question via LLM-to-SQL."""
    schema = get_schema_text()

    try:
        raw_sql = nl_to_sql(query, schema)
    except (ValueError, Exception) as e:
        return {
            "mode": "data",
            "spoken_text": f"I couldn't generate a query: {e}",
            "sql": None,
            "data_preview": None,
        }

    try:
        safe_sql = validate_sql(raw_sql)
    except SQLGuardError:
        return {
            "mode": "data",
            "spoken_text": "I couldn't run that safely -- please rephrase your question.",
            "sql": raw_sql,
            "data_preview": None,
        }

    con = get_connection()
    try:
        result = con.execute(safe_sql).fetchall()
        columns = [desc[0] for desc in con.description]
    except Exception as e:
        logger.error("SQL execution error: %s\nSQL: %s", e, safe_sql)
        con.close()
        return {
            "mode": "data",
            "spoken_text": f"The query failed: {e}. Please try rephrasing.",
            "sql": safe_sql,
            "data_preview": None,
        }
    finally:
        con.close()

    # Format rows as list of dicts
    rows = [dict(zip(columns, row)) for row in result]

    # Generate spoken text from results
    spoken = _summarize_data_result(query, columns, rows)

    return {
        "mode": "data",
        "spoken_text": spoken,
        "sql": safe_sql,
        "data_preview": rows[:20],
    }


def _summarize_data_result(query: str, columns: list[str], rows: list[dict]) -> str:
    """Generate a concise spoken summary of query results."""
    if not rows:
        return "No results found for your question."

    n = len(rows)

    # Single aggregate value
    if n == 1 and len(columns) == 1:
        val = rows[0][columns[0]]
        return f"The answer is {val}."

    # Single row with named columns
    if n == 1:
        parts = [f"{col}: {rows[0][col]}" for col in columns]
        return f"Here's the result: {', '.join(parts)}."

    # Count/group results
    if n <= 10:
        lines = []
        for row in rows:
            vals = [f"{col} {row[col]}" for col in columns]
            lines.append(", ".join(vals))
        return f"Found {n} results. " + ". ".join(lines[:5]) + "."

    # Many rows
    sample = rows[:3]
    first_col = columns[0]
    names = [str(r.get("name", r.get("well_id", r[first_col]))) for r in sample]
    return (
        f"Found {n} results. Top entries include: {', '.join(names)}. "
        f"Check the data_preview field for full details."
    )


# ── Route intent handler ─────────────────────────────────────────────────


_WELL_COLS = """
    SELECT well_id, name, lat, lon, oil_bpd, uplift_oil_bpd,
           water_cut_pct, priority_score, prod_score, uplift_score,
           urgency_score, confidence_score, recency_score,
           issue_category, action_required, days_since_last_visit,
           confidence
    FROM well_priority_vw
"""


def handle_route_from_cached(
    cached_rows: list[dict],
    start: Location,
    time_budget_minutes: int = 360,
    base_url: str = "http://127.0.0.1:8000",
) -> dict:
    """Plan a route using the full output from a previous data query.

    Uses cached_rows directly to identify which wells to visit.
    If coordinates or routing fields are missing, performs a supplemental
    SQL query using well names as the lookup key.
    """
    if not cached_rows:
        return {
            "mode": "route",
            "spoken_text": "No cached query results to route. Run a data query first.",
            "route_order": [], "schedule": [], "artifacts": {},
        }

    # Prefer well_id lookup; fall back to name-based lookup
    well_ids = [r["well_id"] for r in cached_rows if r.get("well_id")]
    well_names = [r["name"] for r in cached_rows if r.get("name") and not r.get("well_id")]

    con = get_connection()
    if well_ids:
        ph = ", ".join("?" * len(well_ids))
        candidates = con.execute(
            _WELL_COLS + f" WHERE well_id IN ({ph})", well_ids
        ).fetchall()
    elif well_names:
        # Supplemental query: extract routing data using well names as placeholder
        ph = ", ".join("?" * len(well_names))
        candidates = con.execute(
            _WELL_COLS + f" WHERE name IN ({ph})", well_names
        ).fetchall()
    else:
        con.close()
        return {
            "mode": "route",
            "spoken_text": "Could not identify wells from the cached results.",
            "route_order": [], "schedule": [], "artifacts": {},
        }
    con.close()

    if not candidates:
        return {
            "mode": "route",
            "spoken_text": "None of the cached wells were found in the database.",
            "route_order": [], "schedule": [], "artifacts": {},
        }

    # Extract the well_ids resolved from DB and delegate to the core route handler.
    # This guarantees all routing fields (lat/lon, scores) come from the DB,
    # while the set of wells to visit exactly matches what was shown to the user.
    resolved_ids = [c[0] for c in candidates]  # well_id is first column
    return handle_route_query(
        query="plan route to selected wells",
        start=start,
        time_budget_minutes=time_budget_minutes,
        max_stops=len(resolved_ids),  # attempt to visit all cached wells
        must_visit_ids=resolved_ids,
        base_url=base_url,
    )


def handle_route_query(
    query: str,
    start: Location,
    end: Location | None = None,
    time_budget_minutes: int = 360,
    max_stops: int = 8,
    top_n_candidates: int = 12,
    must_visit_ids: list[str] | None = None,
    base_url: str = "http://127.0.0.1:8000",
) -> dict:
    """Handle a routing question — fetch wells from DB, solve, build map."""
    end = end or start
    must_visit_ids = must_visit_ids or []

    con = get_connection()

    if must_visit_ids:
        # Explicit wells specified: fetch exactly those wells, visit all of them
        placeholders = ", ".join("?" * len(must_visit_ids))
        candidates = con.execute(
            _WELL_COLS + f" WHERE well_id IN ({placeholders})",
            must_visit_ids,
        ).fetchall()
    else:
        # Default: fetch top-N candidates by priority score
        candidates = con.execute(
            _WELL_COLS + " ORDER BY priority_score DESC LIMIT ?",
            [top_n_candidates],
        ).fetchall()

    con.close()

    if not candidates:
        return {
            "mode": "route",
            "spoken_text": "No candidate wells found in the database. Seed the database first.",
            "route_order": [],
            "schedule": [],
            "artifacts": {},
        }

    # Build well data structures
    wells = []
    priority_scores = []
    breakdowns = []
    for c in candidates:
        (wid, name, lat, lon, oil, uplift, wc, pscore,
         prod_s, uplift_s, urgency_s, conf_s, recency_s,
         issue_cat, action, days_visit, confidence) = c

        wells.append({
            "well_id": wid, "name": name, "lat": lat, "lon": lon,
            "oil_bpd": oil, "uplift_oil_bpd": uplift, "water_cut_pct": wc,
            "priority_score": pscore, "issue_category": issue_cat or "",
            "action_required": action or "", "days_since_last_visit": days_visit,
            "confidence": confidence,
        })
        priority_scores.append(pscore)
        breakdowns.append(ScoreBreakdown(
            well_id=wid, prod_score=prod_s, uplift_score=uplift_s,
            urgency_score=urgency_s, confidence_score=conf_s,
            recency_score=recency_s, priority_score=pscore,
        ))

    # Build Location-compatible objects for travel time matrix
    from .schemas import Well as SchemaWell
    schema_wells = [
        SchemaWell(
            well_id=w["well_id"], name=w["name"], lat=w["lat"], lon=w["lon"],
            current_oil_bpd=w["oil_bpd"], current_liquid_bpd=w["oil_bpd"] * 2,
            water_cut_pct=w["water_cut_pct"], uplift_oil_bpd=w["uplift_oil_bpd"],
            issues=[w["issue_category"]] if w["issue_category"] else [],
            action_required=w["action_required"],
            service_minutes=SERVICE_MINUTES_DEFAULT,
            confidence=w["confidence"],
            days_since_last_visit=w["days_since_last_visit"],
        )
        for w in wells
    ]

    if must_visit_ids:
        # All fetched wells are the user's explicit selection — visit every one
        must_indices = list(range(len(wells)))
        effective_max_stops = len(wells)
    else:
        must_indices = []
        effective_max_stops = max_stops

    # Build travel time matrix
    time_matrix = build_time_matrix(start, end, schema_wells, avg_speed_kmph=45)

    # Solve route
    service_times = [SERVICE_MINUTES_DEFAULT] * len(wells)
    result = solve_route(
        time_matrix=time_matrix,
        priority_scores=priority_scores,
        service_times=service_times,
        must_visit_indices=must_indices,
        max_stops=effective_max_stops,
        time_budget_minutes=time_budget_minutes,
    )

    if result.status == "NO_SOLUTION":
        return {
            "mode": "route",
            "spoken_text": "No feasible route found. Try increasing the time budget or reducing max stops.",
            "route_order": [],
            "schedule": [],
            "artifacts": {},
        }

    # Build schedule
    schedule = []
    elapsed = 0.0
    prev_lat, prev_lon = start.lat, start.lon

    schedule.append({
        "stop_id": "START", "name": start.name, "stop_number": 0,
        "eta": minutes_from_start(0), "drive_minutes": 0,
        "service_minutes": 0, "priority_score": 0,
    })

    total_drive = 0.0
    total_service = 0.0

    for rank, well_idx in enumerate(result.visited_indices, 1):
        w = wells[well_idx]
        d_km = haversine_km(prev_lat, prev_lon, w["lat"], w["lon"])
        drive_min = round(d_km / 45 * 60, 1)
        elapsed += drive_min
        eta = elapsed
        elapsed += SERVICE_MINUTES_DEFAULT
        total_drive += drive_min
        total_service += SERVICE_MINUTES_DEFAULT

        schedule.append({
            "stop_id": w["well_id"], "name": w["name"], "stop_number": rank,
            "eta": minutes_from_start(eta), "drive_minutes": drive_min,
            "service_minutes": SERVICE_MINUTES_DEFAULT,
            "priority_score": round(w["priority_score"], 1),
            "action_required": w["action_required"],
        })
        prev_lat, prev_lon = w["lat"], w["lon"]

    # Return leg
    d_end = haversine_km(prev_lat, prev_lon, end.lat, end.lon)
    drive_end = round(d_end / 45 * 60, 1)
    elapsed += drive_end
    total_drive += drive_end

    schedule.append({
        "stop_id": "END", "name": end.name, "stop_number": len(result.visited_indices) + 1,
        "eta": minutes_from_start(elapsed), "drive_minutes": drive_end,
        "service_minutes": 0, "priority_score": 0,
    })

    # Route order
    selected_ids = [wells[i]["well_id"] for i in result.visited_indices]
    route_order = ["START"] + selected_ids + ["END"]

    # Build map
    map_path = build_transit_map(
        start, end, wells, result.visited_indices, breakdowns, schedule,
    )
    map_relative = Path(map_path).relative_to(OUTPUTS_DIR)
    map_url = f"{base_url}/outputs/{map_relative}"

    # Save plan JSON
    ensure_output_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    plan_path = OUTPUTS_DIR / "plans" / f"plan_{ts}.json"

    import json
    plan_data = {
        "route_order": route_order,
        "schedule": schedule,
        "totals": {
            "drive_minutes": round(total_drive, 1),
            "service_minutes": round(total_service, 1),
            "total_minutes": round(elapsed, 1),
            "total_priority_score": round(sum(priority_scores[i] for i in result.visited_indices), 1),
            "wells_visited": len(result.visited_indices),
            "wells_skipped": len(wells) - len(result.visited_indices),
        },
    }
    plan_path.write_text(json.dumps(plan_data, indent=2), encoding="utf-8")
    plan_url = f"{base_url}/outputs/plans/plan_{ts}.json"

    # Open the animated map in the local browser — skipped in prod (server has no browser)
    if os.environ.get("ENV", "dev").lower() != "prod":
        webbrowser.open(map_url)

    total_priority = sum(priority_scores[i] for i in result.visited_indices)
    n_visited = len(result.visited_indices)
    n_total = len(wells)
    n_skipped = n_total - n_visited

    if n_skipped > 0 and must_visit_ids:
        # User explicitly selected wells but not all fit in the time budget
        skipped_names = [
            wells[i]["name"]
            for i in range(n_total)
            if i not in result.visited_indices
        ]
        skip_msg = (
            f" {n_skipped} well{'s' if n_skipped > 1 else ''} from your selection "
            f"could not fit in the {time_budget_minutes}-minute budget "
            f"({', '.join(skipped_names[:3])}{'…' if n_skipped > 3 else ''}). "
            f"Increase time_budget_minutes to include more."
        )
    else:
        skip_msg = ""

    spoken = (
        f"I planned a route visiting {n_visited} "
        f"{'of your ' + str(n_total) + ' selected ' if must_visit_ids else ''}"
        f"wells with total priority score {total_priority:.0f}. "
        f"Total time: {elapsed:.0f} minutes ({total_drive:.0f} driving, "
        f"{total_service:.0f} service). "
        f"Top stop: {wells[result.visited_indices[0]]['name']} "
        f"with priority {wells[result.visited_indices[0]]['priority_score']:.0f}."
        f"{skip_msg}"
    )

    return {
        "mode": "route",
        "spoken_text": spoken,
        "route_order": route_order,
        "schedule": schedule,
        "artifacts": {
            "map_url": map_url,
            "plan_url": plan_url,
        },
    }
