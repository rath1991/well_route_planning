"""Intent detection — route vs data question classification."""

from __future__ import annotations

import re

_ROUTE_KEYWORDS = [
    r"\broute\b", r"\bvisit\b", r"\bplan my day\b", r"\bstops?\b",
    r"\bschedule\b", r"\bdrive\b", r"\bhours?\b", r"\btravel\b",
    r"\boptimize.*visit", r"\bfield trip\b", r"\bitinerary\b",
    r"\bplan.*route\b", r"\bwhere should i go\b",
]

_ROUTE_PATTERN = re.compile("|".join(_ROUTE_KEYWORDS), re.IGNORECASE)


def is_routing_intent(query: str) -> bool:
    """Return True if the query is about route planning / field visits."""
    return bool(_ROUTE_PATTERN.search(query))
