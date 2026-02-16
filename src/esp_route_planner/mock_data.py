"""Generate realistic Chevron-style mock ESP well datasets."""

from __future__ import annotations

import json
import math
import random
import uuid

from .schemas import MockGenerateRequest, MockGenerateResponse, Well
from .utils import OUTPUTS_DIR, ensure_output_dirs

# ── Chevron-style naming pools ───────────────────────────────────────────

CORRIDORS = [
    ("CMC", "Central Midland Corridor"),
    ("VLT", "Violet"),
    ("NMC", "North Midland Corridor"),
    ("SMC", "South Midland Corridor"),
]

WELL_NAMES = [
    "RANGER", "VULCAN", "SNAPDRAGON", "MUSTANG", "FALCON",
    "PRONGHORN", "MAVERICK", "THUNDERHAWK", "SIDEWINDER", "COPPERHEAD",
    "DIAMONDBACK", "HORNET", "STALLION", "BRONCO", "VIPER",
    "COYOTE", "RAPTOR", "PEREGRINE", "OCELOT", "CONDOR",
]

CTBS = [
    "CTB 6", "CTB 7", "CTB 12", "CTB 14", "CTB 17", "CTB 21", "CTB 23",
]

PAD_SUFFIXES = ["A", "B", "C", "D", "E"]

ISSUES_POOL = [
    "well_test_quality_issue",
    "model_calibration_issue",
    "frequency_suboptimal",
    "high_water_cut_trend",
    "sensor_drift_suspected",
    "pump_efficiency_drop",
]

ACTIONS = {
    "well_test_quality_issue": "Review last well test + validate meters",
    "model_calibration_issue": "Recalibrate model inputs; check fluid properties",
    "frequency_suboptimal": "Confirm VSD frequency change window",
    "high_water_cut_trend": "Investigate rising water cut; confirm separators",
    "sensor_drift_suspected": "Check downhole gauge / surface sensor drift",
    "pump_efficiency_drop": "Inspect pump stages; review vibration data",
}


def generate_mock_wells(req: MockGenerateRequest) -> MockGenerateResponse:
    """Create *n* random Chevron-style wells within a circular area."""
    rng = random.Random(req.seed)
    wells: list[Well] = []

    for i in range(req.n):
        # Uniform random point in circle (equirectangular approx)
        angle = rng.uniform(0, 2 * math.pi)
        r = req.radius_km * math.sqrt(rng.uniform(0, 1))
        dlat = (r * math.cos(angle)) / 111.0
        dlon = (r * math.sin(angle)) / (
            111.0 * math.cos(math.radians(req.center_lat))
        )

        # Chevron-style naming
        corridor_code, corridor_name = rng.choice(CORRIDORS)
        well_name_part = rng.choice(WELL_NAMES)
        well_num = rng.randint(100, 9999)
        well_suffix = rng.choice(["CL", "WA", "SL", "DL"])
        name = f"{corridor_code} {well_name_part} {well_num:04d}{well_suffix}"
        well_id = f"{corridor_code}-{well_num:04d}{well_suffix}"

        ctb = rng.choice(CTBS)
        pad_idx = rng.choice(PAD_SUFFIXES)
        pad_name = f"{corridor_code} {well_num // 100} Pad {pad_idx}"

        oil = round(rng.uniform(80, 600), 1)
        wc = round(rng.uniform(10, 90), 1)
        liquid = round(oil / max(1 - wc / 100, 0.05), 1)
        uplift = round(rng.uniform(5, 40), 1)
        conf = round(rng.uniform(0.4, 0.95), 2)

        # Frequency data — present ~60% of wells
        freq_cur: float | None = None
        freq_opt: float | None = None
        if rng.random() < 0.6:
            freq_cur = round(rng.uniform(40, 60), 1)
            freq_opt = round(freq_cur + rng.uniform(-3, 5), 1)

        # Issues and action
        n_issues = rng.randint(0, 3)
        issues = rng.sample(ISSUES_POOL, k=min(n_issues, len(ISSUES_POOL)))
        if issues:
            action = ACTIONS[issues[0]]
        else:
            action = ""

        # Recency
        days_visit = rng.choice([None, rng.randint(1, 60)])
        days_test = rng.choice([None, rng.randint(1, 90)])

        wells.append(
            Well(
                well_id=well_id,
                name=name,
                lat=round(req.center_lat + dlat, 6),
                lon=round(req.center_lon + dlon, 6),
                current_oil_bpd=oil,
                current_liquid_bpd=liquid,
                water_cut_pct=wc,
                uplift_oil_bpd=uplift,
                issues=issues,
                action_required=action,
                freq_current_hz=freq_cur,
                freq_optimal_hz=freq_opt,
                service_minutes=rng.choice([25, 30, 35, 40, 45]),
                confidence=conf,
                days_since_last_visit=days_visit,
                days_since_last_well_test=days_test,
                asset="Permian Basin",
                corridor=corridor_name,
                ctb=ctb,
                pad_name=pad_name,
            )
        )

    dataset_id = uuid.uuid4().hex[:12]
    ensure_output_dirs()
    path = OUTPUTS_DIR / "mock" / f"{dataset_id}.json"
    path.write_text(
        json.dumps([w.model_dump() for w in wells], indent=2),
        encoding="utf-8",
    )

    return MockGenerateResponse(
        dataset_id=dataset_id,
        saved_path=str(path),
        wells_preview=wells[:5],
    )
