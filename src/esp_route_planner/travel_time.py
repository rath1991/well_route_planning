"""Build travel-time matrix using Google Maps Distance Matrix API.

Falls back to haversine estimation if the API key is missing or the call fails.
Google Distance Matrix API has a limit of 25 origins × 25 destinations per request,
which is fine for our ~20 well dataset.
"""

from __future__ import annotations

import logging
import os

from .schemas import Location, Well
from .utils import haversine_km

logger = logging.getLogger(__name__)

# Google Distance Matrix: max 100 elements (origins × destinations) per request
# With 10 origins × 10 destinations = 100 elements
_GMAPS_CHUNK_SIZE = 10


def build_time_matrix(
    start: Location,
    end: Location,
    wells: list[Well],
    avg_speed_kmph: float = 45,
) -> list[list[float]]:
    """Return an (N+2)×(N+2) matrix of travel times in minutes.

    Index layout:
        0        -> start
        1 .. N   -> wells (in list order)
        N+1      -> end

    Tries Google Maps Distance Matrix API first for real driving times.
    Falls back to haversine / avg_speed_kmph if unavailable.
    """
    points: list[tuple[float, float]] = [(start.lat, start.lon)]
    for w in wells:
        points.append((w.lat, w.lon))
    points.append((end.lat, end.lon))

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if api_key:
        try:
            matrix = _google_maps_matrix(points, api_key)
            logger.info("Travel time matrix built via Google Maps Distance Matrix API")
            return matrix
        except Exception as e:
            logger.warning("Google Maps API failed, falling back to haversine: %s", e)

    return _haversine_matrix(points, avg_speed_kmph)


def _google_maps_matrix(
    points: list[tuple[float, float]],
    api_key: str,
) -> list[list[float]]:
    """Build travel time matrix using Google Maps Distance Matrix API.

    Chunks requests to stay within the 100-element limit per API call.
    Returns matrix of driving times in minutes.
    """
    import googlemaps

    client = googlemaps.Client(key=api_key)
    n = len(points)
    locations = [{"lat": lat, "lng": lon} for lat, lon in points]
    matrix = [[0.0] * n for _ in range(n)]
    chunk = _GMAPS_CHUNK_SIZE

    for i_start in range(0, n, chunk):
        i_end = min(i_start + chunk, n)
        origins = locations[i_start:i_end]

        for j_start in range(0, n, chunk):
            j_end = min(j_start + chunk, n)
            destinations = locations[j_start:j_end]

            result = client.distance_matrix(
                origins=origins,
                destinations=destinations,
                mode="driving",
                units="metric",
            )

            if result.get("status") != "OK":
                raise RuntimeError(f"Distance Matrix API error: {result.get('status')}")

            for i_local, row in enumerate(result["rows"]):
                for j_local, element in enumerate(row["elements"]):
                    i_global = i_start + i_local
                    j_global = j_start + j_local
                    if element["status"] == "OK":
                        matrix[i_global][j_global] = round(
                            element["duration"]["value"] / 60, 1
                        )
                    else:
                        # Fallback for this specific pair
                        p1, p2 = points[i_global], points[j_global]
                        d = haversine_km(p1[0], p1[1], p2[0], p2[1])
                        matrix[i_global][j_global] = round(d / 45 * 60, 1)

    return matrix


def _haversine_matrix(
    points: list[tuple[float, float]],
    avg_speed_kmph: float,
) -> list[list[float]]:
    """Fallback: build matrix using haversine distance / avg speed."""
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
