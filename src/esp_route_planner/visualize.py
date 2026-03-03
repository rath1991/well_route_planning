"""Folium map generation with animated segment-by-segment transit progression.

Uses TimestampedGeoJson for time-ordered segment animation showing
approximate travel times between stops.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import folium
from folium.plugins import AntPath, TimestampedGeoJson

from .schemas import Location, ScoreBreakdown
from .utils import OUTPUTS_DIR, ensure_output_dirs


def build_transit_map(
    start: Location,
    end: Location,
    wells: list[dict],
    visited_indices: list[int],
    breakdowns: list[ScoreBreakdown],
    schedule: list[dict],
) -> str:
    """Create an animated Folium map with segment-by-segment transit progression.

    Each leg animates in time order with approximate travel times.
    Returns the file path to the saved HTML.
    """
    ensure_output_dirs()

    center_lat = (start.lat + end.lat) / 2
    center_lon = (start.lon + end.lon) / 2
    m = folium.Map(location=[center_lat, center_lon], zoom_start=9,
                   tiles="CartoDB positron")

    # ── Build route coordinates and timing ─────────────────────────────────
    route_points = [(start.lat, start.lon)]
    for idx in visited_indices:
        route_points.append((wells[idx]["lat"], wells[idx]["lon"]))
    route_points.append((end.lat, end.lon))

    # Extract drive times from schedule
    drive_times = []
    for stop in schedule:
        if stop["stop_number"] > 0:
            drive_times.append(stop["drive_minutes"])

    # ── Static route polyline (light background) ──────────────────────────
    folium.PolyLine(
        locations=route_points,
        color="#94a3b8",
        weight=3,
        opacity=0.4,
        dash_array="8 4",
    ).add_to(m)

    # ── AntPath for animated flow direction ────────────────────────────────
    AntPath(
        locations=route_points,
        color="#2563eb",
        weight=4,
        opacity=0.7,
        dash_array=[10, 20],
        delay=1500,
        pulse_color="#93c5fd",
    ).add_to(m)

    # ── TimestampedGeoJson for segment-by-segment progression ─────────────
    base_time = datetime(2025, 1, 1, 8, 0, 0)  # 08:00 start
    features = []
    cumulative_min = 0.0

    for seg_idx in range(len(route_points) - 1):
        p1 = route_points[seg_idx]
        p2 = route_points[seg_idx + 1]
        drive_min = drive_times[seg_idx] if seg_idx < len(drive_times) else 10

        seg_start = base_time + timedelta(minutes=cumulative_min)
        cumulative_min += drive_min
        seg_end = base_time + timedelta(minutes=cumulative_min)

        # Add service time for visited stops (not for return leg)
        if seg_idx < len(visited_indices):
            cumulative_min += 35  # service minutes

        stop_label = f"Stop {seg_idx + 1}" if seg_idx < len(visited_indices) else "Return"
        tooltip = f"Drive ~{drive_min:.0f} min to {stop_label}"

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [p1[1], p1[0]],  # GeoJSON is [lon, lat]
                    [p2[1], p2[0]],
                ],
            },
            "properties": {
                "times": [
                    seg_start.isoformat(),
                    seg_end.isoformat(),
                ],
                "style": {
                    "color": "#ef4444" if seg_idx == len(route_points) - 2 else "#2563eb",
                    "weight": 5,
                    "opacity": 0.8,
                },
                "popup": tooltip,
            },
        })

    if features:
        TimestampedGeoJson(
            {"type": "FeatureCollection", "features": features},
            period="PT1M",
            add_last_point=True,
            auto_play=True,
            loop=True,
            max_speed=10,
            loop_button=True,
            time_slider_drag_update=True,
        ).add_to(m)

    # ── Start marker ──────────────────────────────────────────────────────
    folium.Marker(
        [start.lat, start.lon],
        popup=f"<b>START</b><br>{start.name}",
        icon=folium.DivIcon(
            html=(
                '<div style="background:#22c55e;color:white;border-radius:50%;'
                'width:34px;height:34px;display:flex;align-items:center;'
                'justify-content:center;font-weight:bold;font-size:13px;'
                'border:2px solid white;box-shadow:0 2px 4px rgba(0,0,0,0.3);">'
                'Start</div>'
            ),
            icon_size=(34, 34),
            icon_anchor=(17, 17),
        ),
    ).add_to(m)

    # ── End marker ────────────────────────────────────────────────────────
    folium.Marker(
        [end.lat, end.lon],
        popup=f"<b>END</b><br>{end.name}",
        icon=folium.DivIcon(
            html=(
                '<div style="background:#ef4444;color:white;border-radius:50%;'
                'width:34px;height:34px;display:flex;align-items:center;'
                'justify-content:center;font-weight:bold;font-size:13px;'
                'border:2px solid white;box-shadow:0 2px 4px rgba(0,0,0,0.3);">'
                'End</div>'
            ),
            icon_size=(34, 34),
            icon_anchor=(17, 17),
        ),
    ).add_to(m)

    # ── Skipped well markers (gray) ───────────────────────────────────────
    visited_set = set(visited_indices)
    for i, w in enumerate(wells):
        if i in visited_set:
            continue
        folium.CircleMarker(
            [w["lat"], w["lon"]],
            radius=6, color="#9ca3af", fill=True,
            fill_color="#d1d5db", fill_opacity=0.7,
            popup=(
                f"<div style='font-family:sans-serif;font-size:13px;'>"
                f"<b>{w['name']}</b> (skipped)<br>"
                f"Priority: {w.get('priority_score', 0):.1f}<br>"
                f"Oil: {w.get('oil_bpd', 0):.0f} bpd</div>"
            ),
        ).add_to(m)

    # ── Visited well markers with stop numbers ────────────────────────────
    for rank, well_idx in enumerate(visited_indices, 1):
        w = wells[well_idx]
        bd = breakdowns[well_idx]

        # Find matching schedule entry
        sched_entry = None
        for s in schedule:
            if s.get("stop_id") == w["well_id"]:
                sched_entry = s
                break

        drive_min = sched_entry["drive_minutes"] if sched_entry else 0
        svc_min = sched_entry["service_minutes"] if sched_entry else 0
        eta = sched_entry["eta"] if sched_entry else "?"
        action = w.get("action_required", "")

        popup_html = (
            f"<div style='min-width:220px;font-family:sans-serif;font-size:13px;'>"
            f"<b>Stop {rank}: {w['name']}</b><br>"
            f"<b>ETA:</b> {eta} (drive ~{drive_min:.0f} min)<br>"
            f"<b>Time on site:</b> ~{svc_min:.0f} min<br>"
            f"<b>Priority:</b> {bd.priority_score:.1f} / 100<br>"
            f"<hr style='margin:4px 0;'>"
            f"<b>Oil:</b> {w.get('oil_bpd', 0):.0f} bpd<br>"
            f"<b>Uplift:</b> {w.get('uplift_oil_bpd', 0):.0f} bpd<br>"
            f"<b>Issue:</b> {w.get('issue_category', 'None')}<br>"
            f"<b>Action:</b> {action}<br>"
            f"<hr style='margin:4px 0;'>"
            f"<small>Prod: {bd.prod_score:.0f} | Uplift: {bd.uplift_score:.0f} "
            f"| Urgency: {bd.urgency_score:.0f} | Conf: {bd.confidence_score:.0f} "
            f"| Recency: {bd.recency_score:.0f}</small>"
            f"</div>"
        )

        folium.Marker(
            [w["lat"], w["lon"]],
            popup=popup_html,
            tooltip=f"Stop {rank}: {w['name']} (~{drive_min:.0f} min drive, ~{svc_min:.0f} min on site)",
            icon=folium.DivIcon(
                html=(
                    f'<div style="background:#2563eb;color:white;border-radius:50%;'
                    f'width:30px;height:30px;display:flex;align-items:center;'
                    f'justify-content:center;font-weight:bold;font-size:14px;'
                    f'border:2px solid white;box-shadow:0 2px 4px rgba(0,0,0,0.3);">'
                    f'{rank}</div>'
                ),
                icon_size=(30, 30),
                icon_anchor=(15, 15),
            ),
        ).add_to(m)

    # ── Drive time labels on each segment ─────────────────────────────────
    for seg_idx in range(len(route_points) - 1):
        p1 = route_points[seg_idx]
        p2 = route_points[seg_idx + 1]
        mid_lat = (p1[0] + p2[0]) / 2
        mid_lon = (p1[1] + p2[1]) / 2
        drive_min = drive_times[seg_idx] if seg_idx < len(drive_times) else 0

        if drive_min > 0:
            folium.Marker(
                [mid_lat, mid_lon],
                icon=folium.DivIcon(
                    html=(
                        f'<div style="background:rgba(255,255,255,0.9);color:#374151;'
                        f'padding:2px 6px;border-radius:4px;font-size:11px;'
                        f'font-family:sans-serif;white-space:nowrap;'
                        f'border:1px solid #d1d5db;">'
                        f'~{drive_min:.0f} min</div>'
                    ),
                    icon_size=(60, 20),
                    icon_anchor=(30, 10),
                ),
            ).add_to(m)

    # ── Route commentary panel ────────────────────────────────────────────
    commentary_lines = []
    for rank, well_idx in enumerate(visited_indices, 1):
        w = wells[well_idx]
        bd = breakdowns[well_idx]
        sched_entry = None
        for s in schedule:
            if s.get("stop_id") == w["well_id"]:
                sched_entry = s
                break
        eta = sched_entry["eta"] if sched_entry else "?"
        drive = sched_entry["drive_minutes"] if sched_entry else 0
        svc = sched_entry["service_minutes"] if sched_entry else 0
        commentary_lines.append(
            f"<b>Stop {rank}</b> ({eta}, ~{drive:.0f} min drive, ~{svc:.0f} min on site): {w['name']} "
            f"&mdash; Priority {bd.priority_score:.1f}"
        )

    total_priority = sum(breakdowns[i].priority_score for i in visited_indices)
    commentary_html = (
        '<div style="position:fixed;top:10px;right:10px;z-index:1000;'
        'background:white;padding:12px 16px;border:1px solid #ccc;border-radius:8px;'
        'max-width:360px;max-height:80vh;overflow-y:auto;'
        'font-family:sans-serif;font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,0.15);">'
        '<b style="font-size:15px;">Route Plan</b><br>'
        f'<span style="color:#666;">{len(visited_indices)} stops | '
        f'Total priority: {total_priority:.1f}</span>'
        '<hr style="margin:6px 0;">'
        + "<br>".join(commentary_lines)
        + "</div>"
    )
    m.get_root().html.add_child(folium.Element(commentary_html))

    # ── Legend ─────────────────────────────────────────────────────────────
    legend_html = """
    <div style="position:fixed;bottom:20px;left:20px;z-index:1000;
         background:white;padding:10px 14px;border:1px solid #ccc;border-radius:8px;
         font-family:sans-serif;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,0.12);">
    <b>Legend</b><br>
    <span style="color:#22c55e;">&#9679;</span> Start &nbsp;
    <span style="color:#ef4444;">&#9679;</span> End &nbsp;
    <span style="color:#2563eb;">&#9679;</span> Visited Well &nbsp;
    <span style="color:#9ca3af;">&#9679;</span> Skipped Well<br>
    <span style="color:#2563eb;">&#8594;</span> Animated route (use timeline to replay)
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUTS_DIR / "maps" / f"map_{ts}.html"
    m.save(str(path))
    return str(path)


def google_maps_url(
    start: Location,
    end: Location,
    wells: list[dict],
    visited_indices: list[int],
) -> str:
    """Build a Google Maps directions URL with waypoints in visit order."""
    origin = f"{start.lat},{start.lon}"
    destination = f"{end.lat},{end.lon}"
    waypoints = "|".join(
        f"{wells[i]['lat']},{wells[i]['lon']}" for i in visited_indices
    )
    url = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={origin}&destination={destination}"
    )
    if waypoints:
        url += f"&waypoints={waypoints}"
    return url
