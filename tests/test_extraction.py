"""Unit tests for unit-aware quantity extraction (pure text, no I/O)."""

import pytest

from src.extraction import extract_quantities_from_text


@pytest.mark.parametrize(
    "text, expected",
    [
        # thousands separator must not truncate to "1"
        ("Install 1,500 sq ft of vinyl tile flooring.", {"flooring": 1500}),
        # "8-inch" is a dimension, not the wall quantity
        ("Erect 8-inch CMU walls, 120 linear feet total.", {"walls": 120}),
        # two keywords in one sentence must each get their own number
        ("Install 4 doors and 120 linear feet of walls.", {"doors": 4, "walls": 120}),
        # singular forms
        ("Build 30 linear feet of wall along gridline A.", {"walls": 30}),
        ("Provide 1 door at the entry.", {"doors": 1}),
        # number words
        ("Provide four doors at the entry.", {"doors": 4}),
        # door sizes are dimensions, not counts
        ("Install 4 doors (36 in. wide, 3'-0\" x 7'-0\") each.", {"doors": 4}),
        # CWICR / sheet codes are not quantities
        ("Spec 08-1100-0045 doors, see detail A-101.", {}),
        # unit spellings and PDF line wrapping
        ("Install flooring across a 1500 SF hall.", {"flooring": 1500}),
        (
            "Install vinyl tile\nflooring across 2,250.5 sq. ft.\nof corridor.",
            {"flooring": 2250.5},
        ),
        # bullet lists without sentence punctuation
        (
            "- 4 doors\n- 120 LF walls\n- 800 sq ft flooring",
            {"doors": 4, "walls": 120, "flooring": 800},
        ),
        # look-alike words must not match
        ("Drywall and doorways are excluded.", {}),
        ("", {}),
        # drawing labels without full stops must not merge with the next line:
        # the scale note's 1'-0" is not a wall, and "CORRIDOR" is not a sentence start
        ('SCALE: 1/4" = 1\'-0"\nErect 8-inch CMU walls.', {}),
        ("SCALE 1:50\nInstall 4 doors.", {"doors": 4}),
        ('SCALE: 1/4"= 1\'-0"\nErect 8-inch CMU walls.\nFit 4 doors.', {"doors": 4}),
        ("CORRIDOR 12'-6\" x 8'-0\"\nErect 8-inch CMU walls.", {}),
        (
            "Erect 120 linear feet\nOf walls",
            {},
        ),  # new capitalised line, no keyword pair
        # genuine wraps still join: lowercase / digit / connector continuation
        ("Install flooring across\n1500 SF hall.", {"flooring": 1500}),
        ("Erect 120 linear feet of\nWalls along grid A.", {"walls": 120}),
        ("Erect 8-inch CMU walls,\n120 linear feet total.", {"walls": 120}),
        # feet-inches and metric lengths/areas are converted to feet / sq ft
        ("Erect a wall 12'-6\" long.", {"walls": 12.5}),
        ("Build 30 ft of wall.", {"walls": 30}),
        ("Build a 3.048 m wall.", {"walls": 10.0}),
        ("Lay 100 m2 of flooring.", {"flooring": pytest.approx(1076.39)}),
        ("Install 4 doors 3'-0\" x 7'-0\" with 36 in. frames.", {"doors": 4}),
    ],
)
def test_extract_quantities(text, expected):
    assert extract_quantities_from_text(text) == expected
