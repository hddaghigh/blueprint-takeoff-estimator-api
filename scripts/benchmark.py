"""Accuracy benchmark: precision / recall per category over ground-truth cases.

    python -m scripts.benchmark

Every case is a document generated here with known true quantities (text,
vector PDFs with a scale note, a door-schedule PDF, DXF drawings). Each is run
through the real pipeline; an item counts as correct if it is within 2% of the
truth. Precision = correct / returned; recall = correct / expected.
This measures the synthetic suite only: re-run it on your own labelled
drawings (add cases below) before trusting results on real projects.
"""

import io
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import ezdxf
from ezdxf import units
from fastapi import HTTPException
from reportlab.pdfgen import canvas

from src import cad
from src.pdf_scan import scan_pdf
from src.pipelines import process_text
from src.findings import build_items

TOLERANCE = 0.02
SCALE_NOTE = 'SCALE: 1/4" = 1\'-0"'  # 360 pt = 20 ft, 9 pt = 6 in, 54 pt = 3 ft


def pdf(draw: Callable, size=(1000, 800)) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=size)
    draw(c)
    c.showPage()
    c.save()
    return buf.getvalue()


def dxf(build: Callable, unit=units.FT) -> bytes:
    doc = ezdxf.new("R2018")
    doc.units = unit
    for name in ("A-WALL", "A-DOOR", "A-FLOR", "A-ANNO"):
        doc.layers.add(name)
    build(doc, doc.modelspace())
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "b.dxf"
        doc.saveas(path)
        return path.read_bytes()


def text_case(text: str):
    return lambda: {
        i.category: i.quantity for i in process_text(text.encode(), "t").items
    }


def pdf_case(data: bytes):
    return lambda: {
        i.category: i.quantity for i in build_items(scan_pdf(data).findings)
    }


def cad_case(data: bytes):
    return lambda: {
        i.category: i.quantity for i in cad.process_cad(data, "b.dxf").items
    }


def door_schedule(c):
    c.drawString(100, 640, "DOOR SCHEDULE")
    xs, ys = [100, 200, 300, 400], [600, 570, 540, 510, 480]
    for y in ys:
        c.line(xs[0], y, xs[-1], y)
    for x in xs:
        c.line(x, ys[0], x, ys[-1])
    for r, row in enumerate(
        [
            ["Door No.", "Type", "Qty"],
            ["D1", "A", "2"],
            ["D2", "B", "3"],
            ["D3", "A", "1"],
        ]
    ):
        for col, cell in enumerate(row):
            c.drawString(xs[col] + 8, ys[r] - 20, cell)


def double_walls(c):
    c.drawString(50, 40, SCALE_NOTE)
    c.line(100, 500, 460, 500)
    c.line(100, 509, 460, 509)
    c.line(600, 300, 600, 540)
    c.line(609, 300, 609, 540)


def room_and_door(c):
    c.drawString(50, 40, SCALE_NOTE)
    c.rect(100, 100, 360, 270)
    c.drawString(200, 200, "OFFICE 101")
    c.arc(500, 300, 608, 408, 0, 90)


def cad_full(doc, msp):
    for a, b in [
        ((0, 0), (10, 0)),
        ((10, 0), (10, 10)),
        ((10, 10), (0, 10)),
        ((0, 10), (0, 0)),
    ]:
        msp.add_line(a, b, dxfattribs={"layer": "A-WALL"})
    doc.blocks.new("DOOR-36")
    for x in (1, 4, 7):
        msp.add_blockref("DOOR-36", (x, 0), dxfattribs={"layer": "A-DOOR"})
    msp.add_lwpolyline(
        [(0, 0), (20, 0), (20, 15), (0, 15)], close=True, dxfattribs={"layer": "A-FLOR"}
    )


CASES: List[Tuple[str, Callable[[], Dict[str, float]], Dict[str, float]]] = [
    (
        "text: scope",
        text_case(
            "Install 1,500 sq ft of flooring. Erect 120 linear feet of walls. Fit 4 doors."
        ),
        {"flooring": 1500, "walls": 120, "doors": 4},
    ),
    (
        "text: dimension traps",
        text_case(
            "Erect 8-inch CMU walls, 120 linear feet total. Install four doors 3'-0\" x 7'-0\"."
        ),
        {"walls": 120, "doors": 4},
    ),
    (
        "text: metric",
        text_case("Build a 3.048 m wall. Lay 100 m2 of flooring."),
        {"walls": 10, "flooring": 1076.39},
    ),
    (
        "text: part codes ignored",
        text_case("Spec 08-1100-0045 doors, see detail A-101."),
        {},
    ),
    (
        "pdf: text callouts",
        pdf_case(
            pdf(
                lambda c: c.drawString(
                    72, 700, "Install 4 doors and 120 linear feet of walls."
                )
            )
        ),
        {"doors": 4, "walls": 120},
    ),
    ("pdf: door schedule", pdf_case(pdf(door_schedule)), {"doors": 6}),
    ("pdf: scaled double-line walls", pdf_case(pdf(double_walls)), {"walls": 33.33}),
    (
        "pdf: scaled room + door swing",
        pdf_case(pdf(room_and_door)),
        {"flooring": 300, "doors": 1},
    ),
    (
        "pdf: no scale, no callouts",
        pdf_case(pdf(lambda c: c.rect(100, 100, 360, 270))),
        {},
    ),
    (
        "dxf: layers + blocks",
        cad_case(dxf(cad_full)),
        {"walls": 40, "doors": 3, "flooring": 300},
    ),
    (
        "dxf: millimetres",
        cad_case(
            dxf(
                lambda d, m: m.add_line(
                    (0, 0), (3048, 0), dxfattribs={"layer": "A-WALL"}
                ),
                units.MM,
            )
        ),
        {"walls": 10},
    ),
]


def run() -> dict:
    stats = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    rows = []
    for name, produce, expected in CASES:
        try:
            got = produce()
        except HTTPException:  # "nothing found" is an empty result, not a crash
            got = {}
        for cat in set(got) | set(expected):
            ok = (
                cat in got
                and cat in expected
                and abs(got[cat] - expected[cat]) <= TOLERANCE * expected[cat]
            )
            if ok:
                stats[cat]["tp"] += 1
            else:
                stats[cat]["fp"] += cat in got
                stats[cat]["fn"] += cat in expected
        rows.append((name, expected, got))

    def ratio(a, b):
        return 1.0 if b == 0 else a / b

    summary = {
        cat: {
            "precision": ratio(s["tp"], s["tp"] + s["fp"]),
            "recall": ratio(s["tp"], s["tp"] + s["fn"]),
            **s,
        }
        for cat, s in sorted(stats.items())
    }
    return {"cases": rows, "summary": summary}


def main() -> None:
    result = run()
    for name, expected, got in result["cases"]:
        flag = (
            "ok "
            if all(
                c in got and abs(got[c] - v) <= TOLERANCE * v
                for c, v in expected.items()
            )
            and set(got) <= set(expected)
            else "BAD"
        )
        print(
            f"[{flag}] {name:34s} expected={expected} got={ {k: round(v, 2) for k, v in got.items()} }"
        )
    print()
    for cat, s in result["summary"].items():
        print(
            f"{cat:9s} precision={s['precision']:.2f} recall={s['recall']:.2f}  (tp={s['tp']} fp={s['fp']} fn={s['fn']})"
        )


if __name__ == "__main__":
    main()
