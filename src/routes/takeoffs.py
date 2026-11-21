"""Multi-modal takeoff input pipelines: text, photo, and drawing PDF."""

import io
import math
from typing import Dict, Optional

import numpy as np
import pdfplumber
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pdfminer.pdfdocument import PDFPasswordIncorrect
from PIL import Image

from src.config import SAMPLE_BLUEPRINT_PDF_PATH, SAMPLE_SITE_PHOTO_PATH, SCOPE_TXT_PATH
from src.database import get_ddc_cwicr_rates
from src.extraction import extract_matches
from src.schemas import TakeoffItem, TakeoffResponse

router = APIRouter(prefix="/api/v1/takeoffs", tags=["takeoffs"])

# Calibration constant for the photo pipeline: an MVP heuristic mapping
# photographed floor pixel area to square feet, since no camera calibration
# metadata is available for an arbitrary uploaded photo.
PHOTO_PIXELS_PER_SQFT = 500.0

# Hard page cap so one huge PDF cannot tie up a worker. Hitting it is never
# silent: the response carries a warning with pages_total / pages_scanned.
MAX_PDF_PAGES = 100

POINTS_PER_INCH = 72.0  # PDF user-space unit
POINTS_PER_FOOT = POINTS_PER_INCH * 12.0  # 864 points per foot


def _decode_text(raw: bytes) -> str:
    """Decode an uploaded text file; reject binary uploads with a 422, not a 500."""
    if b"\x00" in raw:
        raise HTTPException(
            status_code=422, detail="Upload is a binary file, not a text file"
        )
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")  # legacy Windows text


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
        source = file.filename or "uploaded_text"
    else:
        if not SCOPE_TXT_PATH.exists():
            raise HTTPException(
                status_code=404,
                detail="assets/scope.txt not found; run bootstrap_assets.py",
            )
        raw = SCOPE_TXT_PATH.read_bytes()
        source = str(SCOPE_TXT_PATH)

    text = _decode_text(raw)
    matches = extract_matches(text)
    if not matches:
        raise HTTPException(
            status_code=422, detail="No walls/doors/flooring quantities found in text"
        )

    return TakeoffResponse(
        source=source,
        items=_build_items({c: m.quantity for c, m in matches.items()}),
        metadata={
            "characters_scanned": len(text),
            "evidence": {c: m.snippet for c, m in matches.items()},
        },
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
    """Scan a PDF's text layer, vector geometry and embedded images.

    Takeoff items come only from unit-aware text callouts (never from page
    size or shape counts, which have no reliable meaning). Vector and image
    statistics, and any problem hit while reading (page cap, scanned pages,
    unreadable pages), are reported in `metadata`. Unreadable, corrupt or
    password-protected files return 422.
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

    matches, metadata = await run_in_threadpool(_scan_pdf, raw)
    return TakeoffResponse(
        source=source,
        items=_build_items({c: m.quantity for c, m in matches.items()}),
        metadata=metadata,
    )


def _path_length(points) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def _scan_pdf(raw: bytes):
    """Blocking PDF scan (run in a worker thread). Returns (matches, metadata)."""
    try:
        pdf = pdfplumber.open(io.BytesIO(raw))
    except Exception as exc:
        reason = exc.args[0] if exc.args else exc
        if isinstance(reason, PDFPasswordIncorrect):
            raise HTTPException(
                status_code=422,
                detail="PDF is password-protected; unlock it and re-upload",
            )
        raise HTTPException(status_code=422, detail=f"Not a valid PDF file: {exc}")

    warnings = []
    texts = []
    failed_pages = []
    image_count = line_count = curve_count = rect_count = 0
    path_length_pt = 0.0
    pages_with_text = pages_image_only = pages_scanned = 0

    with pdf:
        pages_total = len(pdf.pages)
        if pages_total == 0:
            warnings.append("PDF has no pages")
        if pages_total > MAX_PDF_PAGES:
            warnings.append(
                f"Only the first {MAX_PDF_PAGES} of {pages_total} pages were scanned; "
                "quantities on later pages are not included"
            )
        for number, page in enumerate(pdf.pages[:MAX_PDF_PAGES], start=1):
            try:
                text = (page.extract_text() or "").strip()
                images = len(page.images)
                lines, curves, rects = page.lines, page.curves, page.rects
                length = sum(
                    math.hypot(ln["x1"] - ln["x0"], ln["y1"] - ln["y0"]) for ln in lines
                ) + sum(_path_length(c["pts"]) for c in curves)
            except Exception:
                failed_pages.append(number)
                continue
            pages_scanned += 1
            texts.append(text)
            pages_with_text += bool(text)
            pages_image_only += bool(images and not text)
            image_count += images
            line_count += len(lines)
            curve_count += len(curves)
            rect_count += len(rects)
            path_length_pt += length

    if failed_pages:
        warnings.append(f"Could not parse page(s) {failed_pages}; they were skipped")
    if pages_scanned and pages_with_text == 0:
        warnings.append(
            "No text layer found"
            + (
                f"; the PDF looks like scanned images ({image_count} embedded). "
                "OCR is not supported, so no quantities could be read"
                if image_count
                else " (vector-only drawing with no text callouts)"
            )
        )
    elif pages_image_only:
        warnings.append(
            f"{pages_image_only} page(s) hold images but no text (likely scanned) "
            "and were not read"
        )

    matches = extract_matches("\n\n".join(texts))
    if pages_with_text and not matches:
        warnings.append(
            "No walls/doors/flooring quantity with a compatible unit found in the text"
        )

    metadata = {
        "pages_total": pages_total,
        "pages_scanned": pages_scanned,
        "pages_with_text": pages_with_text,
        "image_count": image_count,
        "vector_line_count": line_count,
        "vector_curve_count": curve_count,
        "vector_rect_count": rect_count,
        "vector_path_length_ft_paper_space": round(path_length_pt / POINTS_PER_FOOT, 2),
        "evidence": {c: m.snippet for c, m in matches.items()},
        "warnings": warnings,
        "note": (
            "Takeoff items come only from text callouts. Vector/image figures are "
            "reported for reference: lengths are paper-space (1:1, no drawing scale "
            "applied) and curve lengths are approximated by their control points."
        ),
    }
    return matches, metadata
