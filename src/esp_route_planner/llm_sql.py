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

_FEW_SHOT_EXAMPLES = """
Example queries and their SQL:

Q: How many ESP wells have reliability issues?
SQL: SELECT COUNT(DISTINCT well_id) AS reliability_issue_count FROM ops_recommendations_latest WHERE issue_category IS NOT NULL

Q: What are the most common ESP reliability issues?
SQL: SELECT issue_category, COUNT(*) AS count FROM ops_recommendations_latest WHERE issue_category IS NOT NULL GROUP BY issue_category ORDER BY count DESC

Q: Which high producing wells have reliability issues?
SQL: SELECT v.well_id, v.name, v.oil_bpd, v.issue_category, v.action_required, v.priority_score FROM well_priority_vw v WHERE v.oil_bpd >= 300 AND v.issue_category IS NOT NULL ORDER BY v.oil_bpd DESC

Q: Which wells should I focus on first?
SQL: SELECT well_id, name, oil_bpd, priority_score, issue_category, action_required FROM well_priority_vw ORDER BY priority_score DESC LIMIT 10

Q: Where are the reliability wells located? Group by county.
SQL: SELECT w.county, w.state, COUNT(*) AS count, STRING_AGG(w.name, ', ') AS example_wells FROM wells w JOIN ops_recommendations_latest o ON w.well_id = o.well_id WHERE o.issue_category IS NOT NULL GROUP BY w.county, w.state ORDER BY count DESC

Q: Which wells need workover?
SQL: SELECT v.well_id, v.name, v.oil_bpd, v.issue_category, v.action_required, v.workover_horizon FROM well_priority_vw v WHERE v.workover_horizon IN ('IMMEDIATE', '3_6_MONTHS') ORDER BY CASE WHEN v.workover_horizon = 'IMMEDIATE' THEN 1 ELSE 2 END, v.priority_score DESC

Q: Are there any issue categories with zero observations?
SQL: SELECT category FROM (VALUES ('restarts_vsd_trips'), ('gauge_flatlined'), ('freq_suboptimal'), ('pump_efficiency_drop'), ('motor_current_spiky'), ('amps_rising_flat_prod'), ('vibration_high')) AS all_cats(category) WHERE category NOT IN (SELECT DISTINCT issue_category FROM ops_recommendations_latest WHERE issue_category IS NOT NULL)

Q: How many wells are there in each county?
SQL: SELECT county, state, COUNT(*) AS well_count FROM wells GROUP BY county, state ORDER BY well_count DESC

Q: What is the average oil production?
SQL: SELECT ROUND(AVG(oil_bpd), 1) AS avg_oil_bpd, ROUND(MIN(oil_bpd), 1) AS min_oil_bpd, ROUND(MAX(oil_bpd), 1) AS max_oil_bpd FROM production_latest

Q: Which wells take the longest to fix?
SQL: SELECT well_id, name, avg_repair_hours, issue_category, severity FROM well_priority_vw WHERE avg_repair_hours IS NOT NULL ORDER BY avg_repair_hours DESC LIMIT 10

Q: What is the average repair time for high severity wells?
SQL: SELECT ROUND(AVG(w.avg_repair_hours), 1) AS avg_repair_hrs, ROUND(MIN(w.avg_repair_hours), 1) AS min_repair_hrs, ROUND(MAX(w.avg_repair_hours), 1) AS max_repair_hrs, COUNT(*) AS well_count FROM wells w JOIN ops_recommendations_latest o ON w.well_id = o.well_id WHERE o.severity = 'HIGH' AND w.avg_repair_hours IS NOT NULL
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

    system_prompt = (
        "You are a SQL expert. Convert the user's natural language question into a single DuckDB SQL query.\n"
        "Rules:\n"
        "- Return ONLY the SQL query, no markdown, no explanation, no code fences.\n"
        "- Use only the tables and views described in the schema below.\n"
        "- Always include a reasonable LIMIT (max 50) unless counting/aggregating.\n"
        "- Use the well_priority_vw view when the question involves priority scores or ranking.\n"
        "- Use avg_repair_hours (from wells or well_priority_vw) when the question involves repair time, fix time, or maintenance duration.\n"
        "- DuckDB syntax: use STRING_AGG, ROUND, LEAST, GREATEST etc.\n"
        "- When the query returns individual well records (not aggregates/counts), always include well_id as the FIRST column in SELECT.\n\n"
        f"Schema:\n{schema}\n\n"
        f"Few-shot examples:\n{_FEW_SHOT_EXAMPLES}"
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
