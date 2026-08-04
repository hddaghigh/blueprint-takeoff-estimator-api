"""Multi-modal takeoff input pipelines: text, photo, and drawing PDF."""

import math
from typing import Dict, Optional

import numpy as np
import pdfplumber
from fastapi import APIRouter, File, HTTPException, UploadFile
from PIL import Image

from src.config import SAMPLE_BLUEPRINT_PDF_PATH, SAMPLE_SITE_PHOTO_PATH, SCOPE_TXT_PATH
from src.database import get_ddc_cwicr_rates
from src.extraction import extract_quantities_from_text
from src.schemas import TakeoffItem, TakeoffResponse

router = APIRouter(prefix="/api/v1/takeoffs", tags=["takeoffs"])

# Calibration constant for the photo pipeline: an MVP heuristic mapping
# photographed floor pixel area to square feet, since no camera calibration
# metadata is available for an arbitrary uploaded photo.
PHOTO_PIXELS_PER_SQFT = 500.0

# Only the first N pages of an arbitrary uploaded PDF are scanned, to keep
# the endpoint responsive for large documents.
MAX_PDF_PAGES = 15

POINTS_PER_FOOT = 72.0 / 12.0  # 72 points per inch, 12 inches per foot


def _build_items(quantities: Dict[str, float]) -> list[TakeoffItem]:
    rates = get_ddc_cwicr_rates()
    items = []
    for category, quantity in quantities.items():
        rate = rates.get(category)
        if rate is None:
            continue
        items.append(
            TakeoffItem(
                category=category,
                cwicr_code=rate["cwicr_code"],
                description=rate["description"],
                unit=rate["unit"],
                quantity=round(quantity, 2),
            )
        )
    return items


@router.post("/text", response_model=TakeoffResponse)
async def takeoff_from_text(
    file: Optional[UploadFile] = File(default=None),
) -> TakeoffResponse:
    """Isolate wall/door/flooring dimensions from a scope-of-work text file.

    If no file is uploaded, falls back to streaming the bundled
    assets/scope.txt reference asset.
    """
    if file is not None:
        raw = await file.read()
        text = raw.decode("utf-8")
        source = file.filename or "uploaded_text"
    else:
        if not SCOPE_TXT_PATH.exists():
            raise HTTPException(
                status_code=404,
                detail="assets/scope.txt not found; run bootstrap_assets.py",
            )
        text = SCOPE_TXT_PATH.read_text(encoding="utf-8")
        source = str(SCOPE_TXT_PATH)

    quantities = extract_quantities_from_text(text)
    if not quantities:
        raise HTTPException(
            status_code=422, detail="No walls/doors/flooring quantities found in text"
        )

    return TakeoffResponse(
        source=source,
        items=_build_items(quantities),
        metadata={"characters_scanned": len(text)},
    )


@router.post("/photo", response_model=TakeoffResponse)
async def takeoff_from_photo(
    file: Optional[UploadFile] = File(default=None),
) -> TakeoffResponse:
    """Parse a real jobsite photo's RGB pixel matrix with Pillow, compute
    average luminance to prove the file is a valid decodable image, and
    derive a heuristic flooring quantity from the photographed pixel area.
    """
    if file is not None:
        raw = await file.read()
        source = file.filename or "uploaded_photo"
    else:
        if not SAMPLE_SITE_PHOTO_PATH.exists():
            raise HTTPException(
                status_code=404,
                detail="assets/sample_site_photo.jpg not found; run bootstrap_assets.py",
            )
        raw = SAMPLE_SITE_PHOTO_PATH.read_bytes()
        source = str(SAMPLE_SITE_PHOTO_PATH)

    try:
        import io

        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:  # pragma: no cover - defensive, exercised via 422 test
        raise HTTPException(status_code=422, detail=f"Not a valid image file: {exc}")

    rgb_image = image.convert("RGB")
    pixel_matrix = np.asarray(rgb_image, dtype=np.float64)
    pixel_count = pixel_matrix.shape[0] * pixel_matrix.shape[1]
    luminance_weights = np.array([0.2126, 0.7152, 0.0722])
    avg_luminance = float((pixel_matrix @ luminance_weights).mean())

    width, height = rgb_image.size
    estimated_flooring_sqft = (width * height) / PHOTO_PIXELS_PER_SQFT

    quantities = {"flooring": estimated_flooring_sqft}

    return TakeoffResponse(
        source=source,
        items=_build_items(quantities),
        metadata={
            "image_width": width,
            "image_height": height,
            "pixel_count": pixel_count,
            "average_luminance": round(avg_luminance, 2),
            "note": "flooring quantity derived from a pixel-area calibration heuristic (MVP, uncalibrated)",
        },
    )


@router.post("/drawing-pdf", response_model=TakeoffResponse)
async def takeoff_from_pdf(
    file: Optional[UploadFile] = File(default=None),
) -> TakeoffResponse:
    """Scan a real PDF's embedded text and vector geometry with pdfplumber.

    Text-based keyword+number pairs (as in the text pipeline) take priority.
    Any category not found in the text falls back to a quantity derived from
    real vector geometry scanned off the page: summed line lengths for
    walls, rect counts for doors, and summed page area for flooring.
    """
    if file is not None:
        raw = await file.read()
        source = file.filename or "uploaded_drawing.pdf"
    else:
        if not SAMPLE_BLUEPRINT_PDF_PATH.exists():
            raise HTTPException(
                status_code=404,
                detail="assets/sample_blueprint.pdf not found; run bootstrap_assets.py",
            )
        raw = SAMPLE_BLUEPRINT_PDF_PATH.read_bytes()
        source = str(SAMPLE_BLUEPRINT_PDF_PATH)

    import io

    try:
        pdf = pdfplumber.open(io.BytesIO(raw))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Not a valid PDF file: {exc}")

    text_chunks = []
    total_line_length_pt = 0.0
    total_rects = 0
    total_page_area_pt2 = 0.0
    pages_scanned = 0

    with pdf:
        for page in pdf.pages[:MAX_PDF_PAGES]:
            pages_scanned += 1
            page_text = page.extract_text() or ""
            text_chunks.append(page_text)

            for line in page.lines:
                dx = line["x1"] - line["x0"]
                dy = line["y1"] - line["y0"]
                total_line_length_pt += math.hypot(dx, dy)

            total_rects += len(page.rects)
            total_page_area_pt2 += page.width * page.height

    combined_text = "\n".join(text_chunks)
    quantities = extract_quantities_from_text(combined_text)

    if "walls" not in quantities and total_line_length_pt > 0:
        quantities["walls"] = total_line_length_pt / POINTS_PER_FOOT
    if "doors" not in quantities and total_rects > 0:
        quantities["doors"] = float(total_rects)
    if "flooring" not in quantities and total_page_area_pt2 > 0:
        quantities["flooring"] = total_page_area_pt2 / (POINTS_PER_FOOT**2)

    if not quantities:
        raise HTTPException(
            status_code=422,
            detail="No extractable text or vector geometry found in PDF",
        )

    return TakeoffResponse(
        source=source,
        items=_build_items(quantities),
        metadata={
            "pages_scanned": pages_scanned,
            "vector_line_length_ft": round(total_line_length_pt / POINTS_PER_FOOT, 2),
            "vector_rect_count": total_rects,
        },
    )
