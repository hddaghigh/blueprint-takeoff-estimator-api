"""Door schedule extraction from tables (counts straight from the schedule)."""

import re
from dataclasses import dataclass
from typing import Optional

_QTY_HEADER = re.compile(r"^\s*(qty\.?|quantity|count|no\.?\s*of|number\s+of)\b", re.I)


@dataclass(frozen=True)
class ScheduleCount:
    count: float
    evidence: str


def _to_number(cell) -> Optional[float]:
    try:
        return float(str(cell).strip().replace(",", ""))
    except ValueError:
        return None


def door_schedule_count(page) -> Optional[ScheduleCount]:
    """Count doors in a table whose header names doors; None if no such table.

    A quantity column (QTY/COUNT) is summed when present, otherwise each
    non-empty data row is one door. Only pages mentioning a schedule are
    examined, because table extraction is slow.
    """
    text = page.extract_text() or ""
    if not re.search(r"schedule", text, re.I) or not re.search(r"door", text, re.I):
        return None
    for table in page.extract_tables():
        rows = [r for r in table if any((c or "").strip() for c in r)]
        if len(rows) < 2:
            continue
        header = [(c or "").strip() for c in rows[0]]
        if not any(re.search(r"door", h, re.I) for h in header):
            continue
        data = rows[1:]
        qty_col = next((i for i, h in enumerate(header) if _QTY_HEADER.match(h)), None)
        if qty_col is not None:
            values = [_to_number(r[qty_col]) for r in data if qty_col < len(r)]
            numbers = [v for v in values if v is not None]
            if numbers:
                return ScheduleCount(
                    sum(numbers),
                    f"Door schedule: {len(numbers)} rows, quantity column summed",
                )
        return ScheduleCount(float(len(data)), f"Door schedule: {len(data)} rows")
    return None
