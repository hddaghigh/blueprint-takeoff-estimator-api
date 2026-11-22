"""Unit tests for exact path geometry (no PDF needed)."""

import math

import pytest

from src.geometry.paths import (
    bezier_points,
    point_in_polygon,
    polygon_area,
    polyline_length,
    subpaths,
)

K = 0.5522847498307936  # cubic Bezier control-point factor for a quarter circle


def quarter_circle_path(r):
    return [("m", (r, 0)), ("c", (r, K * r), (K * r, r), (0, r))]


def test_quarter_circle_bezier_length_is_exact():
    (sp,) = subpaths(quarter_circle_path(54.0))

    assert not sp.straight and not sp.closed
    assert polyline_length(sp.points) == pytest.approx(math.pi * 54 / 2, rel=1e-4)
    # the chord (what pdfplumber's endpoint-only `pts` would give) is ~10% short
    assert math.dist(sp.points[0], sp.points[-1]) < polyline_length(sp.points) * 0.93


def test_bezier_starts_after_p0_and_ends_at_p3():
    pts = bezier_points((0, 0), (0, 1), (1, 1), (1, 0), steps=4)

    assert len(pts) == 4 and pts[-1] == (1, 0)


def test_closed_and_open_subpaths():
    triangle = [("m", (0, 0)), ("l", (4, 0)), ("l", (4, 3)), ("h",)]
    open_line = [("m", (0, 0)), ("l", (4, 0)), ("l", (4, 3))]
    repeated_end = [("m", (0, 0)), ("l", (4, 0)), ("l", (4, 3)), ("l", (0, 0))]

    assert subpaths(triangle)[0].closed and subpaths(triangle)[0].straight
    assert not subpaths(open_line)[0].closed
    assert subpaths(repeated_end)[0].closed
    assert polyline_length(subpaths(triangle)[0].points, True) == pytest.approx(12.0)
    assert polyline_length(subpaths(open_line)[0].points) == pytest.approx(7.0)


def test_multiple_subpaths_split_at_each_moveto():
    path = [("m", (0, 0)), ("l", (1, 0)), ("m", (5, 5)), ("l", (5, 8))]

    assert [polyline_length(sp.points) for sp in subpaths(path)] == [1.0, 3.0]


def test_polygon_area_and_containment():
    rect = [(0, 0), (20, 0), (20, 15), (0, 15)]
    l_shape = [(0, 0), (10, 0), (10, 5), (5, 5), (5, 10), (0, 10)]

    assert polygon_area(rect) == 300.0
    assert polygon_area(rect + [rect[0]]) == 300.0  # repeated closing point
    assert polygon_area(l_shape) == 75.0
    assert polygon_area([(0, 0), (1, 1)]) == 0.0
    assert point_in_polygon((10, 7), rect) and not point_in_polygon((25, 7), rect)
    assert point_in_polygon((2, 2), l_shape) and not point_in_polygon((8, 8), l_shape)
