"""High-level planner — ties scoring, optimizer, and visualization together."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .optimizer import solve_route
from .schemas import (
    Artifacts,
    RoutePlanRequest,
    RoutePlanResponse,
    ScheduleStop,
    ScoreBreakdown,
    Totals,
)
from .scoring import score_well
from .travel_time import build_time_matrix
from .utils import OUTPUTS_DIR, ensure_output_dirs, haversine_km, minutes_from_start
from .visualize import build_map, google_maps_url


def plan_route(
    req: RoutePlanRequest,
    base_url: str = "http://127.0.0.1:8000",
) -> RoutePlanResponse | None:
    """Execute the full planning pipeline and return a response."""
    end = req.end or req.start
    wells = list(req.wells)
    c = req.constraints

    # 1. Score every well
    all_breakdowns = [score_well(w) for w in wells]
    scores = [b.priority_score for b in all_breakdowns]

    # 2. Filter wells below threshold (keep must-visit)
    must_ids = set(c.must_visit_ids)
    keep_indices: list[int] = []
    for i, w in enumerate(wells):
        if w.well_id in must_ids or scores[i] >= c.min_priority_threshold:
            keep_indices.append(i)

    filtered_wells = [wells[i] for i in keep_indices]
    filtered_scores = [scores[i] for i in keep_indices]
    filtered_breakdowns = [all_breakdowns[i] for i in keep_indices]
    # Map original must-visit to new indices
    must_visit_new: list[int] = [
        j for j, i in enumerate(keep_indices) if wells[i].well_id in must_ids
    ]

    # 3. Travel time matrix
    time_matrix = build_time_matrix(
        req.start, end, filtered_wells, c.avg_speed_kmph
    )

    # 4. Optimize
    result = solve_route(
        time_matrix=time_matrix,
        priority_scores=filtered_scores,
        service_times=[w.service_minutes for w in filtered_wells],
        must_visit_indices=must_visit_new,
        max_stops=c.max_stops,
        time_budget_minutes=c.time_budget_minutes,
    )

    if result.status == "NO_SOLUTION":
        return None

    # 5. Build schedule
    schedule: list[ScheduleStop] = []
    elapsed = 0.0  # minutes from 08:00
    prev_lat, prev_lon = req.start.lat, req.start.lon
    stop_num = 0

    # START entry
    schedule.append(
        ScheduleStop(
            stop_id="START",
            name=req.start.name,
            stop_number=0,
            eta=minutes_from_start(0),
            depart=minutes_from_start(0),
            drive_minutes=0,
            service_minutes=0,
            priority_score=0,
        )
    )

    total_service = 0.0
    total_drive = 0.0

    for well_idx in result.visited_indices:
        w = filtered_wells[well_idx]
        stop_num += 1

        d_km = haversine_km(prev_lat, prev_lon, w.lat, w.lon)
        drive_min = round(d_km / c.avg_speed_kmph * 60, 2)

        elapsed += drive_min
        eta = elapsed
        elapsed += w.service_minutes
        depart = elapsed

        total_drive += drive_min
        total_service += w.service_minutes

        # Build "why selected" explanation
        bd = filtered_breakdowns[well_idx]
        why = _why_selected(w, bd)

        schedule.append(
            ScheduleStop(
                stop_id=w.well_id,
                name=w.name,
                stop_number=stop_num,
                eta=minutes_from_start(eta),
                depart=minutes_from_start(depart),
                drive_minutes=round(drive_min, 1),
                service_minutes=w.service_minutes,
                priority_score=bd.priority_score,
                action_required=w.action_required,
                why_selected=why,
            )
        )
        prev_lat, prev_lon = w.lat, w.lon

    # Drive back to end
    d_end = haversine_km(prev_lat, prev_lon, end.lat, end.lon)
    drive_end = round(d_end / c.avg_speed_kmph * 60, 2)
    elapsed += drive_end
    total_drive += drive_end

    schedule.append(
        ScheduleStop(
            stop_id="END",
            name=end.name,
            stop_number=stop_num + 1,
            eta=minutes_from_start(elapsed),
            depart=minutes_from_start(elapsed),
            drive_minutes=round(drive_end, 1),
            service_minutes=0,
            priority_score=0,
        )
    )

    total_priority = sum(filtered_scores[i] for i in result.visited_indices)
    visited_set = set(result.visited_indices)

    # 6. Generate artifacts
    map_path = build_map(
        req.start, end, filtered_wells, result.visited_indices,
        filtered_breakdowns, schedule,
    )
    gmaps_url = google_maps_url(req.start, end, filtered_wells, result.visited_indices)

    map_relative = Path(map_path).relative_to(OUTPUTS_DIR)
    map_view_url = f"{base_url}/outputs/{map_relative}"

    ensure_output_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    plan_path = OUTPUTS_DIR / "plans" / f"plan_{ts}.json"

    selected_ids = [filtered_wells[i].well_id for i in result.visited_indices]
    route_order = ["START"] + selected_ids + ["END"]

    totals = Totals(
        drive_minutes=round(total_drive, 1),
        service_minutes=round(total_service, 1),
        total_minutes=round(elapsed, 1),
        total_priority_score=round(total_priority, 2),
        wells_visited=len(result.visited_indices),
        wells_skipped=len(filtered_wells) - len(result.visited_indices),
    )

    rationale = _build_rationale(
        filtered_wells, filtered_breakdowns, result.visited_indices, totals, c.time_budget_minutes,
    )

    response = RoutePlanResponse(
        selected_well_ids=selected_ids,
        route_order=route_order,
        schedule=schedule,
        scoring_breakdown=filtered_breakdowns,
        totals=totals,
        rationale=rationale,
        artifacts=Artifacts(
            map_html_path=map_path,
            map_view_url=map_view_url,
            google_maps_url=gmaps_url,
            plan_json_path=str(plan_path),
        ),
    )

    plan_path.write_text(
        response.model_dump_json(indent=2),
        encoding="utf-8",
    )

    return response


def _why_selected(well, bd: ScoreBreakdown) -> str:
    """One-line deterministic reason why this well was picked."""
    parts: list[str] = []
    if bd.prod_score >= 70:
        parts.append(f"high production ({well.current_oil_bpd:.0f} bpd)")
    if bd.uplift_score >= 70:
        parts.append(f"significant uplift potential ({well.uplift_oil_bpd:.0f} bpd)")
    if bd.urgency_score >= 50:
        issues_str = ", ".join(well.issues[:2]) if well.issues else "operational flags"
        parts.append(f"urgent ({issues_str})")
    if bd.recency_score >= 70 and well.days_since_last_visit is not None:
        parts.append(f"overdue visit ({well.days_since_last_visit}d ago)")
    if not parts:
        parts.append(f"balanced priority (score {bd.priority_score:.1f})")
    return "; ".join(parts).capitalize()


def _build_rationale(
    wells: list,
    breakdowns: list[ScoreBreakdown],
    visited: list[int],
    totals: Totals,
    budget: int,
) -> str:
    """Deterministic explanation of route decisions."""
    visited_set = set(visited)
    selected = [(wells[i].name, breakdowns[i].priority_score) for i in visited]
    skipped = [
        (wells[i].name, breakdowns[i].priority_score)
        for i in range(len(wells))
        if i not in visited_set
    ]

    parts: list[str] = []
    parts.append(
        f"Selected {len(selected)} of {len(wells)} wells "
        f"with total priority score {totals.total_priority_score:.1f}."
    )

    if selected:
        top = sorted(selected, key=lambda x: -x[1])[:3]
        names = ", ".join(f"{n} ({s:.1f})" for n, s in top)
        parts.append(f"Highest priority stops: {names}.")

    parts.append(
        f"Route uses {totals.total_minutes:.0f} of {budget} min budget "
        f"({totals.drive_minutes:.0f} driving, {totals.service_minutes:.0f} service)."
    )

    if skipped:
        low = sorted(skipped, key=lambda x: x[1])[:3]
        names = ", ".join(f"{n} ({s:.1f})" for n, s in low)
        parts.append(f"Skipped (lower priority or long detour): {names}.")

    return " ".join(parts)
