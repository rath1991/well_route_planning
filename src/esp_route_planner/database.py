"""DuckDB database — ~20 Delaware Basin ESP wells with deterministic seeding.

Tables
------
- wells              : static well metadata + coordinates
- production_latest  : current production snapshot
- reliability_flags_latest : sensor / operational flags
- ops_recommendations_latest : issue categories + actions

View
----
- well_priority_vw   : deterministic priority scoring (0-100)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .utils import OUTPUTS_DIR, ensure_output_dirs

DB_PATH = OUTPUTS_DIR / "db" / "esp_delaware.duckdb"

# ── Hero wells (always present, deterministic) ────────────────────────────
# Format: (well_id, name, pad_name, field, county, state, lat, lon,
#          oil_bpd, liquid_bpd, water_cut, uplift, days_test, days_visit,
#          restarts_7d, vsd_trips_7d, motor_temp_high, intake_unstable,
#          gauge_flat, confidence,
#          issue_cat, severity, action, workover_horizon, avg_repair_hours)

_HERO_WELLS = [
    (
        "FRONTIER-1212WA", "FRONTIER REDSTONE 1212WA", "FRONTIER 12 Pad A",
        "Red Hills", "Reeves", "TX", 31.88, -103.22,
        576, 1920, 70.0, 45, 60, 38,
        7, 3, True, True, False, 0.91,
        "restarts_vsd_trips", "HIGH",
        "Immediate: motor temp + intake pressure unstable; schedule workover",
        "IMMEDIATE", 8.5,
    ),
    (
        "APEX-4844CL", "APEX RANGER 4844CL", "APEX 48 Pad A",
        "Ranger Draw", "Reeves", "TX", 31.94, -103.15,
        420, 1050, 60.0, 28, 45, 21,
        2, 1, False, False, False, 0.85,
        "restarts_vsd_trips", "MEDIUM",
        "Confirm VSD frequency change window; review restart log",
        "NONE", 3.5,
    ),
    (
        "SUMMIT-0451WA", "SUMMIT VULCAN 0451WA", "SUMMIT 4 Pad B",
        "Vulcan Flat", "Loving", "TX", 31.99, -103.05,
        310, 885, 65.0, 35, 60, 35,
        0, 0, False, False, True, 0.72,
        "gauge_flatlined", "MEDIUM",
        "Replace or recalibrate downhole gauge; check fluid properties",
        "3_6_MONTHS", 4.0,
    ),
    (
        "BASIN-0325WA", "BASIN CANYON 0325WA", "BASIN 3 Pad C",
        "Ranger Draw", "Reeves", "TX", 31.82, -103.28,
        550, 2200, 75.0, 15, 30, 14,
        0, 0, False, False, False, 0.68,
        "pump_efficiency_drop", "MEDIUM",
        "Investigate rising water cut; confirm separators",
        "3_6_MONTHS", 3.0,
    ),
    (
        "PLAINS-1001WA", "PLAINS MESA 1001WA", "PLAINS 10 Pad A",
        "Mesa Verde", "Ward", "TX", 31.75, -103.38,
        380, 1140, 66.7, 20, 40, 28,
        4, 2, False, False, False, 0.80,
        "restarts_vsd_trips", "HIGH",
        "Investigate frequent restarts; check cable + VSD",
        "IMMEDIATE", 7.0,
    ),
]

# ── Pools for generated wells ─────────────────────────────────────────────

_PREFIXES = ["APEX", "FRONTIER", "SUMMIT", "BASIN", "PLAINS", "VECTOR", "HORIZON", "TRINITY"]
_NAMES = [
    "HAWK", "COYOTE", "MUSTANG", "FALCON", "PRONGHORN", "EAGLE",
    "VIPER", "TALON", "MESA", "CANYON", "BLUFF", "RIDGE", "CREEK",
    "BUTTE", "DUNE", "DRAW", "FLATS", "BEND", "HOLLOW",
]
_FIELDS = ["Red Hills", "Ranger Draw", "Vulcan Flat", "Mesa Verde", "Pecos Bend", "Ward Flats"]
_COUNTIES = [
    ("Reeves", "TX"), ("Loving", "TX"), ("Ward", "TX"),
    ("Eddy", "NM"), ("Lea", "NM"),
]
_ISSUE_CATS_HIGH = ["restarts_vsd_trips", "pump_efficiency_drop"]
_ISSUE_CATS_MEDIUM = ["gauge_flatlined", "freq_suboptimal", "motor_current_spiky", "vibration_high"]
_ISSUE_CATS_LOW = ["amps_rising_flat_prod", "freq_suboptimal", "vibration_high"]
_ACTIONS = {
    "restarts_vsd_trips":   "Investigate frequent restarts; check cable + VSD",
    "gauge_flatlined":      "Replace or recalibrate downhole gauge",
    "freq_suboptimal":      "Review VSD frequency settings; schedule adjustment",
    "pump_efficiency_drop": "Inspect pump stages; review vibration data",
    "motor_current_spiky":  "Monitor motor current trends; check for scaling or wear",
    "vibration_high":       "Inspect tubing and ESP centralizers; check for imbalance",
    "amps_rising_flat_prod":"Review amp trend vs production; inspect pump intake",
}


def _generate_wells(seed: int, n_extra: int = 15) -> list[tuple]:
    """Generate additional synthetic wells beyond heroes."""
    rng = random.Random(seed)
    wells = []
    used_ids: set[str] = {h[0] for h in _HERO_WELLS}

    for _ in range(n_extra):
        prefix = rng.choice(_PREFIXES)
        name_part = rng.choice(_NAMES)
        num = rng.randint(100, 9999)
        suffix = rng.choice(["WA", "CL", "DL", "SL"])
        wid = f"{prefix}-{num:04d}{suffix}"
        while wid in used_ids:
            num = rng.randint(100, 9999)
            wid = f"{prefix}-{num:04d}{suffix}"
        used_ids.add(wid)

        wname = f"{prefix} {name_part} {num:04d}{suffix}"
        pad = f"{prefix} {num // 100} Pad {rng.choice('ABCDE')}"
        field = rng.choice(_FIELDS)
        county, state = rng.choice(_COUNTIES)

        # Cluster around Delaware Basin center ± 0.3 deg
        lat = round(31.7 + rng.random() * 0.5, 4)
        lon = round(-103.5 + rng.random() * 0.6, 4)

        oil = round(rng.uniform(80, 600), 1)
        wc = round(rng.uniform(40, 80), 1)
        liquid = round(oil / (1 - wc / 100), 1)
        uplift = round(rng.uniform(5, 50), 1)
        days_test = rng.randint(10, 90)
        days_visit = rng.randint(5, 60)

        # All ESP wells have an operational observation — severity varies
        severity = rng.choice(["HIGH", "HIGH", "MEDIUM", "MEDIUM", "MEDIUM", "LOW"])
        if severity == "HIGH":
            restarts = rng.choice([0, 3, 5, 7])
            vsd = rng.choice([0, 1, 2])
            motor_temp = rng.random() < 0.35
            intake_unstable = rng.random() < 0.30
            gauge_flat = rng.random() < 0.20
            conf = round(rng.uniform(0.75, 0.95), 2)
            issue_cat = rng.choice(_ISSUE_CATS_HIGH)
            workover = rng.choice(["NONE", "3_6_MONTHS", "IMMEDIATE"])
        elif severity == "MEDIUM":
            restarts = rng.choice([0, 0, 2, 3])
            vsd = rng.choice([0, 0, 1])
            motor_temp = rng.random() < 0.15
            intake_unstable = rng.random() < 0.15
            gauge_flat = rng.random() < 0.25
            conf = round(rng.uniform(0.60, 0.85), 2)
            issue_cat = rng.choice(_ISSUE_CATS_MEDIUM)
            workover = rng.choice(["NONE", "NONE", "3_6_MONTHS"])
        else:  # LOW
            restarts, vsd = 0, 0
            motor_temp, intake_unstable, gauge_flat = False, False, False
            conf = round(rng.uniform(0.70, 0.98), 2)
            issue_cat = rng.choice(_ISSUE_CATS_LOW)
            workover = "NONE"
        action = _ACTIONS[issue_cat]

        if severity == "HIGH":
            avg_repair_hours = round(rng.uniform(5.0, 10.0), 1)
        elif severity == "MEDIUM":
            avg_repair_hours = round(rng.uniform(2.0, 5.0), 1)
        else:  # LOW
            avg_repair_hours = round(rng.uniform(1.0, 3.0), 1)

        wells.append((
            wid, wname, pad, field, county, state, lat, lon,
            oil, liquid, wc, uplift, days_test, days_visit,
            restarts, vsd, motor_temp, intake_unstable, gauge_flat, conf,
            issue_cat, severity, action, workover, avg_repair_hours,
        ))

    return wells


# ── DDL ────────────────────────────────────────────────────────────────────

_DDL = """
DROP VIEW IF EXISTS well_priority_vw;
DROP TABLE IF EXISTS ops_recommendations_latest;
DROP TABLE IF EXISTS reliability_flags_latest;
DROP TABLE IF EXISTS production_latest;
DROP TABLE IF EXISTS wells;

