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

_UNIT_PATTERNS = [
    # order matters: area must be tried before length ("sq ft" before "ft")
    ("area", r"sq\.?\s*ft\.?|sqft|sf\b|sq\.?\s*feet|square\s+f(?:ee|oo)t"),
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
    for m in _NUMBER_TOKEN_RE.finditer(sentence):
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
        found.append(Candidate(value, unit_class, m.start(), end))
    return found


def extract_matches(text: str) -> Dict[str, TextMatch]:
    """Return category -> TextMatch for the first compatible number found.

    Sentences are scanned independently; inside a sentence each keyword picks
    the *nearest* number whose unit class fits that category.
    """
    found: Dict[str, TextMatch] = {}
    normalised = text.replace("\r", "")
    for raw_sentence in _SENTENCE_SPLIT_RE.split(normalised):
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
                found[category] = TextMatch(best.value, sentence[:160])
    return found


def extract_quantities_from_text(text: str) -> Dict[str, float]:
    """category -> quantity for every category with a unit-compatible match."""
    return {cat: m.quantity for cat, m in extract_matches(text).items()}
