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


class TakeoffResponse(BaseModel):
    source: str
    items: List[TakeoffItem]
    metadata: Dict[str, object] = Field(default_factory=dict)


class TakeoffQuantity(BaseModel):
    """Minimal input for the estimate engine: a category and a quantity."""

    category: str
    quantity: float


class EstimateRequest(BaseModel):
    items: List[TakeoffQuantity]
    margin_rate: float = 0.15


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


class EstimateResponse(BaseModel):
    line_items: List[EstimateLineItem]
    subtotal: float
    margin_rate: float
    margin_amount: float
    total: float