CREATE TABLE wells (
    well_id       VARCHAR PRIMARY KEY,
    name          VARCHAR NOT NULL,
    asset         VARCHAR NOT NULL DEFAULT 'Delaware Basin',
    pad_name      VARCHAR,
    field         VARCHAR,
    county        VARCHAR,
    state         VARCHAR,
    basin         VARCHAR NOT NULL DEFAULT 'Delaware Basin',
    lat           DOUBLE NOT NULL,
    lon           DOUBLE NOT NULL,
    is_esp        BOOLEAN NOT NULL DEFAULT TRUE,
    avg_repair_hours DOUBLE
);

CREATE TABLE production_latest (
    well_id              VARCHAR PRIMARY KEY REFERENCES wells(well_id),
    oil_bpd              DOUBLE,
    liquid_bpd           DOUBLE,
    water_cut_pct        DOUBLE,
    uplift_oil_bpd       DOUBLE,
    days_since_last_test INTEGER,
    days_since_last_visit INTEGER
);

CREATE TABLE reliability_flags_latest (
    well_id                   VARCHAR PRIMARY KEY REFERENCES wells(well_id),
    restarts_7d               INTEGER DEFAULT 0,
    vsd_trips_7d              INTEGER DEFAULT 0,
    motor_temp_high           BOOLEAN DEFAULT FALSE,
    intake_pressure_unstable  BOOLEAN DEFAULT FALSE,
    downhole_gauge_flatlined  BOOLEAN DEFAULT FALSE,
    confidence                DOUBLE DEFAULT 0.8,
    motor_current_spiky       BOOLEAN DEFAULT FALSE,
    amps_rising_flat_prod     BOOLEAN DEFAULT FALSE,
    vibration_high            BOOLEAN DEFAULT FALSE
);

