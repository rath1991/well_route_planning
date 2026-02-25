"""Intent detection — route vs data question classification."""

from __future__ import annotations

import re

_ROUTE_KEYWORDS = [
    r"\broute\b",
    r"\bvisit\b",
    r"\bplan my day\b",
    r"\bfield trip\b",
    r"\bitinerary\b",
    r"\boptimize.*visit",
    r"\bplan.*route\b",
    r"\bwhere should i go\b",
    r"\bplan my (?:visits?|stops?|trip)\b",
    r"\bgo.*(?:these|those|the) wells?\b",
]

_ROUTE_PATTERN = re.compile("|".join(_ROUTE_KEYWORDS), re.IGNORECASE)


def is_routing_intent(query: str) -> bool:
    """Return True if the query is about route planning / field visits."""
    return bool(_ROUTE_PATTERN.search(query))
