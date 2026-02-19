"""Routing Agent — consumes CandidateWellSet and produces an optimized route.

This agent does NOT compute priority scores. It only uses scores provided
by the Data & Intelligence Agent to maximize total priority captured.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .intelligence_schemas import CandidateWell, CandidateWellSet
from .optimizer import solve_route
from .schemas import (
    Artifacts,
    Location,
    RoutePlanResponse,
    ScoreBreakdown,
    ScheduleStop,
    Totals,
)
from .travel_time import build_time_matrix
from .utils import OUTPUTS_DIR, ensure_output_dirs, haversine_km, minutes_from_start
from .visualize import build_map, google_maps_url


class RoutingAgent:
    """Optimizes visit order for a set of pre-scored candidate wells."""

    def plan(
        self,
        candidates: CandidateWellSet,
        start: Location,
        end: Location | None = None,
        time_budget_minutes: int = 480,
        max_stops: int = 8,
        must_visit_ids: list[str] | None = None,
        base_url: str = "http://127.0.0.1:8000",
    ) -> RoutePlanResponse | None:
        """Build optimized route from pre-scored candidates.

        Returns None if no feasible route exists.
        """
        end = end or start
        must_visit_ids = must_visit_ids or []
        wells = candidates.wells

        if not wells:
            return None

        # Convert CandidateWells to schema Wells for visualizer compatibility
        from .schemas import Well as SchemaWell

        schema_wells = [
            SchemaWell(
                well_id=cw.well_id, name=cw.name, lat=cw.lat, lon=cw.lon,
                current_oil_bpd=cw.oil_bpd, current_liquid_bpd=cw.oil_bpd * 2,
                water_cut_pct=cw.water_cut_pct, uplift_oil_bpd=cw.uplift_oil_bpd,
                issues=cw.issues, action_required=cw.action_required,
                service_minutes=cw.service_minutes, confidence=cw.confidence,
                days_since_last_visit=cw.days_since_last_visit,
                asset=cw.asset, corridor=cw.corridor, ctb=cw.ctb, pad_name=cw.pad_name,
            )
            for cw in wells
        ]

        # Priority scores come from intelligence agent — do NOT recompute
        priority_scores = [cw.priority_score for cw in wells]
        breakdowns = [
            ScoreBreakdown(
                well_id=cw.scoring_breakdown.well_id,
                prod_score=cw.scoring_breakdown.prod_score,
                uplift_score=cw.scoring_breakdown.uplift_score,
                urgency_score=cw.scoring_breakdown.urgency_score,
                confidence_score=cw.scoring_breakdown.confidence_score,
                recency_score=cw.scoring_breakdown.recency_score,
                priority_score=cw.scoring_breakdown.priority_score,
            )
            for cw in wells
        ]

        must_indices = [
            i for i, cw in enumerate(wells)
            if cw.well_id in set(must_visit_ids)
        ]

        # Build travel time matrix
        time_matrix = build_time_matrix(
            start, end, schema_wells, avg_speed_kmph=45,
        )

        # Solve
        result = solve_route(
            time_matrix=time_matrix,
            priority_scores=priority_scores,
            service_times=[cw.service_minutes for cw in wells],
            must_visit_indices=must_indices,
            max_stops=max_stops,
            time_budget_minutes=time_budget_minutes,
        )

        if result.status == "NO_SOLUTION":
            return None

        # Build schedule
        schedule: list[ScheduleStop] = []
        elapsed = 0.0
        prev_lat, prev_lon = start.lat, start.lon
        stop_num = 0

        schedule.append(ScheduleStop(
            stop_id="START", name=start.name, stop_number=0,
            eta=minutes_from_start(0), depart=minutes_from_start(0),
            drive_minutes=0, service_minutes=0, priority_score=0,
        ))

        total_service = 0.0
        total_drive = 0.0

        for well_idx in result.visited_indices:
            cw = wells[well_idx]
            stop_num += 1

            d_km = haversine_km(prev_lat, prev_lon, cw.lat, cw.lon)
            drive_min = round(d_km / 45 * 60, 2)

            elapsed += drive_min
            eta = elapsed
            elapsed += cw.service_minutes
            depart = elapsed
            total_drive += drive_min
            total_service += cw.service_minutes

            bd = breakdowns[well_idx]
            why = _why_selected(cw, bd)

            schedule.append(ScheduleStop(
                stop_id=cw.well_id, name=cw.name, stop_number=stop_num,
                eta=minutes_from_start(eta), depart=minutes_from_start(depart),
                drive_minutes=round(drive_min, 1), service_minutes=cw.service_minutes,
                priority_score=bd.priority_score,
                action_required=cw.action_required, why_selected=why,
            ))
            prev_lat, prev_lon = cw.lat, cw.lon

        # Return to end
        d_end = haversine_km(prev_lat, prev_lon, end.lat, end.lon)
        drive_end = round(d_end / 45 * 60, 2)
        elapsed += drive_end
        total_drive += drive_end

        schedule.append(ScheduleStop(
            stop_id="END", name=end.name, stop_number=stop_num + 1,
            eta=minutes_from_start(elapsed), depart=minutes_from_start(elapsed),
            drive_minutes=round(drive_end, 1), service_minutes=0, priority_score=0,
        ))

        total_priority = sum(priority_scores[i] for i in result.visited_indices)

        # Generate map
        map_path = build_map(
            start, end, schema_wells, result.visited_indices, breakdowns, schedule,
        )
        gmaps_url = google_maps_url(start, end, schema_wells, result.visited_indices)

        map_relative = Path(map_path).relative_to(OUTPUTS_DIR)
        map_view_url = f"{base_url}/outputs/{map_relative}"

        ensure_output_dirs()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        plan_path = OUTPUTS_DIR / "plans" / f"plan_{ts}.json"

        selected_ids = [wells[i].well_id for i in result.visited_indices]
        route_order = ["START"] + selected_ids + ["END"]

        totals = Totals(
            drive_minutes=round(total_drive, 1),
            service_minutes=round(total_service, 1),
            total_minutes=round(elapsed, 1),
            total_priority_score=round(total_priority, 2),
            wells_visited=len(result.visited_indices),
            wells_skipped=len(wells) - len(result.visited_indices),
        )

        rationale = _build_rationale(wells, breakdowns, result.visited_indices, totals, time_budget_minutes)

        response = RoutePlanResponse(
            selected_well_ids=selected_ids,
            route_order=route_order,
            schedule=schedule,
            scoring_breakdown=breakdowns,
            totals=totals,
            rationale=rationale,
            artifacts=Artifacts(
                map_html_path=map_path,
                map_view_url=map_view_url,
                google_maps_url=gmaps_url,
                plan_json_path=str(plan_path),
            ),
        )

        plan_path.write_text(response.model_dump_json(indent=2), encoding="utf-8")
        return response


def _why_selected(cw: CandidateWell, bd: ScoreBreakdown) -> str:
    parts: list[str] = []
    if bd.prod_score >= 70:
        parts.append(f"high production ({cw.oil_bpd:.0f} bpd)")
    if bd.uplift_score >= 70:
        parts.append(f"significant uplift potential ({cw.uplift_oil_bpd:.0f} bpd)")
    if bd.urgency_score >= 50:
        issues_str = ", ".join(cw.issues[:2]) if cw.issues else "operational flags"
        parts.append(f"urgent ({issues_str})")
    if bd.recency_score >= 70 and cw.days_since_last_visit is not None:
        parts.append(f"overdue visit ({cw.days_since_last_visit}d ago)")
    if not parts:
        parts.append(f"balanced priority (score {bd.priority_score:.1f})")
    return "; ".join(parts).capitalize()


def _build_rationale(
    wells: list[CandidateWell],
    breakdowns: list[ScoreBreakdown],
    visited: list[int],
    totals: Totals,
    budget: int,
) -> str:
    visited_set = set(visited)
    selected = [(wells[i].name, breakdowns[i].priority_score) for i in visited]
    skipped = [
        (wells[i].name, breakdowns[i].priority_score)
        for i in range(len(wells)) if i not in visited_set
    ]

    parts = [
        f"Selected {len(selected)} of {len(wells)} candidate wells "
        f"with total priority score {totals.total_priority_score:.1f}."
    ]
    if selected:
        top = sorted(selected, key=lambda x: -x[1])[:3]
        parts.append("Highest priority stops: " + ", ".join(f"{n} ({s:.1f})" for n, s in top) + ".")
    parts.append(
        f"Route uses {totals.total_minutes:.0f} of {budget} min budget "
        f"({totals.drive_minutes:.0f} driving, {totals.service_minutes:.0f} service)."
    )
    if skipped:
        low = sorted(skipped, key=lambda x: x[1])[:3]
        parts.append("Skipped (lower priority or long detour): " + ", ".join(f"{n} ({s:.1f})" for n, s in low) + ".")
    return " ".join(parts)