CREATE TABLE ops_recommendations_latest (
    well_id          VARCHAR PRIMARY KEY REFERENCES wells(well_id),
    issue_category   VARCHAR,
    severity         VARCHAR,
    action_required  VARCHAR,
    workover_horizon VARCHAR DEFAULT 'NONE'
);
"""

_VIEW_SQL = """
CREATE VIEW well_priority_vw AS
SELECT
    w.well_id,
    w.name,
    w.lat,
    w.lon,
    w.field,
    w.county,
    w.state,
    w.pad_name,
    w.is_esp,
    w.avg_repair_hours,
    p.oil_bpd,
    p.liquid_bpd,
    p.water_cut_pct,
    p.uplift_oil_bpd,
    p.days_since_last_test,
    p.days_since_last_visit,
    r.restarts_7d,
    r.vsd_trips_7d,
    r.motor_temp_high,
    r.intake_pressure_unstable,
    r.downhole_gauge_flatlined,
    r.confidence,
    o.issue_category,
    o.severity,
    o.action_required,
    o.workover_horizon,
    -- Component scores (each 0-100)
    LEAST(p.oil_bpd / 500.0 * 100.0, 100.0) AS prod_score,
    LEAST(p.uplift_oil_bpd / 30.0 * 100.0, 100.0) AS uplift_score,
    LEAST(
        (CASE WHEN o.issue_category IS NOT NULL THEN 30.0 ELSE 0.0 END)
      + (CASE WHEN r.restarts_7d > 2 THEN 20.0 ELSE 0.0 END)
      + (CASE WHEN r.motor_temp_high THEN 15.0 ELSE 0.0 END)
      + (CASE WHEN r.intake_pressure_unstable THEN 15.0 ELSE 0.0 END)
      + (CASE WHEN r.downhole_gauge_flatlined THEN 15.0 ELSE 0.0 END)
      + (CASE WHEN p.water_cut_pct > 70 THEN 20.0 ELSE 0.0 END),
      100.0
    ) AS urgency_score,
    r.confidence * 100.0 AS confidence_score,
    LEAST(COALESCE(p.days_since_last_visit, 15) / 30.0 * 100.0, 100.0) AS recency_score,
    -- Weighted priority score
    ROUND(
        0.30 * LEAST(p.oil_bpd / 500.0 * 100.0, 100.0)
      + 0.25 * LEAST(p.uplift_oil_bpd / 30.0 * 100.0, 100.0)
      + 0.25 * LEAST(
            (CASE WHEN o.issue_category IS NOT NULL THEN 30.0 ELSE 0.0 END)
          + (CASE WHEN r.restarts_7d > 2 THEN 20.0 ELSE 0.0 END)
          + (CASE WHEN r.motor_temp_high THEN 15.0 ELSE 0.0 END)
          + (CASE WHEN r.intake_pressure_unstable THEN 15.0 ELSE 0.0 END)
          + (CASE WHEN r.downhole_gauge_flatlined THEN 15.0 ELSE 0.0 END)
          + (CASE WHEN p.water_cut_pct > 70 THEN 20.0 ELSE 0.0 END),
          100.0
        )
      + 0.10 * (r.confidence * 100.0)
      + 0.10 * LEAST(COALESCE(p.days_since_last_visit, 15) / 30.0 * 100.0, 100.0),
      2
    ) AS priority_score
