#!/usr/bin/env python3
"""Inspect DuckDB — display tables, schemas, columns, views, and sample data.

Usage:
    python inspect_db.py              # full inspection
    python inspect_db.py --seed       # seed first, then inspect
    python inspect_db.py --table wells  # inspect a specific table
"""

import argparse
import sys
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).parent / "outputs" / "db" / "esp_delaware.duckdb"

SEPARATOR = "-" * 80


def connect():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        print("Run with --seed to create it, or call POST /admin/seed first.")
        sys.exit(1)
    return duckdb.connect(str(DB_PATH), read_only=True)


def print_header(title: str):
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def show_tables(con):
    print_header("TABLES")
    tables = con.execute("SHOW TABLES").fetchall()
    for t in tables:
        print(f"  - {t[0]}")
    print(f"\n  Total: {len(tables)} tables")
    return [t[0] for t in tables]


def show_views(con):
    print_header("VIEWS")
    views = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type = 'VIEW'"
    ).fetchall()
    if views:
        for v in views:
            print(f"  - {v[0]}")
    else:
        print("  (no views)")
    return [v[0] for v in views]


def show_columns(con, table_name: str):
    print_header(f"COLUMNS: {table_name}")
    cols = con.execute(f"DESCRIBE {table_name}").fetchall()
    print(f"  {'Column':<35} {'Type':<20} {'Null':<6} {'Key':<6} {'Default'}")
    print(f"  {'─'*35} {'─'*20} {'─'*6} {'─'*6} {'─'*15}")
    for c in cols:
        name, dtype, null_ok, key, default, extra = c[0], c[1], c[2], c[3], c[4], c[5] if len(c) > 5 else ""
        print(f"  {name:<35} {dtype:<20} {null_ok:<6} {str(key or ''):<6} {str(default or '')}")
    print(f"\n  Total: {len(cols)} columns")


def show_row_counts(con, tables, views):
    print_header("ROW COUNTS")
    for name in tables + views:
        count = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        kind = "view" if name in views else "table"
        print(f"  {name:<40} {count:>6} rows  ({kind})")


def show_sample_data(con, table_name: str, limit: int = 5):
    print_header(f"SAMPLE DATA: {table_name} (LIMIT {limit})")
    result = con.execute(f"SELECT * FROM {table_name} LIMIT {limit}").fetchall()
    columns = [desc[0] for desc in con.description]

    if not result:
        print("  (empty table)")
        return

    # Calculate column widths
    widths = [len(c) for c in columns]
    for row in result:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)[:40]))

    # Cap widths
    widths = [min(w, 40) for w in widths]

    # Print header
    header = "  ".join(f"{c:<{widths[i]}}" for i, c in enumerate(columns))
    print(f"  {header}")
    print(f"  {'  '.join('─' * w for w in widths)}")

    # Print rows
    for row in result:
        vals = []
        for i, val in enumerate(row):
            s = str(val)[:40]
            vals.append(f"{s:<{widths[i]}}")
        print(f"  {'  '.join(vals)}")


def show_priority_view_top(con, limit: int = 10):
    print_header(f"TOP {limit} WELLS BY PRIORITY SCORE (well_priority_vw)")
    result = con.execute(f"""
        SELECT well_id, name, oil_bpd, priority_score,
               prod_score, uplift_score, urgency_score,
               confidence_score, recency_score,
               issue_category, workover_horizon
        FROM well_priority_vw
        ORDER BY priority_score DESC
        LIMIT {limit}
    """).fetchall()
    columns = [desc[0] for desc in con.description]

    print(f"  {'well_id':<18} {'name':<28} {'oil':>6} {'pri':>6} "
          f"{'prod':>5} {'uplft':>5} {'urg':>5} {'conf':>5} {'rec':>5} "
          f"{'issue_category':<22} {'workover'}")
    print(f"  {'─'*18} {'─'*28} {'─'*6} {'─'*6} "
          f"{'─'*5} {'─'*5} {'─'*5} {'─'*5} {'─'*5} "
          f"{'─'*22} {'─'*10}")

    for r in result:
        wid, name, oil, pri, prod, uplift, urg, conf, rec, issue, wo = r
        print(f"  {wid:<18} {(name or '')[:28]:<28} {oil:>6.0f} {pri:>6.1f} "
              f"{prod:>5.0f} {uplift:>5.0f} {urg:>5.0f} {conf:>5.0f} {rec:>5.0f} "
              f"{(issue or 'None'):<22} {wo or 'NONE'}")


