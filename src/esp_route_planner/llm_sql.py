"""LLM-to-SQL via OpenAI — converts natural language to DuckDB SQL.

Uses OpenAI API with schema context and few-shot examples.
Environment variables:
  OPENAI_API_KEY  — required
  OPENAI_MODEL    — optional, defaults to gpt-4o-mini
"""

from __future__ import annotations

import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

# ── Schema & join metadata ────────────────────────────────────────────────────

_SCHEMA_CONTEXT = """
DATABASE SCHEMA
===============

TABLE wells
  well_id VARCHAR PRIMARY KEY
  name VARCHAR          -- human-readable well name (e.g. "APEX HOLLOW 7911CL")
  asset VARCHAR
  pad_name VARCHAR
  field VARCHAR
  county VARCHAR
  state VARCHAR
  basin VARCHAR
  lat DOUBLE, lon DOUBLE
  is_esp BOOLEAN        -- always TRUE for wells in this DB
  avg_repair_hours DOUBLE  -- historical hours to diagnose + fix issues at this well

TABLE production_latest   (one row per well)
  well_id VARCHAR FK -> wells.well_id
  oil_bpd DOUBLE
  liquid_bpd DOUBLE
  water_cut_pct DOUBLE
  uplift_oil_bpd DOUBLE
  days_since_last_test INTEGER
  days_since_last_visit INTEGER

TABLE reliability_flags_latest   (one row per well)
  well_id VARCHAR FK -> wells.well_id
  restarts_7d INTEGER
  vsd_trips_7d INTEGER
  motor_temp_high BOOLEAN
  intake_pressure_unstable BOOLEAN
  downhole_gauge_flatlined BOOLEAN
  motor_current_spiky BOOLEAN
  amps_rising_flat_prod BOOLEAN
  vibration_high BOOLEAN
  confidence DOUBLE

TABLE ops_recommendations_latest   (one row per well)
  well_id VARCHAR FK -> wells.well_id
  issue_category VARCHAR   -- values: 'restarts_vsd_trips','gauge_flatlined',
                           --   'freq_suboptimal','pump_efficiency_drop',
                           --   'motor_current_spiky','amps_rising_flat_prod',
                           --   'vibration_high', NULL (no issue)
  severity VARCHAR         -- values: 'HIGH','MEDIUM','LOW', NULL
  action_required VARCHAR
  workover_horizon VARCHAR -- values: 'IMMEDIATE','3_6_MONTHS','NONE'

VIEW well_priority_vw   (pre-joined; use this for MOST queries)
  -- Joins wells + production_latest + reliability_flags_latest + ops_recommendations_latest
  -- Only includes ESP wells (is_esp = TRUE)
  -- Columns: well_id, name, lat, lon, field, county, state, pad_name,
  --          is_esp, avg_repair_hours,
  --          oil_bpd, liquid_bpd, water_cut_pct, uplift_oil_bpd,
  --          days_since_last_test, days_since_last_visit,
  --          restarts_7d, vsd_trips_7d, motor_temp_high,
  --          intake_pressure_unstable, downhole_gauge_flatlined,
  --          motor_current_spiky, amps_rising_flat_prod, vibration_high,
  --          confidence, issue_category, severity, action_required,
  --          workover_horizon,
  --          prod_score, uplift_score, urgency_score, confidence_score,
  --          recency_score, priority_score
  -- priority_score = 0.30*prod_score + 0.25*uplift_score + 0.25*urgency_score
  --                + 0.10*confidence_score + 0.10*recency_score  (each 0-100)

JOIN CONDITIONS (only needed when querying raw tables directly)
  wells            JOIN production_latest         ON wells.well_id = production_latest.well_id
  wells            JOIN reliability_flags_latest  ON wells.well_id = reliability_flags_latest.well_id
  wells LEFT JOIN  ops_recommendations_latest     ON wells.well_id = ops_recommendations_latest.well_id

COLUMN OWNERSHIP — critical, never select a column from the wrong table:
  name, lat, lon, county, state, field, basin, is_esp, avg_repair_hours
      → wells ONLY (not in production_latest or reliability_flags_latest)
  oil_bpd, liquid_bpd, water_cut_pct, uplift_oil_bpd,
  days_since_last_test, days_since_last_visit
      → production_latest ONLY
  restarts_7d, vsd_trips_7d, motor_temp_high, intake_pressure_unstable,
  downhole_gauge_flatlined, motor_current_spiky, amps_rising_flat_prod,
  vibration_high, confidence
      → reliability_flags_latest ONLY
  issue_category, severity, action_required, workover_horizon
      → ops_recommendations_latest ONLY
  prod_score, uplift_score, urgency_score, confidence_score,
  recency_score, priority_score
      → well_priority_vw ONLY (computed columns)
"""