FROM wells w
JOIN production_latest p ON w.well_id = p.well_id
JOIN reliability_flags_latest r ON w.well_id = r.well_id
LEFT JOIN ops_recommendations_latest o ON w.well_id = o.well_id
WHERE w.is_esp = TRUE;
"""


# ── Public API ─────────────────────────────────────────────────────────────


@dataclass
class SeedResult:
    total_wells: int
    with_issues: int
    sample_wells: list[dict]
    valid: bool
    errors: list[str]


def get_connection() -> duckdb.DuckDBPyConnection:
    """Return a DuckDB connection to the shared database."""
    ensure_output_dirs()
    return duckdb.connect(str(DB_PATH))


def db_exists() -> bool:
    if not DB_PATH.exists():
        return False
    try:
        con = get_connection()
        tables = con.execute("SHOW TABLES").fetchall()
        con.close()
        return len(tables) >= 4
    except Exception:
        return False


def get_schema_text() -> str:
    """Return a concise schema description for LLM-to-SQL prompting."""
    return """Tables in DuckDB:

TABLE wells (well_id VARCHAR PK, name VARCHAR, asset VARCHAR, pad_name VARCHAR, field VARCHAR, county VARCHAR, state VARCHAR, basin VARCHAR, lat DOUBLE, lon DOUBLE, is_esp BOOLEAN, avg_repair_hours DOUBLE)
  -- avg_repair_hours: historical average hours to diagnose and fix issues at this well

TABLE production_latest (well_id VARCHAR PK FK->wells, oil_bpd DOUBLE, liquid_bpd DOUBLE, water_cut_pct DOUBLE, uplift_oil_bpd DOUBLE, days_since_last_test INTEGER, days_since_last_visit INTEGER)

TABLE reliability_flags_latest (well_id VARCHAR PK FK->wells, restarts_7d INTEGER, vsd_trips_7d INTEGER, motor_temp_high BOOLEAN, intake_pressure_unstable BOOLEAN, downhole_gauge_flatlined BOOLEAN, confidence DOUBLE, motor_current_spiky BOOLEAN, amps_rising_flat_prod BOOLEAN, vibration_high BOOLEAN)

TABLE ops_recommendations_latest (well_id VARCHAR PK FK->wells, issue_category VARCHAR, severity VARCHAR, action_required VARCHAR, workover_horizon VARCHAR)
  -- issue_category values: 'restarts_vsd_trips', 'gauge_flatlined', 'freq_suboptimal', 'pump_efficiency_drop', NULL (no issue)
  -- severity values: 'HIGH', 'MEDIUM', 'LOW', NULL
  -- workover_horizon values: 'IMMEDIATE', '3_6_MONTHS', 'NONE'

