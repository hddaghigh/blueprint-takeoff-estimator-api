"""Input pipelines shared by the HTTP routes and the one-shot estimate endpoint.

Each `process_*` function takes raw bytes and returns a TakeoffResponse; they
raise HTTPException(422) for unreadable input.
"""

import io
from typing import Dict, Optional

import numpy as np
from fastapi import HTTPException, UploadFile
from PIL import Image

from src import config
from src.extraction import extract_matches
from src.findings import Finding, build_items
from src.ocr import engine as ocr_engine
from src.ocr.preprocess import prepare
from src.pdf_scan import scan_pdf
from src.schemas import TakeoffResponse
from src.vision.claude import analyze_image


async def read_upload(file: UploadFile) -> bytes:
    """Read an upload, rejecting anything over MAX_UPLOAD_MB with a 413."""
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(
            status_code=413, detail=f"Upload exceeds {config.MAX_UPLOAD_MB} MB limit"
        )
    return raw


def decode_text(raw: bytes) -> str:
    """Decode an uploaded text file; reject binary uploads with a 422, not a 500."""
    if b"\x00" in raw:
        raise HTTPException(
            status_code=422, detail="Upload is a binary file, not a text file"
        )
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")  # legacy Windows text


def detect_kind(raw: bytes, filename: str) -> str:
    """pdf | photo | cad | text, from the file's content (not just its name)."""
    name = filename.lower()
    if raw[:5] == b"%PDF-":
        return "pdf"
    if raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "photo"
    if name.endswith((".dxf", ".dwg")) or raw[:4].startswith(b"AC10"):
        return "cad"
    if raw[:4] in (b"II*\x00", b"MM\x00*") or raw[:3] == b"GIF" or raw[:2] == b"BM":
        return "photo"
    if b"\x00" not in raw[:4096]:
        return "text"
    raise HTTPException(
        status_code=422,
        detail="Unsupported file type; upload a PDF, JPEG/PNG photo, DXF/DWG drawing or text file",
    )


def run_takeoff(
    raw: bytes,
    name: str,
    units: Optional[str] = None,
    wall_representation: str = "centerline",
    use_vision: bool = False,
    include_blocks: Optional[str] = None,
) -> TakeoffResponse:
    """Detect the file type and run the matching pipeline (blocking)."""
    from src import cad  # local import: cad pulls in ezdxf

    kind = detect_kind(raw, name)
    if kind == "pdf":
        return process_pdf(raw, name, use_vision)
    if kind == "photo":
        return process_photo(raw, name, use_vision)
    if kind == "cad":
        return cad.process_cad(raw, name, units, wall_representation, include_blocks)
    return process_text(raw, name)


def process_text(raw: bytes, source: str) -> TakeoffResponse:
    text = decode_text(raw)
    matches = extract_matches(text)
    if not matches:
        raise HTTPException(
            status_code=422, detail="No walls/doors/flooring quantities found in text"
        )
    findings = {
        c: Finding(c, m.quantity, "text", "high", m.snippet) for c, m in matches.items()
    }
    return TakeoffResponse(
        source=source,
        items=build_items(findings),
        metadata={
            "characters_scanned": len(text),
            "evidence": {c: f.evidence for c, f in findings.items()},
        },
    )


def process_pdf(raw: bytes, source: str, use_vision: bool = False) -> TakeoffResponse:
    result = scan_pdf(raw, use_vision)
    return TakeoffResponse(
        source=source, items=build_items(result.findings), metadata=result.metadata
    )


def process_photo(raw: bytes, source: str, use_vision: bool = False) -> TakeoffResponse:
    """Validate and read a jobsite photo.

    Decodes the RGB pixel matrix and computes average luminance (proves the file
    is a real, decodable image), then reads visible text with OCR when an engine
    is installed. No quantity is invented from pixel counts.
    """
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Not a valid image file: {exc}")

    rgb = image.convert("RGB")
    pixels = np.asarray(rgb, dtype=np.float64)
    avg_luminance = float((pixels @ np.array([0.2126, 0.7152, 0.0722])).mean())
    width, height = rgb.size

    warnings = []
    findings: Dict[str, Finding] = {}
    ocr_meta = {"engine": None, "mean_confidence": None}
    engine = ocr_engine.get_engine()
    if engine is None:
        warnings.append("OCR is not installed; any text in the photo was not read")
    else:
        try:
            wants_binary = getattr(engine, "wants_binary", True)
            result = engine.read(prepare(rgb, binarize=wants_binary))
            ocr_meta = {
                "engine": engine.name,
                "mean_confidence": round(result.mean_confidence, 1),
            }
            confidence = (
                "medium"
                if result.mean_confidence >= ocr_engine.LOW_CONFIDENCE
                else "low"
            )
            for category, match in extract_matches(result.text).items():
                findings[category] = Finding(
                    category,
                    match.quantity,
                    "text",
                    confidence,
                    "OCR: " + match.snippet,
                )
        except Exception as exc:
            warnings.append(f"OCR failed: {exc}")

    vision_meta = {"used": False}
    if use_vision:
        vres = analyze_image(rgb, "a jobsite photo or drawing sheet")
        warnings.extend(vres.warnings)
        vision_meta = {
            "used": True,
            "model": vres.model,
            "notes": vres.notes,
            "text_found": vres.text_found,
            "dimensions": vres.dimensions,
        }
        for category, match in extract_matches("\n".join(vres.text_found)).items():
            findings.setdefault(
                category,
                Finding(
                    category,
                    match.quantity,
                    "vision",
                    "low",
                    "Claude vision text: " + match.snippet,
                ),
            )
        for category, found in vres.findings.items():
            findings.setdefault(category, found)

    review = [
        f"{f.category} is {f.basis}-derived ({f.confidence} confidence)"
        for f in findings.values()
        if f.confidence != "high"
    ]
    metadata = {
        "image_width": width,
        "image_height": height,
        "pixel_count": width * height,
        "average_luminance": round(avg_luminance, 2),
        "ocr": ocr_meta,
        "vision": vision_meta,
        "warnings": warnings,
        "review_reasons": review,
        "needs_review": bool(review),
        "note": "No quantity is derived from pixel counts; items come only from text read in the image.",
    }
    return TakeoffResponse(
        source=source, items=build_items(findings), metadata=metadata
    )
