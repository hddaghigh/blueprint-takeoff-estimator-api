"""Parse drawing scale notes (SCALE: 1/4" = 1'-0", 1" = 20', 1:50).

A Scale's `factor` converts paper length to real length:
    real_length = paper_length * factor
so 1/4" = 1'-0" (0.25 in on paper = 12 in real) has factor 48.
"""

import re
from dataclasses import dataclass
from typing import List

_NUM = r"\d+(?:\.\d+)?"
_PAPER = rf"(?P<paper>\d+\s+\d+/\d+|\d+/\d+|{_NUM}|\.\d+)"
_INCH = r"(?:\"|''|in\b\.?|inch(?:es)?\b)"
_FOOT = r"(?:'|ft\b\.?|feet\b|foot\b)"

_IMPERIAL_RE = re.compile(
    rf"(?<![\w./])" + _PAPER + rf"\s*{_INCH}\s*=\s*(?P<rft>{_NUM})\s*{_FOOT}"
    rf"(?:\s*-?\s*(?P<rin>{_NUM})\s*(?:\"|''|in\b)?)?",
    re.IGNORECASE,
)
_RATIO_RE = re.compile(rf"scale\b[^\n\d]{{0,12}}1\s*:\s*(?P<r>{_NUM})\b", re.IGNORECASE)
_NTS_RE = re.compile(r"\b(?:N\.?T\.?S\.?|not\s+to\s+scale)(?![\w])", re.IGNORECASE)


@dataclass(frozen=True)
class Scale:
    factor: float
    label: str


def _paper_inches(text: str) -> float:
    total = 0.0
    for part in text.split():
        if "/" in part:
            num, den = part.split("/")
            total += float(num) / float(den)
        else:
            total += float(part)
    return total


def find_scales(text: str) -> List[Scale]:
    """Every scale note in `text` (duplicates removed, reading order)."""
    scales: List[Scale] = []
    for m in _IMPERIAL_RE.finditer(text):
        paper = _paper_inches(m.group("paper"))
        real_inches = float(m.group("rft")) * 12 + float(m.group("rin") or 0)
        if paper > 0 and real_inches > 0:
            scales.append(Scale(real_inches / paper, m.group(0).strip()))
    for m in _RATIO_RE.finditer(text):
        ratio = float(m.group("r"))
        if ratio > 0:
            scales.append(Scale(ratio, f"1:{m.group('r')}"))
    unique, seen = [], set()
    for s in scales:
        key = round(s.factor, 6)
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def scale_spans(text: str) -> List[tuple]:
    """(start, end) of every scale note, so callers can keep them out of quantities.

    The right-hand side of `1/4" = 1'-0"` is a length, but it is a drawing
    scale, never a wall, so it must not be read as a quantity.
    """
    spans = [m.span() for m in _IMPERIAL_RE.finditer(text)]
    spans += [m.span() for m in _RATIO_RE.finditer(text)]
    return spans


def is_not_to_scale(text: str) -> bool:
    return bool(_NTS_RE.search(text))
