"""Cost estimation: prices takeoff quantities against the DDC-CWICR rate table."""

from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from src import pipelines
from src.database import get_ddc_cwicr_rates
from src.schemas import (
    DocumentEstimateResponse,
    EstimateLineItem,
    EstimateRequest,
    EstimateResponse,
    TakeoffQuantity,
    TakeoffResponse,
)

router = APIRouter(prefix="/api/v1/estimates", tags=["estimates"])


def calculate_estimate(request: EstimateRequest) -> EstimateResponse:
    """Price a takeoff.

    Per line: material = qty x material rate x (1 + waste%) x region index;
    labor = qty x labor hours x labor rate x region index. Then, on the sum of
    lines (`base`): margin, overhead, profit and contingency are each a rate of
    `base`; tax applies last to everything. With only `margin_rate` given this
    equals the original base x (1 + margin).
    """
    rates = get_ddc_cwicr_rates()
    if not request.items:
        raise HTTPException(status_code=422, detail="No takeoff items provided")

    line_items, review = [], []
    base = 0.0
    for takeoff in request.items:
        rate = rates.get(takeoff.category)
        if rate is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown DDC-CWICR category '{takeoff.category}'; expected one of {sorted(rates)}",
            )
        if takeoff.unit and takeoff.unit != rate["unit"]:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unit mismatch for '{takeoff.category}': quantity is in "
                    f"'{takeoff.unit}' but the rate is priced per '{rate['unit']}'"
                ),
            )

        material = (
            takeoff.quantity
            * rate["material_cost_per_unit"]
            * (1 + request.waste_percent / 100)
            * request.region_index
        )
        labor_hours = takeoff.quantity * rate["labor_hours_per_unit"]
        labor = labor_hours * rate["labor_rate_per_hour"] * request.region_index
        line_subtotal = material + labor
        base += line_subtotal
        if takeoff.confidence and takeoff.confidence != "high":
            review.append(
                f"{takeoff.category}: quantity {takeoff.quantity:g} has "
                f"{takeoff.confidence} confidence"
            )
        line_items.append(
            EstimateLineItem(
                category=takeoff.category,
                cwicr_code=rate["cwicr_code"],
                description=rate["description"],
                unit=rate["unit"],
                quantity=takeoff.quantity,
                material_cost=round(material, 2),
                labor_hours=round(labor_hours, 2),
                labor_cost=round(labor, 2),
                line_subtotal=round(line_subtotal, 2),
                confidence=takeoff.confidence,
            )
        )

    margin = base * request.margin_rate
    overhead = base * request.overhead_rate
    profit = base * request.profit_rate
    contingency = base * request.contingency_rate
    pre_tax = base + margin + overhead + profit + contingency
    tax = pre_tax * request.tax_rate
    return EstimateResponse(
        line_items=line_items,
        subtotal=round(base, 2),
        margin_rate=request.margin_rate,
        margin_amount=round(margin, 2),
        total=round(pre_tax + tax, 2),
        adjustments={
            "waste_percent": request.waste_percent,
            "region_index": request.region_index,
            "overhead_amount": round(overhead, 2),
            "profit_amount": round(profit, 2),
            "contingency_amount": round(contingency, 2),
            "tax_amount": round(tax, 2),
        },
        needs_review=review,
    )


@router.post("", response_model=EstimateResponse)
async def create_estimate(request: EstimateRequest) -> EstimateResponse:
    return calculate_estimate(request)


@router.post("/from-document", response_model=DocumentEstimateResponse)
async def estimate_from_document(
    file: UploadFile = File(...),
    margin_rate: float = Form(default=0.15),
    waste_percent: float = Form(default=0.0),
    region_index: float = Form(default=1.0),
    overhead_rate: float = Form(default=0.0),
    profit_rate: float = Form(default=0.0),
    contingency_rate: float = Form(default=0.0),
    tax_rate: float = Form(default=0.0),
    units: Optional[str] = Form(default=None),
    wall_representation: str = Form(default="centerline"),
    include_blocks: Optional[str] = Form(default=None),
    use_vision: bool = Form(default=False),
) -> DocumentEstimateResponse:
    """Upload a document and get takeoff + priced estimate in one call.

    The file type is detected from its content (PDF, photo, DXF/DWG, text).
    `needs_review` lists everything a person should check before trusting the
    numbers: low-confidence quantities, scale/OCR/page-limit problems.
    """
    raw = await pipelines.read_upload(file)
    name = file.filename or "upload"
    takeoff: TakeoffResponse = await run_in_threadpool(
        pipelines.run_takeoff,
        raw,
        name,
        units,
        wall_representation,
        use_vision,
        include_blocks,
    )

    review = list(takeoff.metadata.get("review_reasons", []))
    if takeoff.metadata.get("needs_review") and not review:
        review.append("the takeoff reported warnings; see takeoff.metadata.warnings")
    if not takeoff.items:
        review.append("no quantities were found, so no estimate was produced")
        return DocumentEstimateResponse(
            takeoff=takeoff, estimate=None, needs_review=review
        )

    estimate = calculate_estimate(
        EstimateRequest(
            items=[
                TakeoffQuantity(
                    category=i.category,
                    quantity=i.quantity,
                    unit=i.unit,
                    confidence=i.confidence,
                )
                for i in takeoff.items
            ],
            margin_rate=margin_rate,
            waste_percent=waste_percent,
            region_index=region_index,
            overhead_rate=overhead_rate,
            profit_rate=profit_rate,
            contingency_rate=contingency_rate,
            tax_rate=tax_rate,
        )
    )
    return DocumentEstimateResponse(
        takeoff=takeoff, estimate=estimate, needs_review=review + estimate.needs_review
    )