def show_issue_summary(con):
    print_header("ISSUE CATEGORY SUMMARY")
    result = con.execute("""
        SELECT COALESCE(issue_category, '(no issue)') AS category,
               COUNT(*) AS count
        FROM ops_recommendations_latest
        RIGHT JOIN wells ON wells.well_id = ops_recommendations_latest.well_id
        GROUP BY category
        ORDER BY count DESC
    """).fetchall()
    for cat, count in result:
        bar = "#" * count
        print(f"  {cat:<30} {count:>3}  {bar}")


def show_workover_summary(con):
    print_header("WORKOVER HORIZON SUMMARY")
    result = con.execute("""
        SELECT workover_horizon, COUNT(*) AS count
        FROM ops_recommendations_latest
        WHERE workover_horizon IS NOT NULL AND workover_horizon != 'NONE'
        GROUP BY workover_horizon
        ORDER BY CASE WHEN workover_horizon = 'IMMEDIATE' THEN 1 ELSE 2 END
    """).fetchall()
    if result:
        for horizon, count in result:
            print(f"  {horizon:<20} {count}")
    else:
        print("  (no workover entries)")


def show_geography(con):
    print_header("GEOGRAPHIC DISTRIBUTION")
    result = con.execute("""
        SELECT county, state, COUNT(*) AS count,
               ROUND(MIN(lat), 2) AS min_lat, ROUND(MAX(lat), 2) AS max_lat,
               ROUND(MIN(lon), 2) AS min_lon, ROUND(MAX(lon), 2) AS max_lon
        FROM wells
        GROUP BY county, state
        ORDER BY count DESC
    """).fetchall()
    print(f"  {'County':<15} {'State':<6} {'Count':>5}  {'Lat range':<20} {'Lon range'}")
    print(f"  {'─'*15} {'─'*6} {'─'*5}  {'─'*20} {'─'*20}")
    for county, state, count, min_lat, max_lat, min_lon, max_lon in result:
        print(f"  {county:<15} {state:<6} {count:>5}  {min_lat} to {max_lat:<12}  {min_lon} to {max_lon}")


def show_reliability_flags(con):
    print_header("RELIABILITY FLAGS SUMMARY")
    flags = [
        ("restarts_7d > 0", "Wells with restarts"),
        ("vsd_trips_7d > 0", "Wells with VSD trips"),
        ("motor_temp_high = TRUE", "Motor temp high"),
        ("intake_pressure_unstable = TRUE", "Intake pressure unstable"),
        ("downhole_gauge_flatlined = TRUE", "Gauge flatlined"),
        ("motor_current_spiky = TRUE", "Motor current spiky"),
        ("amps_rising_flat_prod = TRUE", "Amps rising flat prod"),
        ("vibration_high = TRUE", "Vibration high"),
    ]
    for condition, label in flags:
        count = con.execute(
            f"SELECT COUNT(*) FROM reliability_flags_latest WHERE {condition}"
        ).fetchone()[0]
        print(f"  {label:<35} {count:>3}")


def main():
    parser = argparse.ArgumentParser(description="Inspect DuckDB ESP database")
    parser.add_argument("--seed", action="store_true", help="Seed the database before inspecting")
    parser.add_argument("--table", type=str, help="Inspect a specific table only")
    parser.add_argument("--limit", type=int, default=5, help="Sample data row limit (default: 5)")
    args = parser.parse_args()

    if args.seed:
        # Seed via the module
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from esp_route_planner.database import seed_database
        print("Seeding database...")
        result = seed_database(seed=42, force_recreate=True)
        print(f"Seeded: {result.total_wells} wells, {result.with_issues} with issues")
        print()

    con = connect()

    if args.table:
        show_columns(con, args.table)
        show_sample_data(con, args.table, limit=args.limit)
        con.close()
        return

    # Full inspection
    tables = show_tables(con)
    views = show_views(con)
    show_row_counts(con, tables, views)

    for t in tables:
        show_columns(con, t)
        show_sample_data(con, t, limit=args.limit)

    for v in views:
        show_columns(con, v)

    show_priority_view_top(con, limit=10)
    show_issue_summary(con)
    show_workover_summary(con)
    show_reliability_flags(con)
    show_geography(con)

    con.close()
    print(f"\n{SEPARATOR}")
    print(f"  Database: {DB_PATH}")
    print(f"  Size: {DB_PATH.stat().st_size / 1024:.1f} KB")
    print(SEPARATOR)


if __name__ == "__main__":
    main()
