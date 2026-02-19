"""SQL safety guard — ensures LLM-generated SQL is read-only.

Blocks any SQL containing write/DDL/dangerous keywords.
Enforces single SELECT (or WITH...SELECT) only.
Adds LIMIT 50 if missing.
"""

from __future__ import annotations

import re

# Keywords that indicate write/DDL/dangerous operations
_BLOCKED_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "ATTACH", "COPY", "CALL", "EXPORT", "TRUNCATE", "MERGE",
    "GRANT", "REVOKE",
]

# PRAGMA is blocked except for internal use
_BLOCKED_PATTERNS = [
    re.compile(r"\bPRAGMA\b", re.IGNORECASE),
]

# Allowed tables and views
_ALLOWED_OBJECTS = {
    "wells", "production_latest", "reliability_flags_latest",
    "ops_recommendations_latest", "well_priority_vw",
}


class SQLGuardError(Exception):
    """Raised when SQL fails safety checks."""


def validate_sql(sql: str) -> str:
    """Validate and sanitize LLM-generated SQL. Returns cleaned SQL or raises SQLGuardError."""
    if not sql or not sql.strip():
        raise SQLGuardError("Empty SQL")

    cleaned = sql.strip().rstrip(";").strip()

    # Remove markdown code fences if LLM included them
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    upper = cleaned.upper()

    # Block dangerous keywords
    for kw in _BLOCKED_KEYWORDS:
        pattern = re.compile(rf"\b{kw}\b", re.IGNORECASE)
        if pattern.search(cleaned):
            raise SQLGuardError(f"Blocked keyword detected: {kw}")

    # Block PRAGMA
    for pat in _BLOCKED_PATTERNS:
        if pat.search(cleaned):
            raise SQLGuardError("PRAGMA statements are not allowed")

    # Must start with SELECT or WITH
    first_word = upper.lstrip().split()[0] if upper.strip() else ""
    if first_word not in ("SELECT", "WITH"):
        raise SQLGuardError(f"SQL must start with SELECT or WITH, got: {first_word}")

    # Block multiple statements (semicolons in the middle)
    # Allow semicolon only at the very end
    if ";" in cleaned:
        raise SQLGuardError("Multiple statements not allowed")

    # Add LIMIT if missing
    if "LIMIT" not in upper:
        cleaned = cleaned + "\nLIMIT 50"

    return cleaned
