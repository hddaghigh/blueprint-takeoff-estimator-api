"""Parse construction dimension strings into feet.

Handles feet-inches (12'-6 1/2", 12' - 6", 12 ft 6 in), bare feet and inches
(30 ft, 8-inch, 36 in.), and metric (3600 mm, 3.6 m, 120 cm).
"""

import re
from dataclasses import dataclass
from typing import List, Optional

MM_TO_FT = 1 / 304.8
CM_TO_FT = 1 / 30.48
M_TO_FT = 1 / 0.3048

_NUM = r"\d+(?:\.\d+)?"

_APOS_RE = re.compile(rf"(?<![\w.,'/-])(?P<ft>{_NUM})'(?!')")
# after "12'" : "-6", "-6 1/2", "-6\"" , or an inch value that carries a quote mark
_APOS_TAIL_DASH_RE = re.compile(
    rf"\s*-\s*(?P<in>{_NUM})(?:[\s-]+(?P<fn>\d+)/(?P<fd>\d+))?\s*(?:\"|'')?"
)
_APOS_TAIL_QUOTED_RE = re.compile(
    rf"\s*(?P<in>{_NUM})(?:\s+(?P<fn>\d+)/(?P<fd>\d+))?\s*(?:\"|'')"
)
_FT_WORD_RE = re.compile(
    rf"(?<![\w.,'/-])(?P<ft>{_NUM})\s*(?:ft\b\.?|feet\b|foot\b)"
    rf"(?:\s*(?P<in>{_NUM})(?:\s+(?P<fn>\d+)/(?P<fd>\d+))?\s*(?:in\b\.?|inch(?:es)?\b))?",
    re.IGNORECASE,
)
_INCH_RE = re.compile(
    r"(?<![\w.,'/-])(?:(?P<w>\d+)\s+(?P<fn>\d+)/(?P<fd>\d+)|(?P<n>\d+)/(?P<d>\d+)"
    rf"|(?P<dec>{_NUM}))\s*(?:\"|''|-?inch(?:es)?\b|in\b\.?)",
    re.IGNORECASE,
)
_METRIC_RE = re.compile(
    r"(?<![\w.,'/-])(?P<v>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<u>mm|cm|meters?|metres?|m)\b(?!\s*[²2])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Dimension:
    feet: float
    raw: str
    kind: str  # "feet" | "inch" | "metric"
    start: int
    end: int


def _frac(whole: Optional[str], num: Optional[str], den: Optional[str]) -> float:
    value = float(whole) if whole else 0.0
    if num and den and float(den):
        value += float(num) / float(den)
    return value


def _find_all(text: str) -> List[Dimension]:
    found: List[Dimension] = []

    for m in _APOS_RE.finditer(text):
        feet = float(m.group("ft"))
        end = m.end()
        tail = _APOS_TAIL_DASH_RE.match(text, end) or _APOS_TAIL_QUOTED_RE.match(
            text, end
        )
        if tail:
            feet += _frac(tail.group("in"), tail.group("fn"), tail.group("fd")) / 12
            end = tail.end()
        found.append(Dimension(feet, text[m.start() : end], "feet", m.start(), end))

    for m in _FT_WORD_RE.finditer(text):
        feet = float(m.group("ft"))
        if m.group("in"):
            feet += _frac(m.group("in"), m.group("fn"), m.group("fd")) / 12
        found.append(Dimension(feet, m.group(0), "feet", m.start(), m.end()))

    for m in _INCH_RE.finditer(text):
        if m.group("w"):
            inches = _frac(m.group("w"), m.group("fn"), m.group("fd"))
        elif m.group("n"):
            inches = _frac(None, m.group("n"), m.group("d"))
        else:
            inches = float(m.group("dec"))
        found.append(Dimension(inches / 12, m.group(0), "inch", m.start(), m.end()))

    for m in _METRIC_RE.finditer(text):
        value = float(m.group("v").replace(",", ""))
        unit = m.group("u").lower()
        factor = (
            MM_TO_FT
            if unit == "mm"
            else CM_TO_FT if unit == "cm" else M_TO_FT  # m / meter / metre
        )
        found.append(
            Dimension(value * factor, m.group(0), "metric", m.start(), m.end())
        )
    return found


def find_dimensions(text: str) -> List[Dimension]:
    """All non-overlapping dimension strings in `text`, in reading order.

    When two patterns overlap (e.g. 12'-6" also contains 6"), the longer wins.
    """
    ordered = sorted(_find_all(text), key=lambda d: (d.start, -(d.end - d.start)))
    kept: List[Dimension] = []
    for dim in ordered:
        if kept and dim.start < kept[-1].end:
            continue
        kept.append(dim)
    return kept


def parse_length_to_feet(text: str) -> Optional[float]:
    """Feet value of the first dimension in `text`, or None."""
    dims = find_dimensions(text)
    return dims[0].feet if dims else None
