"""Shared utilities — haversine, time formatting, path helpers."""

from __future__ import annotations

import math
from pathlib import Path

OUTPUTS_DIR = Path(__file__).resolve().parents[2] / "outputs"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometres."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def minutes_from_start(minutes: float) -> str:
    """Convert minutes-since-08:00 to HH:MM string."""
    h, m = divmod(int(minutes), 60)
    return f"{8 + h:02d}:{m:02d}"


def ensure_output_dirs() -> None:
    """Create output sub-directories if they don't exist."""
    for sub in ("mock", "plans", "maps"):
        (OUTPUTS_DIR / sub).mkdir(parents=True, exist_ok=True)
