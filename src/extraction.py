"""Shared regex-based quantity extraction used by the text and PDF pipelines."""

import re
from typing import Dict, Optional

CATEGORY_KEYWORDS = {
    "walls": "walls",
    "doors": "doors",
    "flooring": "flooring",
}

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def extract_quantities_from_text(text: str) -> Dict[str, float]:
    """Scan `text` sentence by sentence and pull the first number that shares
    a sentence with each DDC-CWICR keyword (walls / doors / flooring).

    Returns a dict of category -> quantity for every category with a match.
    """
    found: Dict[str, float] = {}
    sentences = _SENTENCE_SPLIT_RE.split(text)
    for sentence in sentences:
        lowered = sentence.lower()
        for category, keyword in CATEGORY_KEYWORDS.items():
            if category in found:
                continue
            if keyword in lowered:
                match = _NUMBER_RE.search(sentence)
                if match:
                    found[category] = float(match.group())
    return found


def extract_single_category(text: str, category: str) -> Optional[float]:
    return extract_quantities_from_text(text).get(category)
