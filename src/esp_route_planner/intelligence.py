"""Data & Intelligence Agent — queries DuckDB and returns structured analytics.

This agent is responsible for all analytical questions about ESP wells:
reliability summaries, common issues, high-priority ranking, workover lists,
location breakdowns, and identifying not-observed categories.

It computes priority_score using the deterministic scoring formula and
produces a CandidateWellSet for the Routing Agent.
"""

from __future__ import annotations

import duckdb

from .database import DB_PATH, ACTIONS, get_connection
from .intelligence_schemas import (
    CandidateWell,
    CandidateWellSet,
    CommonIssuesResponse,
    HighPriorityResponse,
    HighPriorityWell,
    HighProducingResponse,
    IssueCategoryCount,
    LocationGroup,
    LocationSummaryResponse,
    NotObservedResponse,
    ReliabilitySummaryResponse,
    ScoreBreakdownItem,
    WellSummary,
    WorkoverResponse,
    WorkoverWell,
)
from .scoring import score_well
from .schemas import Well


class DataIntelligenceAgent:
    """Answers analytical questions by querying the DuckDB mock database."""

    def __init__(self) -> None:
        self._check_db()

    def _check_db(self) -> None:
        if not DB_PATH.exists():
            raise RuntimeError(
                "Database not seeded. Call POST /admin/seed first."
            )

    def _con(self) -> duckdb.DuckDBPyConnection:
        return get_connection()

    # ── A) Reliability Summary ───────────────────────────────────────────

    def reliability_summary(self) -> ReliabilitySummaryResponse:
        con = self._con()
        total = con.execute("SELECT COUNT(*) FROM wells WHERE is_esp = TRUE").fetchone()[0]
        issues = con.execute("""
            SELECT COUNT(DISTINCT s.well_id) FROM esp_signals s
            WHERE s.restarts_7d > 0 OR s.vsd_trips_7d > 0
               OR s.motor_temp_high = TRUE OR s.intake_pressure_unstable = TRUE
               OR s.downhole_gauge_flatlined = TRUE
        """).fetchone()[0]
        con.close()

        pct = round(issues / total * 100, 1) if total > 0 else 0
        return ReliabilitySummaryResponse(
            total_esp=total,
            reliability_issue_count=issues,
            pct_impacted=pct,
            methodology=(
                f"I've identified {issues} ESP wells with reliability issues "
                f"based on SCADA, equipment, and production data. "
                f"This represents {pct}% of the {total} ESP wells in the database."
            ),
        )

    # ── B) Common Issues ─────────────────────────────────────────────────

    def common_issues(self) -> CommonIssuesResponse:
        con = self._con()

        restart_vsd = con.execute(
            "SELECT COUNT(*) FROM esp_signals WHERE restarts_7d > 0 OR vsd_trips_7d > 0"
        ).fetchone()[0]
        gauge_flat = con.execute(
            "SELECT COUNT(*) FROM esp_signals WHERE downhole_gauge_flatlined = TRUE"
        ).fetchone()[0]
        motor_temp = con.execute(
            "SELECT COUNT(*) FROM esp_signals WHERE motor_temp_high = TRUE"
        ).fetchone()[0]
        intake_pres = con.execute(
            "SELECT COUNT(*) FROM esp_signals WHERE intake_pressure_unstable = TRUE"
        ).fetchone()[0]

        # From ops_recommendations
        cats = con.execute("""
            SELECT issue_category, COUNT(*) as cnt
            FROM ops_recommendations
            WHERE issue_category IS NOT NULL AND issue_category != ''
            GROUP BY issue_category
            ORDER BY cnt DESC
        """).fetchall()
        con.close()

        counts = [
            IssueCategoryCount(
                category="restart_vsd_trip",
                count=restart_vsd,
                description="ESP restarts or VSD trips in last 7 days",
            ),
            IssueCategoryCount(
                category="gauge_flatlined",
                count=gauge_flat,
                description="Downhole gauge flatlined / not reading",
            ),
            IssueCategoryCount(
                category="motor_temp_high",
                count=motor_temp,
                description="Motor temperature above threshold",
            ),
            IssueCategoryCount(
                category="intake_pressure_unstable",
                count=intake_pres,
                description="Intake pressure showing instability",
            ),
        ]
        # Add ops categories
        ops_cats_added = {"restart_vsd_trip", "gauge_flatlined", "motor_temp_high", "intake_pressure_unstable"}
        for cat, cnt in cats:
            if cat not in ops_cats_added:
                counts.append(IssueCategoryCount(
                    category=cat,
                    count=cnt,
                    description=ACTIONS.get(cat, cat),
                ))

        counts.sort(key=lambda x: -x.count)
        top = counts[0].category if counts else "none"

        return CommonIssuesResponse(
            issue_counts=counts,
            top_issue_category=top,
            summary=(
                f"Most common issue: {restart_vsd} wells with restarts/VSD trips. "
                f"{gauge_flat} wells have flatlined gauges. "
                f"{motor_temp} wells show high motor temperature."
            ),
        )

    # ── C) High Priority Wells ───────────────────────────────────────────

    def high_priority(self, top_n: int = 10) -> HighPriorityResponse:
        candidates = self._load_all_scored()
        candidates.sort(key=lambda x: -x["priority_score"])
        top = candidates[:top_n]

        wells = []
        for c in top:
            wells.append(HighPriorityWell(
                well_id=c["well_id"],
                name=c["name"],
                oil_bpd=c["oil_bpd"],
                priority_score=c["priority_score"],
                scoring_breakdown=c["breakdown"],
                issues=c["issues"],
                action_required=c["action_required"],
                corridor=c["corridor"],
                field=c["field"],
            ))

        names = ", ".join(w.name for w in wells[:3])
        return HighPriorityResponse(
            wells=wells,
            summary=f"Top {len(wells)} priority wells. Highest: {names}.",
        )

    # ── D) High Producing Impacted ───────────────────────────────────────

    def high_producing_impacted(self, oil_threshold_bpd: float = 300) -> HighProducingResponse:
        con = self._con()
        rows = con.execute("""
            SELECT w.well_id, w.name, p.oil_bpd, w.corridor, w.field, w.county,
                   o.issue_category, o.action_required
            FROM wells w
            JOIN production_daily p ON w.well_id = p.well_id
            JOIN esp_signals s ON w.well_id = s.well_id
            JOIN ops_recommendations o ON w.well_id = o.well_id
            WHERE p.oil_bpd >= ?
              AND (s.restarts_7d > 0 OR s.vsd_trips_7d > 0
                   OR s.motor_temp_high = TRUE OR s.intake_pressure_unstable = TRUE
                   OR s.downhole_gauge_flatlined = TRUE)
            ORDER BY p.oil_bpd DESC
        """, [oil_threshold_bpd]).fetchall()
        con.close()

        scored = {c["well_id"]: c for c in self._load_all_scored()}
        wells = []
        for r in rows:
            wid = r[0]
            sc = scored.get(wid)
            issues = [r[6]] if r[6] else []
            wells.append(WellSummary(
                well_id=wid,
                name=r[1],
                oil_bpd=r[2],
                priority_score=sc["priority_score"] if sc else 0,
                issues=issues,
                action_required=r[7] or "",
                corridor=r[3],
                field=r[4],
                county=r[5],
            ))

        return HighProducingResponse(
            threshold_bpd=oil_threshold_bpd,
            count=len(wells),
            wells=wells[:20],
            summary=(
                f"{len(wells)} high-producing wells (≥{oil_threshold_bpd} bpd) "
                f"have reliability issues. These represent significant production at risk."
            ),
        )

    # ── E) Workover ──────────────────────────────────────────────────────

    def workover(self) -> WorkoverResponse:
        con = self._con()
        imm = con.execute("""
            SELECT w.well_id, w.name, p.oil_bpd, o.issue_category, o.action_required,
                   o.workover_horizon, w.corridor
            FROM wells w
            JOIN production_daily p ON w.well_id = p.well_id
            JOIN ops_recommendations o ON w.well_id = o.well_id
            WHERE o.workover_horizon = 'IMMEDIATE'
            ORDER BY p.oil_bpd DESC
        """).fetchall()

        months = con.execute("""
            SELECT w.well_id, w.name, p.oil_bpd, o.issue_category, o.action_required,
                   o.workover_horizon, w.corridor
            FROM wells w
            JOIN production_daily p ON w.well_id = p.well_id
            JOIN ops_recommendations o ON w.well_id = o.well_id
            WHERE o.workover_horizon = '3_6_MONTHS'
            ORDER BY p.oil_bpd DESC
        """).fetchall()
        con.close()

        def _to_wo(row) -> WorkoverWell:
            return WorkoverWell(
                well_id=row[0], name=row[1], oil_bpd=row[2],
                issues=[row[3]] if row[3] else [],
                action_required=row[4] or "",
                workover_horizon=row[5], corridor=row[6],
            )

        return WorkoverResponse(
            immediate=[_to_wo(r) for r in imm],
            three_to_six_months=[_to_wo(r) for r in months],
            summary=(
                f"{len(imm)} wells need immediate workover/pump replacement. "
                f"{len(months)} wells are candidates for 3–6 month workover planning."
            ),
        )

    # ── F) Location Summary ──────────────────────────────────────────────

    def location_summary(self, group_by: str = "corridor") -> LocationSummaryResponse:
        col = {"corridor": "w.corridor", "county": "w.county", "field": "w.field"}[group_by]
        con = self._con()
        rows = con.execute(f"""
            SELECT {col} as grp, COUNT(*) as cnt,
                   LIST(w.name ORDER BY w.name LIMIT 3) as examples
            FROM wells w
            JOIN esp_signals s ON w.well_id = s.well_id
            WHERE s.restarts_7d > 0 OR s.vsd_trips_7d > 0
               OR s.motor_temp_high = TRUE OR s.intake_pressure_unstable = TRUE
               OR s.downhole_gauge_flatlined = TRUE
            GROUP BY grp
            ORDER BY cnt DESC
        """).fetchall()
        con.close()

        groups = [
            LocationGroup(group_key=r[0], count=r[1], example_wells=r[2])
            for r in rows
        ]

        return LocationSummaryResponse(
            group_by=group_by,
            groups=groups,
            summary=f"Reliability wells grouped by {group_by}. {len(groups)} groups found.",
        )

    # ── G) Not Observed ──────────────────────────────────────────────────

    def not_observed(self) -> NotObservedResponse:
        con = self._con()
        spiky = con.execute("SELECT COUNT(*) FROM esp_signals WHERE motor_current_spiky = TRUE").fetchone()[0]
        amps = con.execute("SELECT COUNT(*) FROM esp_signals WHERE amps_rising_flat_prod = TRUE").fetchone()[0]
        vib = con.execute("SELECT COUNT(*) FROM esp_signals WHERE vibration_high = TRUE").fetchone()[0]
        con.close()

        not_obs = []
        if spiky == 0:
            not_obs.append("motor_current_spiky")
        if amps == 0:
            not_obs.append("amps_rising_flat_prod")
        if vib == 0:
            not_obs.append("vibration_high")

        return NotObservedResponse(
            not_observed_categories=not_obs,
            summary=(
                f"{len(not_obs)} issue categories have zero observations: "
                f"{', '.join(not_obs)}. "
                "This may indicate these sensors are not deployed or thresholds not yet triggered."
            ),
        )

    # ── Build CandidateWellSet for Routing Agent ─────────────────────────

    def build_candidate_set(
        self,
        top_n: int | None = None,
        min_priority: float = 0,
        oil_threshold: float = 0,
        reliability_only: bool = False,
    ) -> CandidateWellSet:
        """Score all wells and return a filtered candidate set for routing."""
        all_scored = self._load_all_scored()
        total = len(all_scored)

        # Filter
        filtered = all_scored
        if reliability_only:
            reliability_ids = self._get_reliability_ids()
            filtered = [c for c in filtered if c["well_id"] in reliability_ids]
        if oil_threshold > 0:
            filtered = [c for c in filtered if c["oil_bpd"] >= oil_threshold]
        if min_priority > 0:
            filtered = [c for c in filtered if c["priority_score"] >= min_priority]

        filtered.sort(key=lambda x: -x["priority_score"])
        if top_n:
            filtered = filtered[:top_n]

        candidates = [
            CandidateWell(
                well_id=c["well_id"],
                name=c["name"],
                lat=c["lat"],
                lon=c["lon"],
                oil_bpd=c["oil_bpd"],
                uplift_oil_bpd=c["uplift_oil_bpd"],
                water_cut_pct=c["water_cut_pct"],
                priority_score=c["priority_score"],
                scoring_breakdown=c["breakdown"],
                issues=c["issues"],
                action_required=c["action_required"],
                service_minutes=c["service_minutes"],
                confidence=c["confidence"],
                days_since_last_visit=c["days_since_last_visit"],
                asset="Permian Basin",
                corridor=c["corridor"],
                ctb=c["ctb"],
                pad_name=c["pad_name"],
            )
            for c in filtered
        ]

        return CandidateWellSet(
            wells=candidates,
            total_scored=total,
            filtered_count=len(candidates),
            methodology=(
                f"Scored {total} ESP wells using priority formula "
                f"(prod×0.30 + uplift×0.25 + urgency×0.25 + confidence×0.10 + recency×0.10). "
                f"Filtered to {len(candidates)} candidates."
            ),
        )

    # ── Internal helpers ─────────────────────────────────────────────────

    def _load_all_scored(self) -> list[dict]:
        """Load all wells from DB, score them, return enriched dicts."""
        con = self._con()
        rows = con.execute("""
            SELECT w.well_id, w.name, w.lat, w.lon, w.corridor, w.field, w.county,
                   w.ctb, w.pad_name, w.service_minutes,
                   w.days_since_last_visit, w.days_since_last_well_test,
                   w.freq_current_hz, w.freq_optimal_hz,
                   p.oil_bpd, p.liquid_bpd, p.water_cut_pct, p.uplift_oil_bpd,
                   s.confidence,
                   o.issue_category, o.action_required
            FROM wells w
            JOIN production_daily p ON w.well_id = p.well_id
            JOIN esp_signals s ON w.well_id = s.well_id
            JOIN ops_recommendations o ON w.well_id = o.well_id
            WHERE w.is_esp = TRUE
        """).fetchall()
        con.close()

        results = []
        for r in rows:
            issues = [r[19]] if r[19] else []
            well = Well(
                well_id=r[0], name=r[1], lat=r[2], lon=r[3],
                current_oil_bpd=r[14], current_liquid_bpd=r[15],
                water_cut_pct=r[16], uplift_oil_bpd=r[17],
                issues=issues,
                action_required=r[20] or "",
                freq_current_hz=r[12], freq_optimal_hz=r[13],
                service_minutes=r[9] or 35,
                confidence=r[18],
                days_since_last_visit=r[10],
                days_since_last_well_test=r[11],
                asset="Permian Basin", corridor=r[4], ctb=r[7], pad_name=r[8],
            )
            bd = score_well(well)
            results.append({
                "well_id": r[0], "name": r[1], "lat": r[2], "lon": r[3],
                "corridor": r[4], "field": r[5], "county": r[6],
                "ctb": r[7], "pad_name": r[8],
                "service_minutes": r[9] or 35,
                "days_since_last_visit": r[10],
                "oil_bpd": r[14], "uplift_oil_bpd": r[17],
                "water_cut_pct": r[16], "confidence": r[18],
                "issues": issues,
                "action_required": r[20] or "",
                "priority_score": bd.priority_score,
                "breakdown": ScoreBreakdownItem(
                    well_id=bd.well_id,
                    prod_score=bd.prod_score,
                    uplift_score=bd.uplift_score,
                    urgency_score=bd.urgency_score,
                    confidence_score=bd.confidence_score,
                    recency_score=bd.recency_score,
                    priority_score=bd.priority_score,
                ),
            })
        return results

    def _get_reliability_ids(self) -> set[str]:
        con = self._con()
        rows = con.execute("""
            SELECT DISTINCT well_id FROM esp_signals
            WHERE restarts_7d > 0 OR vsd_trips_7d > 0
               OR motor_temp_high = TRUE OR intake_pressure_unstable = TRUE
               OR downhole_gauge_flatlined = TRUE
        """).fetchall()
        con.close()
        return {r[0] for r in rows}