VIEW well_priority_vw (well_id, name, lat, lon, field, county, state, pad_name, is_esp, avg_repair_hours, oil_bpd, liquid_bpd, water_cut_pct, uplift_oil_bpd, days_since_last_test, days_since_last_visit, restarts_7d, vsd_trips_7d, motor_temp_high, intake_pressure_unstable, downhole_gauge_flatlined, confidence, issue_category, severity, action_required, workover_horizon, prod_score, uplift_score, urgency_score, confidence_score, recency_score, priority_score)
  -- priority_score = 0.30*prod + 0.25*uplift + 0.25*urgency + 0.10*confidence + 0.10*recency (each 0-100)
  -- All rows in this view have is_esp = TRUE (non-ESP wells are excluded)
  -- Use this view for most queries about well priority, scoring, and ranking."""


def refresh_view() -> None:
    """Drop and recreate well_priority_vw with the latest definition.

    Safe to call on every startup — only the view is touched, no data is modified.
    Ensures schema changes (e.g. adding is_esp to SELECT) take effect on existing DBs.
    """
    con = get_connection()
    try:
        con.execute("DROP VIEW IF EXISTS well_priority_vw")
        con.execute(_VIEW_SQL)
    finally:
        con.close()


def seed_database(seed: int = 42, force_recreate: bool = False) -> SeedResult:
    """Create and populate the DuckDB database with ~20 synthetic wells."""
    ensure_output_dirs()

    if DB_PATH.exists() and not force_recreate:
        con = get_connection()
        count = con.execute("SELECT COUNT(*) FROM wells").fetchone()[0]
        sample = con.execute(
            "SELECT well_id, name FROM wells LIMIT 3"
        ).fetchall()
        con.close()
        return SeedResult(
            total_wells=count, with_issues=0,
            sample_wells=[{"well_id": r[0], "name": r[1]} for r in sample],
            valid=True,
            errors=["Database already exists. Use force_recreate=true to rebuild."],
        )

    # Delete existing
    if DB_PATH.exists():
        DB_PATH.unlink()
    wal = DB_PATH.parent / (DB_PATH.name + ".wal")
    if wal.exists():
        wal.unlink()

    all_wells = list(_HERO_WELLS) + _generate_wells(seed, n_extra=15)

    con = duckdb.connect(str(DB_PATH))
    con.execute(_DDL)

    for w in all_wells:
        (wid, wname, pad, field, county, state, lat, lon,
         oil, liquid, wc, uplift, days_test, days_visit,
         restarts, vsd, motor_temp, intake_unstable, gauge_flat, conf,
         issue_cat, severity, action, workover, avg_repair_hours) = w

        con.execute(
            "INSERT INTO wells VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [wid, wname, "Delaware Basin", pad, field, county, state,
             "Delaware Basin", lat, lon, True, avg_repair_hours],
        )
        con.execute(
            "INSERT INTO production_latest VALUES (?,?,?,?,?,?,?)",
            [wid, oil, liquid, wc, uplift, days_test, days_visit],
        )
        con.execute(
            "INSERT INTO reliability_flags_latest VALUES (?,?,?,?,?,?,?,?,?,?)",
            [wid, restarts, vsd, motor_temp, intake_unstable, gauge_flat,
             conf, False, False, False],
        )
        con.execute(
            "INSERT INTO ops_recommendations_latest VALUES (?,?,?,?,?)",
            [wid, issue_cat, severity, action, workover],
        )

    con.execute(_VIEW_SQL)

    # Validate
    total = con.execute("SELECT COUNT(*) FROM wells").fetchone()[0]
    with_issues = con.execute(
        "SELECT COUNT(*) FROM ops_recommendations_latest WHERE issue_category IS NOT NULL"
    ).fetchone()[0]
    sample = con.execute(
        "SELECT well_id, name, priority_score, avg_repair_hours FROM well_priority_vw "
        "ORDER BY priority_score DESC LIMIT 5"
    ).fetchall()
    con.close()

    return SeedResult(
        total_wells=total,
        with_issues=with_issues,
        sample_wells=[
            {"well_id": r[0], "name": r[1], "priority_score": r[2], "avg_repair_hours": r[3]}
            for r in sample
        ],
        valid=True,
        errors=[],
    )
