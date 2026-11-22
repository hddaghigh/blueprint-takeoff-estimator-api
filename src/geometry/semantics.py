"""Turn raw page geometry into construction elements (needs a drawing scale).

All inputs are in PDF points (top-origin, as pdfplumber reports them). A scale
factor converts paper length to real length (see src/measure/scale.py).
Results are heuristics: callers must label them low/medium confidence.
"""

import math
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from src.geometry.paths import (
    Segment,
    SubPath,
    bbox,
    point_in_polygon,
    polygon_area,
    polyline_length,
    subpaths,
)

POINTS_PER_FOOT = 72.0 * 12.0  # paper points per paper foot

WALL_THICKNESS_IN = (3.5, 16.0)  # real-world wall thickness range, inches
DOOR_RADIUS_FT = (2.0, 4.5)  # real-world door leaf width range, feet
ROOM_AREA_SQFT = (20.0, 20000.0)
ROOM_WORDS = re.compile(
    r"^(room|hall|hallway|office|corridor|lobby|storage|kitchen|toilet|restroom|"
    r"bath|bathroom|bedroom|living|classroom|conference|mechanical|vestibule|"
    r"reception|closet|laundry|warehouse|workshop|gym|cafeteria)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- walls
def _normalise(seg: Segment):
    x0, y0, x1, y1 = seg
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length == 0:
        return None
    ux, uy = dx / length, dy / length
    if ux < 0 or (abs(ux) < 1e-9 and uy < 0):
        ux, uy = -ux, -uy
    nx, ny = -uy, ux
    offset = x0 * nx + y0 * ny
    t0 = x0 * ux + y0 * uy
    t1 = x1 * ux + y1 * uy
    return math.degrees(math.atan2(uy, ux)), offset, min(t0, t1), max(t0, t1)


def _free(used: List[Tuple[float, float]], a: float, b: float) -> bool:
    return all(b <= s + 1e-6 or a >= e - 1e-6 for s, e in used)


def paired_wall_length(
    segments: Sequence[Segment], scale_factor: float, min_overlap_pt: float = 6.0
) -> Tuple[float, int]:
    """Centreline length (paper points) of double-line walls, and pair count.

    Two parallel straight segments whose spacing equals a plausible wall
    thickness at this scale, and which overlap along their length, form a wall.
    """
    min_gap = WALL_THICKNESS_IN[0] / scale_factor * 72.0
    max_gap = WALL_THICKNESS_IN[1] / scale_factor * 72.0
    groups = {}
    for idx, seg in enumerate(segments):
        norm = _normalise(seg)
        if norm is None:
            continue
        angle, offset, t0, t1 = norm
        groups.setdefault(round(angle / 0.5) % 360, []).append((offset, t0, t1, idx))

    candidates = []
    for items in groups.values():
        items.sort()
        for i, (o_i, a0, a1, id_i) in enumerate(items):
            for o_j, b0, b1, id_j in items[i + 1 :]:
                gap = o_j - o_i
                if gap > max_gap:
                    break
                if gap < min_gap:
                    continue
                lo, hi = max(a0, b0), min(a1, b1)
                if hi - lo >= min_overlap_pt:
                    candidates.append((gap, lo, hi, id_i, id_j))
    candidates.sort()
    used = {}
    total, pairs = 0.0, 0
    for gap, lo, hi, id_i, id_j in candidates:
        if _free(used.setdefault(id_i, []), lo, hi) and _free(
            used.setdefault(id_j, []), lo, hi
        ):
            used[id_i].append((lo, hi))
            used[id_j].append((lo, hi))
            total += hi - lo
            pairs += 1
    return total, pairs


# ---------------------------------------------------------------- doors
def door_swing_count(
    curve_paths: Sequence[Sequence[tuple]], scale_factor: Optional[float]
):
    """Count quarter-circle arcs (door swings).

    Returns (candidates, confirmed). `confirmed` additionally requires the arc
    radius to be a plausible door leaf width at the given scale (needs a scale).
    """
    candidates = confirmed = 0
    for path in curve_paths:
        for sp in subpaths(path):
            if sp.straight or sp.closed:
                continue
            x0, y0, x1, y1 = bbox(sp.points)
            w, h = x1 - x0, y1 - y0
            if max(w, h) <= 0 or abs(w - h) / max(w, h) > 0.15:
                continue
            chord = math.dist(sp.points[0], sp.points[-1])
            if chord <= 0:
                continue
            ratio = polyline_length(sp.points) / chord
            if not 1.04 <= ratio <= 1.18:  # 90 degree arc is ~1.11
                continue
            candidates += 1
            if scale_factor:
                radius_ft = max(w, h) * scale_factor / POINTS_PER_FOOT
                if DOOR_RADIUS_FT[0] <= radius_ft <= DOOR_RADIUS_FT[1]:
                    confirmed += 1
    return candidates, confirmed


# ---------------------------------------------------------------- rooms
@dataclass
class Room:
    label: str
    area_sqft: float


def room_areas(
    polygons: Sequence[List[Tuple[float, float]]],
    words: Sequence[dict],
    scale_factor: float,
) -> List[Room]:
    """Area of the smallest closed polygon around each room-name label."""
    rooms: List[Room] = []
    used = set()
    for word in words:
        if not ROOM_WORDS.match(word["text"]):
            continue
        centre = ((word["x0"] + word["x1"]) / 2, (word["top"] + word["bottom"]) / 2)
        best = None
        for idx, poly in enumerate(polygons):
            if idx in used or not point_in_polygon(centre, poly):
                continue
            sqft = polygon_area(poly) * scale_factor**2 / POINTS_PER_FOOT**2
            if ROOM_AREA_SQFT[0] <= sqft <= ROOM_AREA_SQFT[1] and (
                best is None or sqft < best[1]
            ):
                best = (idx, sqft)
        if best is not None:
            used.add(best[0])
            rooms.append(Room(word["text"], best[1]))
    return rooms


def page_polygons(page) -> List[List[Tuple[float, float]]]:
    polys = []
    for cv in page.curves:
        for sp in subpaths(cv["path"]):
            if sp.closed and len(sp.points) >= 3:
                polys.append(sp.points)
    for r in page.rects:
        polys.append(
            [
                (r["x0"], r["top"]),
                (r["x1"], r["top"]),
                (r["x1"], r["bottom"]),
                (r["x0"], r["bottom"]),
            ]
        )
    return polys
