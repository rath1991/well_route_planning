"""Build travel-time matrix between locations."""

from __future__ import annotations

from .schemas import Location, Well
from .utils import haversine_km


def build_time_matrix(
    start: Location,
    end: Location,
    wells: list[Well],
    avg_speed_kmph: float,
) -> list[list[float]]:
    """Return an (N+2)×(N+2) matrix of travel times in minutes.

    Index layout:
        0        → start
        1 .. N   → wells (in list order)
        N+1      → end

    Uses haversine great-circle distance / avg_speed_kmph.
    """
    points: list[tuple[float, float]] = [(start.lat, start.lon)]
    for w in wells:
        points.append((w.lat, w.lon))
    points.append((end.lat, end.lon))

    n = len(points)
    matrix: list[list[float]] = []
    for i in range(n):
        row: list[float] = []
        for j in range(n):
            if i == j:
                row.append(0.0)
            else:
                d = haversine_km(points[i][0], points[i][1], points[j][0], points[j][1])
                row.append(round(d / avg_speed_kmph * 60, 2))
        matrix.append(row)

    return matrix
