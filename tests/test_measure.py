"""Unit tests for dimension and scale parsing (pure text)."""

import pytest

from src.measure.dimensions import find_dimensions, parse_length_to_feet
from src.measure.scale import find_scales, is_not_to_scale


@pytest.mark.parametrize(
    "text, feet",
    [
        ("12'-6\"", 12.5),
        ("12'-6 1/2\"", 12 + 6.5 / 12),
        ("12' - 6\"", 12.5),
        ("12'-0\"", 12.0),
        ("12'", 12.0),
        ("30 ft", 30.0),
        ("12 ft 6 in", 12.5),
        ("8-inch", 8 / 12),
        ("36 in.", 3.0),
        ('6"', 0.5),
        ('1 1/2"', 1.5 / 12),
        ("3600 mm", 3600 / 304.8),
        ("3.6 m", 3.6 / 0.3048),
        ("120 cm", 120 / 30.48),
    ],
)
def test_parse_length_to_feet(text, feet):
    assert parse_length_to_feet(text) == pytest.approx(feet, rel=1e-6)


def test_find_dimensions_keeps_compound_and_ignores_plain_numbers():
    dims = find_dimensions(
        "Door 3'-0\" x 7'-0\", 4 doors, room 12'-6\" wide, 1,500 sq ft"
    )
    assert [round(d.feet, 3) for d in dims] == [3.0, 7.0, 12.5]


def test_area_units_are_not_dimensions():
    assert find_dimensions("250 m2 of floor, 1500 sq ft, 40 m²") == []


@pytest.mark.parametrize(
    "text, factor",
    [
        ('SCALE: 1/4" = 1\'-0"', 48.0),
        ('1/8" = 1\'-0"', 96.0),
        ('3/16" = 1\'-0"', 64.0),
        ('1" = 1\'-0"', 12.0),
        ('1 1/2" = 1\'-0"', 8.0),
        ("1\" = 20'", 240.0),
        ('1" = 20\'-0"', 240.0),
        ("SCALE 1:50", 50.0),
        ("Scale: 1 : 100", 100.0),
    ],
)
def test_find_scales(text, factor):
    scales = find_scales(text)
    assert len(scales) == 1
    assert scales[0].factor == pytest.approx(factor)


def test_scale_edge_cases():
    assert find_scales("Install 4 doors at 1:30 pm") == []  # time, not a scale
    assert find_scales("no scale note here") == []
    both = find_scales('PLAN 1/4" = 1\'-0"  DETAIL 1" = 1\'-0"')
    assert sorted(s.factor for s in both) == [12.0, 48.0]
    assert is_not_to_scale("DETAIL - NOT TO SCALE")
    assert is_not_to_scale("NTS")
    assert not is_not_to_scale("Install 4 doors")
