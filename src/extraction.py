"""Unit-aware quantity extraction shared by the text and PDF pipelines.

A takeoff quantity is only accepted when the number's unit is compatible with
the category it is paired with:

    walls    -> a length   ("120 linear feet", "120 LF", "30 ft")
    flooring -> an area    ("1,500 sq ft", "1500 SF", "200 square feet")
    doors    -> a count    ("4", "four", "4 ea")

Dimensions such as "8-inch" or 3'-0" are never mistaken for quantities, and
part/code numbers such as "08-1100-0045" or "D-4" are ignored.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.measure.dimensions import find_dimensions
from src.measure.scale import scale_spans

CATEGORY_KEYWORDS = {
    "walls": re.compile(r"\bwalls?\b", re.IGNORECASE),
    "doors": re.compile(r"\bdoors?\b", re.IGNORECASE),
    "flooring": re.compile(r"\bflooring\b|\bfloor\s+area\b", re.IGNORECASE),
}

# Which unit class each category accepts as its quantity.
CATEGORY_UNIT_CLASS = {"walls": "length", "flooring": "area", "doors": "count"}

# Never pair a number with a keyword if it is further away than this.
MAX_PAIR_DISTANCE = 120

_NUMBER_WORDS = {
    word: value
    for value, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve "
        "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}

SQM_TO_SQFT = 10.7639

_UNIT_PATTERNS = [
    # order matters: area must be tried before length ("sq ft" before "ft")
    ("area", r"sq\.?\s*ft\.?|sqft|sf\b|sq\.?\s*feet|square\s+f(?:ee|oo)t"),
    ("area_m", r"m2\b|m²|sq\.?\s*m\b|square\s+met(?:er|re)s?"),
    (
        "length",
        r"linear\s+f(?:ee|oo)t|lin\.?\s*ft\.?|l\.?f\.?\b|feet\b|foot\b|ft\b\.?|'",
    ),
    ("dimension", r"-?\s*inch(?:es)?\b|in\b\.?|\""),
    ("count", r"ea\b\.?|each\b|nos?\b\.?|pcs?\b\.?"),
]
_UNIT_RE = re.compile(
    "|".join(f"(?P<{name}>{pat})" for name, pat in _UNIT_PATTERNS), re.IGNORECASE
)

_NUMBER_TOKEN_RE = re.compile(
    r"(?<![\w.,])(?<!\w-)"  # not glued to a word, decimal or part code (A-101)
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
    r"|(?P<word>" + "|".join(_NUMBER_WORDS) + r")\b)"
    r"(?!-?\d)(?!\w)",  # not followed by more digits / part-code suffix
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9])|\n\s*\n|\n\s*[•●▪*-]\s+")


_CONTINUES = re.compile(
    r"(?:[,;:\-–(]|\b(?:and|or|of|to|for|the|with|at|in|on|a|an|per|by|across|"
    r"along|between|from|into|throughout|each|about|approximately|total))\s*$",
    re.IGNORECASE,
)
_BOUNDARY_START = re.compile(r"[A-Z•●▪*-]|\d+[.)]\s")


def _blank_scales(text: str) -> str:
    """Blank out scale notes (SCALE 1/4" = 1'-0", 1:50): they are not quantities."""
    chars = list(text)
    for start, end in scale_spans(text):
        for i in range(start, end):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def _join_lines(text: str) -> str:
    """Re-flow line breaks: a wrapped sentence is joined, a new statement is split.

    PDF text and OCR both arrive one visual line per row, and drawing labels
    ("SCALE 1/4"=1'-0"", "CORRIDOR 12'-6" x 8'-0"") have no full stop. A line
    that starts a new capitalised statement (or bullet) begins a new sentence
    unless the previous line clearly continues (ends in a comma, hyphen or a
    connecting word such as "of", "and", "across").
    """
    out: List[str] = []
    previous = ""
    for line in text.replace("\r", "").split("\n"):
        stripped = line.strip()
        if not stripped:
            out.append("\n\n")
            previous = ""
            continue
        if previous:
            new_statement = _BOUNDARY_START.match(stripped) and not _CONTINUES.search(
                previous
            )
            out.append("\n\n" if new_statement else " ")
        out.append(stripped)
        previous = stripped
    return "".join(out)


@dataclass(frozen=True)
class Candidate:
    value: float
    unit_class: str  # "area" | "length" | "dimension" | "count"
    start: int
    end: int


@dataclass(frozen=True)
class TextMatch:
    quantity: float
    snippet: str


def _candidates(sentence: str) -> List[Candidate]:
    found = []
    # Dimension strings (12'-6", 30 ft, 3.6 m, 8-inch) are parsed first so their
    # digits are never re-read as counts; inch dimensions are never quantities.
    dims = find_dimensions(sentence)
    for dim in dims:
        unit_class = "dimension" if dim.kind == "inch" else "length"
        found.append(Candidate(dim.feet, unit_class, dim.start, dim.end))
    for m in _NUMBER_TOKEN_RE.finditer(sentence):
        if any(d.start <= m.start() < d.end for d in dims):
            continue
        if m.group("word"):
            value = float(_NUMBER_WORDS[m.group("word").lower()])
        else:
            value = float(m.group("num").replace(",", ""))
        unit_class = "count"
        end = m.end()
        unit = _UNIT_RE.match(sentence, end)
        if unit is None:
            # allow a single space between number and unit ("120 linear feet")
            unit = (
                _UNIT_RE.match(sentence, end + 1)
                if sentence[end : end + 1] == " "
                else None
            )
            if unit is not None:
                end += 1
        if unit is not None:
            unit_class = unit.lastgroup
            end = unit.end()
            if unit_class == "area_m":
                unit_class, value = "area", value * SQM_TO_SQFT
        found.append(Candidate(value, unit_class, m.start(), end))
    return sorted(found, key=lambda c: c.start)


def extract_matches(text: str) -> Dict[str, TextMatch]:
    """Return category -> TextMatch for the first compatible number found.

    Sentences are scanned independently; inside a sentence each keyword picks
    the *nearest* number whose unit class fits that category.
    """
    found: Dict[str, TextMatch] = {}
    for raw_sentence in _SENTENCE_SPLIT_RE.split(_join_lines(_blank_scales(text))):
        sentence = re.sub(r"\s+", " ", raw_sentence).strip()
        if not sentence:
            continue
        candidates = _candidates(sentence)
        for category, keyword_re in CATEGORY_KEYWORDS.items():
            if category in found:
                continue
            wanted = CATEGORY_UNIT_CLASS[category]
            best: Optional[Candidate] = None
            best_distance = MAX_PAIR_DISTANCE + 1
            for kw in keyword_re.finditer(sentence):
                for cand in candidates:
                    if cand.unit_class != wanted:
                        continue
                    distance = max(kw.start() - cand.end, cand.start - kw.end(), 0)
                    if distance < best_distance:
                        best, best_distance = cand, distance
            if best is not None:
                found[category] = TextMatch(round(best.value, 4), sentence[:160])
    return found


def extract_quantities_from_text(text: str) -> Dict[str, float]:
    """category -> quantity for every category with a unit-compatible match."""
    return {cat: m.quantity for cat, m in extract_matches(text).items()}
