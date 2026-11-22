"""A quantity found by any pipeline, with provenance, and its conversion to items."""

from dataclasses import dataclass
from typing import Dict, List

from src.database import get_ddc_cwicr_rates
from src.schemas import TakeoffItem


@dataclass
class Finding:
    category: str
    quantity: float
    basis: str  # text | schedule | geometry | cad | vision
    confidence: str  # high | medium | low
    evidence: str


def build_items(findings: Dict[str, Finding]) -> List[TakeoffItem]:
    """Attach DDC-CWICR code/description/unit to each finding's category."""
    rates = get_ddc_cwicr_rates()
    items = []
    for category, finding in findings.items():
        rate = rates.get(category)
        if rate is None:
            continue
        items.append(
            TakeoffItem(
                category=category,
                cwicr_code=rate["cwicr_code"],
                description=rate["description"],
                unit=rate["unit"],
                quantity=round(finding.quantity, 2),
                basis=finding.basis,
                confidence=finding.confidence,
                evidence=finding.evidence,
            )
        )
    return items
