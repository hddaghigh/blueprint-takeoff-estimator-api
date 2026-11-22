"""Repair common OCR spacing errors before quantity extraction.

OCR engines often drop the spaces between words or between a number and its
unit ("4doors and120linear feetofwalls"). Only text from OCR goes through
this; text layers and CAD notes are never altered. Splitting uses a small
construction vocabulary and only fires when a letter run is *entirely* made of
vocabulary words (or is one such word glued to a digit), so part codes
(D1, A101), dimensions and ordinary words are left alone.
"""

import re
from functools import lru_cache
from typing import List, Optional

VOCABULARY = frozenset("""
    wall walls door doors flooring floor area linear sq sqft sf lf ft feet foot
    square each ea inch inches total about approx approximately
    and of for with to at the in on per across along from into
    install provide erect fit build lay supply furnish place construct
    """.split())
_MAX_WORD = max(len(w) for w in VOCABULARY)
_RUN = re.compile(r"[A-Za-z]+")


@lru_cache(maxsize=4096)
def _segment(run: str) -> Optional[tuple]:
    """Split `run` entirely into vocabulary words, preferring longer words."""
    low = run.lower()
    n = len(low)
    best: List[Optional[tuple]] = [None] * (n + 1)
    best[0] = ()
    for end in range(1, n + 1):
        for start in range(max(0, end - _MAX_WORD), end):
            word = low[start:end]
            if best[start] is not None and word in VOCABULARY:
                candidate = best[start] + (run[start:end],)
                # fewer, longer words win
                if best[end] is None or len(candidate) < len(best[end]):
                    best[end] = candidate
    return best[n]


_ABBR_KEYWORD = re.compile(r"([A-Z]{2,})(walls?|doors?|flooring)")
_LOWER_UPPER = re.compile(r"([a-z]+)([A-Z].*)")


def _split(run: str) -> Optional[tuple]:
    """Words making up a glued run, or None when it should be left alone."""
    parts = _segment(run)
    if parts:
        return parts
    m = _ABBR_KEYWORD.fullmatch(run)  # CMUwalls -> CMU walls
    if m:
        return m.groups()
    m = _LOWER_UPPER.fullmatch(run)  # inchCMUwalls -> inch CMU walls
    if m and m.group(1).lower() in VOCABULARY:
        return (m.group(1),) + (_split(m.group(2)) or (m.group(2),))
    return None


def unglue(text: str) -> str:
    """Insert missing spaces around vocabulary words and digit/word joins."""
    out: List[str] = []
    last = 0
    for m in _RUN.finditer(text):
        run = m.group()
        before = text[m.start() - 1] if m.start() else ""
        after = text[m.end()] if m.end() < len(text) else ""
        parts = _split(run)
        replacement = None
        if parts and (len(parts) > 1 or before.isdigit() or after.isdigit()):
            replacement = " ".join(parts)
        elif run.isupper() and len(run) >= 3 and (before.isdigit() or after.isdigit()):
            replacement = run  # label glued to a number: handled by spacing below
        if replacement is None:
            continue
        lead = " " if before.isdigit() else ""
        trail = " " if after.isdigit() else ""
        out.append(text[last : m.start()])
        out.append(lead + replacement + trail)
        last = m.end()
    out.append(text[last:])
    return "".join(out)
