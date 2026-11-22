"""OCR text repair, box merging and engine selection (pure logic, no OCR run)."""

import pytest

from src.extraction import extract_quantities_from_text
from src.ocr import engine as ocr
from src.ocr.normalize import unglue
from src.ocr.rapid import Line, _spans, merge_lines


@pytest.mark.parametrize(
    "glued, fixed",
    [
        (
            "Install 4doors and120linear feetofwalls.",
            "Install 4 doors and 120 linear feet of walls.",
        ),
        (
            "Provide 1,500sqft of vinyl tile flooring.",
            "Provide 1,500 sqft of vinyl tile flooring.",
        ),
        ("Erect8-inchCMUwalls.", "Erect 8-inch CMU walls."),
        ("Provide 4 HMdoors.", "Provide 4 HM doors."),
        ("OFFICE101 18'-4\"", "OFFICE 101 18'-4\""),
        # must be left alone
        ("Door D1 3'-0\" x 7'-0\"", "Door D1 3'-0\" x 7'-0\""),
        ("Spec 08-1100-0045 doors, see A101", "Spec 08-1100-0045 doors, see A101"),
        ("Total area 100m2 of flooring", "Total area 100m2 of flooring"),
        ("drywall and doorways", "drywall and doorways"),
        ("iPhone and McDonald", "iPhone and McDonald"),
        (
            "Install 4 doors and 120 linear feet of walls.",
            "Install 4 doors and 120 linear feet of walls.",
        ),
    ],
)
def test_unglue(glued, fixed):
    assert unglue(glued) == fixed


def test_glued_ocr_text_yields_the_right_quantities_after_repair():
    ocr_text = "Install 4doors and120linear feetofwalls.\nProvide 1,500sqft of vinyl tile flooring."

    assert extract_quantities_from_text(ocr_text) != {
        "doors": 4,
        "walls": 120,
        "flooring": 1500,
    }
    assert extract_quantities_from_text(unglue(ocr_text)) == {
        "doors": 4,
        "walls": 120,
        "flooring": 1500,
    }


def line(x0, y0, x1, y1, text, conf=0.9):
    return Line(x0, y0, x1, y1, text, conf)


def test_merge_keeps_the_longest_text_for_overlapping_boxes():
    clipped = line(100, 100, 900, 140, "Install 4 doors and 120 linear feet of v", 0.97)
    whole = line(
        100, 102, 1000, 142, "Install 4 doors and 120 linear feet of walls.", 0.90
    )
    other = line(100, 300, 800, 340, "CORRIDOR 12'-6\"", 0.95)

    merged = merge_lines([clipped, other, whole])

    assert [l.text for l in merged] == [
        "Install 4 doors and 120 linear feet of walls.",
        "CORRIDOR 12'-6\"",
    ]


def test_merge_keeps_neighbouring_lines_that_do_not_overlap():
    a = line(100, 100, 900, 140, "first")
    b = line(100, 150, 900, 190, "second")  # directly below, touching rows

    assert [l.text for l in merge_lines([b, a])] == ["first", "second"]


def test_bands_overlap_and_cover_the_page():
    assert _spans(1000) == []  # small pages are read in one pass
    spans = _spans(3300)

    assert spans[0][0] == 0 and spans[-1][1] == 3300
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        assert b0 < a1  # consecutive bands overlap, so no line is cut in two


# ------------------------------------------------------------ engine selection
class Dummy:
    name = "dummy"


@pytest.mark.parametrize(
    "mode, rapid, tess, expected",
    [
        ("auto", Dummy(), Dummy(), "rapid"),
        ("auto", None, Dummy(), "tess"),
        ("auto", None, None, None),
        ("rapidocr", Dummy(), Dummy(), "rapid"),
        ("tesseract", Dummy(), Dummy(), "tess"),
        ("tesseract", Dummy(), None, None),  # explicit choice is never silently swapped
        ("none", Dummy(), Dummy(), None),
    ],
)
def test_engine_selection(monkeypatch, mode, rapid, tess, expected):
    r, t = object() if rapid else None, object() if tess else None
    monkeypatch.setattr(ocr, "_make_rapidocr", lambda: r)
    monkeypatch.setattr(ocr, "_make_tesseract", lambda: t)

    chosen = ocr._build_engine(mode)

    assert chosen is {"rapid": r, "tess": t, None: None}[expected]


def test_real_rapidocr_engine_loads_without_kwargs_state_leak():
    """The engine must not pass per-call kwargs (RapidOCR keeps them on the instance)."""
    import inspect

    from src.ocr import rapid

    source = inspect.getsource(rapid.RapidOcrEngine)
    for forbidden in ("unclip_ratio=", "box_thresh=", "text_score="):
        assert forbidden not in source
