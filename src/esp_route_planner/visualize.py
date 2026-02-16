"""Folium map generation with animated route and stop-by-stop commentary."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import folium
from folium.plugins import AntPath

from .schemas import Location, ScheduleStop, ScoreBreakdown, Well
from .utils import OUTPUTS_DIR, ensure_output_dirs


def _popup_html(well: Well, bd: ScoreBreakdown, stop_num: int, action: str) -> str:
    """Build English-language popup HTML for a visited well marker."""
    issues = ", ".join(well.issues) if well.issues else "None"
    action_line = f"<br><b>Action Required:</b> {action}" if action else ""
    return (
        f"<div style='min-width:220px;font-family:sans-serif;font-size:13px;'>"
        f"<b>Stop {stop_num}: {well.name}</b><br>"
        f"<b>Well ID:</b> {well.well_id}<br>"
        f"<b>Asset:</b> {well.asset} / {well.corridor}<br>"
        f"<b>Priority Score:</b> {bd.priority_score:.1f} / 100<br>"
        f"<hr style='margin:4px 0;'>"
        f"<b>Production:</b> {well.current_oil_bpd:.0f} bpd oil<br>"
        f"<b>Uplift Potential:</b> {well.uplift_oil_bpd:.0f} bpd<br>"
        f"<b>Water Cut:</b> {well.water_cut_pct:.1f}%<br>"
        f"<b>Confidence:</b> {well.confidence:.0%}<br>"
        f"<b>Issues:</b> {issues}"
        f"{action_line}"
        f"<hr style='margin:4px 0;'>"
        f"<small>Prod: {bd.prod_score:.0f} | Uplift: {bd.uplift_score:.0f} "
        f"| Urgency: {bd.urgency_score:.0f} | Conf: {bd.confidence_score:.0f} "
        f"| Recency: {bd.recency_score:.0f}</small>"
        f"</div>"
    )


def _skipped_popup_html(well: Well, bd: ScoreBreakdown) -> str:
    """Popup for a well not selected for the route."""
    return (
        f"<div style='min-width:180px;font-family:sans-serif;font-size:13px;'>"
        f"<b>{well.name}</b> (skipped)<br>"
        f"<b>Priority Score:</b> {bd.priority_score:.1f} / 100<br>"
        f"<b>Production:</b> {well.current_oil_bpd:.0f} bpd<br>"
        f"<b>Reason:</b> Lower priority or long detour"
        f"</div>"
    )


def build_map(
    start: Location,
    end: Location,
    wells: list[Well],
    visited_indices: list[int],
    breakdowns: list[ScoreBreakdown],
    schedule: list[ScheduleStop],
) -> str:
    """Create a Folium HTML map with animated route and stop numbers."""
    ensure_output_dirs()

    center_lat = (start.lat + end.lat) / 2
    center_lon = (start.lon + end.lon) / 2
    m = folium.Map(location=[center_lat, center_lon], zoom_start=9)

    # ── Start marker ─────────────────────────────────────────────────────
    folium.Marker(
        [start.lat, start.lon],
        popup=f"<b>START</b><br>{start.name}",
        icon=folium.DivIcon(
            html=(
                '<div style="background:#22c55e;color:white;border-radius:50%;'
                'width:32px;height:32px;display:flex;align-items:center;'
                'justify-content:center;font-weight:bold;font-size:14px;'
                'border:2px solid white;box-shadow:0 2px 4px rgba(0,0,0,0.3);">'
                'S</div>'
            ),
            icon_size=(32, 32),
            icon_anchor=(16, 16),
        ),
    ).add_to(m)

    # ── End marker ───────────────────────────────────────────────────────
    folium.Marker(
        [end.lat, end.lon],
        popup=f"<b>END</b><br>{end.name}",
        icon=folium.DivIcon(
            html=(
                '<div style="background:#ef4444;color:white;border-radius:50%;'
                'width:32px;height:32px;display:flex;align-items:center;'
                'justify-content:center;font-weight:bold;font-size:14px;'
                'border:2px solid white;box-shadow:0 2px 4px rgba(0,0,0,0.3);">'
                'E</div>'
            ),
            icon_size=(32, 32),
            icon_anchor=(16, 16),
        ),
    ).add_to(m)

    visited_set = set(visited_indices)

    # ── Skipped well markers (gray) ──────────────────────────────────────
    for i, well in enumerate(wells):
        if i in visited_set:
            continue
        folium.CircleMarker(
            [well.lat, well.lon],
            radius=6,
            color="#9ca3af",
            fill=True,
            fill_color="#d1d5db",
            fill_opacity=0.7,
            popup=_skipped_popup_html(well, breakdowns[i]),
        ).add_to(m)

    # ── Visited well markers with stop numbers ───────────────────────────
    # Build a mapping from well_idx to schedule stop_number
    stop_map: dict[int, ScheduleStop] = {}
    for stop in schedule:
        for idx in visited_indices:
            if wells[idx].well_id == stop.stop_id:
                stop_map[idx] = stop
                break

    for rank, well_idx in enumerate(visited_indices, 1):
        w = wells[well_idx]
        bd = breakdowns[well_idx]
        sched = stop_map.get(well_idx)
        action = sched.action_required if sched else w.action_required

        folium.Marker(
            [w.lat, w.lon],
            popup=_popup_html(w, bd, rank, action),
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

    # ── Animated route polyline (AntPath) ────────────────────────────────
    route_coords: list[list[float]] = [[start.lat, start.lon]]
    for idx in visited_indices:
        route_coords.append([wells[idx].lat, wells[idx].lon])
    route_coords.append([end.lat, end.lon])

    AntPath(
        locations=route_coords,
        color="#2563eb",
        weight=4,
        opacity=0.8,
        dash_array=[10, 20],
        delay=1000,
        pulse_color="#93c5fd",
    ).add_to(m)

    # ── Route commentary panel ───────────────────────────────────────────
    commentary_lines: list[str] = []
    for rank, well_idx in enumerate(visited_indices, 1):
        w = wells[well_idx]
        bd = breakdowns[well_idx]
        sched = stop_map.get(well_idx)
        eta = sched.eta if sched else "?"
        why = sched.why_selected if sched else ""
        commentary_lines.append(
            f"<b>Stop {rank}</b> ({eta}): {w.name} "
            f"&mdash; Priority {bd.priority_score:.1f}<br>"
            f"<small style='color:#555;'>{why}</small>"
        )

    total_priority = sum(breakdowns[i].priority_score for i in visited_indices)
    commentary_html = (
        '<div style="position:fixed;top:10px;right:10px;z-index:1000;'
        'background:white;padding:12px 16px;border:1px solid #ccc;border-radius:8px;'
        'max-width:340px;max-height:80vh;overflow-y:auto;'
        'font-family:sans-serif;font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,0.15);">'
        '<b style="font-size:15px;">Route Plan</b><br>'
        f'<span style="color:#666;">{len(visited_indices)} stops | '
        f'Total priority: {total_priority:.1f}</span>'
        '<hr style="margin:6px 0;">'
        + "<br>".join(commentary_lines)
        + "</div>"
    )
    m.get_root().html.add_child(folium.Element(commentary_html))

    # ── Legend ───────────────────────────────────────────────────────────
    legend_html = """
    <div style="position:fixed;bottom:20px;left:20px;z-index:1000;
         background:white;padding:10px 14px;border:1px solid #ccc;border-radius:8px;
         font-family:sans-serif;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,0.12);">
    <b>Legend</b><br>
    <span style="color:#22c55e;">&#9679;</span> Start &nbsp;
    <span style="color:#ef4444;">&#9679;</span> End &nbsp;
    <span style="color:#2563eb;">&#9679;</span> Visited Well &nbsp;
    <span style="color:#9ca3af;">&#9679;</span> Skipped Well<br>
    <span style="color:#2563eb;">&#8594;</span> Animated route (visit order)
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
    wells: list[Well],
    visited_indices: list[int],
) -> str:
    """Build a Google Maps directions URL with waypoints in visit order."""
    origin = f"{start.lat},{start.lon}"
    destination = f"{end.lat},{end.lon}"
    waypoints = "|".join(f"{wells[i].lat},{wells[i].lon}" for i in visited_indices)
    url = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={origin}&destination={destination}"
    )
    if waypoints:
        url += f"&waypoints={waypoints}"
    return url
