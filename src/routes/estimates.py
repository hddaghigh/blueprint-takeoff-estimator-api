"""Cost estimation sync: turns takeoff quantities into a priced proposal."""

from fastapi import APIRouter, HTTPException

from src.database import get_ddc_cwicr_rates
from src.schemas import EstimateLineItem, EstimateRequest, EstimateResponse

router = APIRouter(prefix="/api/v1/estimates", tags=["estimates"])


@router.post("", response_model=EstimateResponse)
async def create_estimate(request: EstimateRequest) -> EstimateResponse:
    rates = get_ddc_cwicr_rates()
    line_items = []
    subtotal = 0.0

    for takeoff in request.items:
        rate = rates.get(takeoff.category)
        if rate is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown DDC-CWICR category '{takeoff.category}'; expected one of {sorted(rates)}",
            )

        material_cost = takeoff.quantity * rate["material_cost_per_unit"]
        labor_hours = takeoff.quantity * rate["labor_hours_per_unit"]
        labor_cost = labor_hours * rate["labor_rate_per_hour"]
        line_subtotal = material_cost + labor_cost
        subtotal += line_subtotal

        line_items.append(
            EstimateLineItem(
                category=takeoff.category,
                cwicr_code=rate["cwicr_code"],
                description=rate["description"],
                unit=rate["unit"],
                quantity=takeoff.quantity,
                material_cost=round(material_cost, 2),
                labor_hours=round(labor_hours, 2),
                labor_cost=round(labor_cost, 2),
                line_subtotal=round(line_subtotal, 2),
            )
        )

    if not line_items:
        raise HTTPException(status_code=422, detail="No takeoff items provided")

    margin_amount = subtotal * request.margin_rate
    total = subtotal + margin_amount

    return EstimateResponse(
        line_items=line_items,
        subtotal=round(subtotal, 2),
        margin_rate=request.margin_rate,
        margin_amount=round(margin_amount, 2),
        total=round(total, 2),
    )