_FEW_SHOT_EXAMPLES = """
QUERY EXAMPLES
==============

-- Use well_priority_vw for anything involving well name + production + priority --

Q: How many ESP wells are there?
SQL: SELECT COUNT(*) AS well_count FROM wells WHERE is_esp = TRUE

Q: Which wells should I focus on first?
SQL: SELECT well_id, name, oil_bpd, priority_score, issue_category, action_required FROM well_priority_vw ORDER BY priority_score DESC LIMIT 10

Q: What are the top 5 wells by priority score?
SQL: SELECT well_id, name, priority_score, issue_category, action_required FROM well_priority_vw ORDER BY priority_score DESC LIMIT 5

Q: Show me wells with high water cut
SQL: SELECT well_id, name, water_cut_pct, oil_bpd, priority_score FROM well_priority_vw WHERE water_cut_pct > 70 ORDER BY water_cut_pct DESC LIMIT 20

Q: Which high producing wells have reliability issues?
SQL: SELECT well_id, name, oil_bpd, issue_category, action_required, priority_score FROM well_priority_vw WHERE oil_bpd >= 300 AND issue_category IS NOT NULL ORDER BY oil_bpd DESC LIMIT 20

Q: Which wells need workover?
SQL: SELECT well_id, name, oil_bpd, issue_category, action_required, workover_horizon FROM well_priority_vw WHERE workover_horizon IN ('IMMEDIATE','3_6_MONTHS') ORDER BY CASE WHEN workover_horizon='IMMEDIATE' THEN 1 ELSE 2 END, priority_score DESC LIMIT 20

Q: Which wells take the longest to fix?
SQL: SELECT well_id, name, avg_repair_hours, issue_category, severity FROM well_priority_vw WHERE avg_repair_hours IS NOT NULL ORDER BY avg_repair_hours DESC LIMIT 10

Q: Show me wells with motor temperature issues
SQL: SELECT well_id, name, oil_bpd, priority_score, action_required FROM well_priority_vw WHERE motor_temp_high = TRUE ORDER BY priority_score DESC LIMIT 20

Q: Show me low confidence wells
SQL: SELECT well_id, name, confidence, priority_score, issue_category FROM well_priority_vw ORDER BY confidence ASC LIMIT 10

Q: Which wells have vibration problems?
SQL: SELECT well_id, name, oil_bpd, priority_score, action_required FROM well_priority_vw WHERE vibration_high = TRUE ORDER BY priority_score DESC LIMIT 20

Q: Show me all ESP wells where is_esp is true
SQL: SELECT well_id, name, oil_bpd, priority_score, issue_category FROM well_priority_vw WHERE is_esp = TRUE ORDER BY priority_score DESC LIMIT 20

-- Use raw tables only for pure aggregates on a single table or columns not in the view --

Q: What is the average oil production?
SQL: SELECT ROUND(AVG(oil_bpd),1) AS avg_oil_bpd, ROUND(MIN(oil_bpd),1) AS min_oil_bpd, ROUND(MAX(oil_bpd),1) AS max_oil_bpd FROM production_latest

Q: What are the most common ESP reliability issues?
SQL: SELECT issue_category, COUNT(*) AS count FROM ops_recommendations_latest WHERE issue_category IS NOT NULL GROUP BY issue_category ORDER BY count DESC

Q: How many wells are there in each county?
SQL: SELECT county, state, COUNT(*) AS well_count FROM wells GROUP BY county, state ORDER BY well_count DESC

Q: What is the average repair time for high severity wells?
SQL: SELECT ROUND(AVG(w.avg_repair_hours),1) AS avg_repair_hrs, COUNT(*) AS well_count FROM wells w JOIN ops_recommendations_latest o ON w.well_id = o.well_id WHERE o.severity = 'HIGH' AND w.avg_repair_hours IS NOT NULL

Q: Are there any issue categories with zero observations?
SQL: SELECT category FROM (VALUES ('restarts_vsd_trips'),('gauge_flatlined'),('freq_suboptimal'),('pump_efficiency_drop'),('motor_current_spiky'),('amps_rising_flat_prod'),('vibration_high')) AS all_cats(category) WHERE category NOT IN (SELECT DISTINCT issue_category FROM ops_recommendations_latest WHERE issue_category IS NOT NULL)

Q: Which wells have both high restarts and a reliability issue?
SQL: SELECT well_id, name, restarts_7d, issue_category, priority_score FROM well_priority_vw WHERE restarts_7d > 2 AND issue_category IS NOT NULL ORDER BY restarts_7d DESC LIMIT 20

Q: Where are the reliability wells located? Group by county.
SQL: SELECT w.county, w.state, COUNT(*) AS count, STRING_AGG(w.name, ', ') AS example_wells FROM wells w JOIN ops_recommendations_latest o ON w.well_id = o.well_id WHERE o.issue_category IS NOT NULL GROUP BY w.county, w.state ORDER BY count DESC
"""

_SYSTEM_PROMPT_TEMPLATE = """\
You are a SQL expert for an oil-field ESP (Electric Submersible Pump) well database.
Convert the user's natural language question into a single DuckDB SQL query.

RULES:
1. Return ONLY the raw SQL — no markdown, no code fences, no explanation.
2. ALWAYS prefer well_priority_vw over raw tables. It already joins everything.
   Only query raw tables when the query is a pure single-table aggregate
   or needs a column not exposed in the view.
3. NEVER select a column from a table that doesn't own it (see COLUMN OWNERSHIP).
   E.g. 'name' is ONLY in wells — never SELECT name FROM production_latest.
4. When joining raw tables always use the explicit join condition:
     JOIN <table> ON wells.well_id = <table>.well_id
5. Always include LIMIT (max 50) unless the query is a count/aggregate.
6. When results include individual well records (not aggregates), always
   include well_id as the FIRST column in SELECT.
7. DuckDB syntax: STRING_AGG, ROUND, LEAST, GREATEST, COALESCE.

{schema}

{examples}
"""


def nl_to_sql(query: str, schema: str) -> str:
    """Convert a natural language query to DuckDB SQL using OpenAI.

    Returns the raw SQL string (caller must validate with sql_guard).
    Raises ValueError if API key is missing or call fails.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set")

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=api_key)

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        schema=_SCHEMA_CONTEXT,
        examples=_FEW_SHOT_EXAMPLES,
    )

    logger.info("LLM-to-SQL: model=%s query=%r", model, query)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
        temperature=0,
        max_tokens=500,
    )

    sql = response.choices[0].message.content.strip()
    logger.info("LLM-to-SQL result: %s", sql)
    return sql
