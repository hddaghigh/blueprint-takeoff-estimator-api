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
    ],
)
def test_extract_quantities(text, expected):
    assert extract_quantities_from_text(text) == expected
