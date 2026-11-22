"""Pydantic models shared across the takeoff and estimate routers."""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class TakeoffItem(BaseModel):
    """A single quantity extracted for one DDC-CWICR category."""

    category: str
    cwicr_code: str
    description: str
    unit: str
    quantity: float
    # Provenance: where the number came from and how far to trust it.
    basis: str = "text"  # text | schedule | geometry | cad | vision
    confidence: str = "high"  # high | medium | low
    evidence: Optional[str] = None


class TakeoffResponse(BaseModel):
    source: str
    items: List[TakeoffItem]
    metadata: Dict[str, object] = Field(default_factory=dict)


class TakeoffQuantity(BaseModel):
    """Input for the estimate engine: a category and a quantity.

    `unit`, when given, must match the rate's unit (guards against pricing
    square feet as linear feet). `confidence` marks lines for human review.
    """

    category: str
    quantity: float = Field(ge=0)
    unit: Optional[str] = None
    confidence: Optional[str] = None


class EstimateRequest(BaseModel):
    items: List[TakeoffQuantity]
    # The original flat markup; still applied exactly as before.
    margin_rate: float = Field(default=0.15, ge=0)
    # Optional extras, all off by default so existing requests price identically.
    waste_percent: float = Field(default=0.0, ge=0)  # added to material cost only
    region_index: float = Field(default=1.0, gt=0)  # multiplies material and labor
    overhead_rate: float = Field(default=0.0, ge=0)
    profit_rate: float = Field(default=0.0, ge=0)
    contingency_rate: float = Field(default=0.0, ge=0)
    tax_rate: float = Field(default=0.0, ge=0)  # applied after everything else


class EstimateLineItem(BaseModel):
    category: str
    cwicr_code: str
    description: str
    unit: str
    quantity: float
    material_cost: float
    labor_hours: float
    labor_cost: float
    line_subtotal: float
    confidence: Optional[str] = None


class EstimateResponse(BaseModel):
    line_items: List[EstimateLineItem]
    subtotal: float
    margin_rate: float
    margin_amount: float
    total: float
    adjustments: Dict[str, float] = Field(default_factory=dict)
    needs_review: List[str] = Field(default_factory=list)


class DocumentEstimateResponse(BaseModel):
    takeoff: TakeoffResponse
    estimate: Optional[EstimateResponse] = None
    needs_review: List[str] = Field(default_factory=list)
