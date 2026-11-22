"""Exact path geometry for pdfplumber curve/line/rect objects (page points)."""

import math
from dataclasses import dataclass, field
from typing import Iterable, List, Tuple

Point = Tuple[float, float]
Segment = Tuple[float, float, float, float]


@dataclass
class SubPath:
    points: List[Point]
    closed: bool = False
    straight: bool = True  # False once a Bezier segment was added


def bezier_points(p0: Point, p1: Point, p2: Point, p3: Point, steps: int = 24):
    """Points along a cubic Bezier, excluding p0, including p3."""
    pts = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1 - t
        pts.append(
            (
                mt**3 * p0[0]
                + 3 * mt * mt * t * p1[0]
                + 3 * mt * t * t * p2[0]
                + t**3 * p3[0],
                mt**3 * p0[1]
                + 3 * mt * mt * t * p1[1]
                + 3 * mt * t * t * p2[1]
                + t**3 * p3[1],
            )
        )
    return pts


def subpaths(path: Iterable[tuple]) -> List[SubPath]:
    """Split a pdfplumber `path` command list (m/l/c/h) into sampled sub-paths."""
    result: List[SubPath] = []
    current = None
    for command in path:
        op = command[0]
        if op == "m":
            if current is not None and len(current.points) > 1:
                result.append(current)
            current = SubPath([command[1]])
        elif current is None:
            continue
        elif op == "l":
            current.points.append(command[1])
        elif op == "c":
            current.points.extend(
                bezier_points(current.points[-1], command[1], command[2], command[3])
            )
            current.straight = False
        elif op == "h":
            current.closed = True
    if current is not None and len(current.points) > 1:
        result.append(current)
    for sp in result:
        if len(sp.points) > 2 and sp.points[0] == sp.points[-1]:
            sp.closed = True
    return result


def polyline_length(points: List[Point], closed: bool = False) -> float:
    total = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    if closed and points and points[0] != points[-1]:
        total += math.dist(points[-1], points[0])
    return total


def polygon_area(points: List[Point]) -> float:
    """Absolute shoelace area; a repeated closing point is tolerated."""
    pts = points[:-1] if len(points) > 1 and points[0] == points[-1] else points
    if len(pts) < 3:
        return 0.0
    twice = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))
    return abs(twice) / 2


def point_in_polygon(point: Point, polygon: List[Point]) -> bool:
    x, y = point
    inside = False
    n = len(polygon)
    for i in range(n):
        x0, y0 = polygon[i]
        x1, y1 = polygon[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def bbox(points: List[Point]):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def page_segments(page, cap: int = 20000) -> Tuple[List[Segment], bool]:
    """Straight segments (lines, straight polylines, rect edges) in top-origin points.

    Returns (segments, truncated). Curved sub-paths are excluded.
    """
    segs: List[Segment] = []

    def add(a: Point, b: Point) -> bool:
        if len(segs) >= cap:
            return False
        if a != b:
            segs.append((a[0], a[1], b[0], b[1]))
        return True

    for ln in page.lines:
        pts = ln["pts"]
        if not add(pts[0], pts[-1]):
            return segs, True
    for cv in page.curves:
        for sp in subpaths(cv["path"]):
            if not sp.straight:
                continue
            pts = sp.points + (
                [sp.points[0]] if sp.closed and sp.points[0] != sp.points[-1] else []
            )
            for a, b in zip(pts, pts[1:]):
                if not add(a, b):
                    return segs, True
    for r in page.rects:
        x0, x1, t, b = r["x0"], r["x1"], r["top"], r["bottom"]
        corners = [(x0, t), (x1, t), (x1, b), (x0, b), (x0, t)]
        for a, c in zip(corners, corners[1:]):
            if not add(a, c):
                return segs, True
    return segs, False
