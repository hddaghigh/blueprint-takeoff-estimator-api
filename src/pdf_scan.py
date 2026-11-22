"""PDF scanning service: text layer, OCR fallback, scale, geometry, schedules.

Priority per category: door schedule > text callout (text layer, then OCR) >
geometry. Every finding carries its basis, a confidence and evidence, and
anything that limits the result is listed in `metadata.warnings`.
"""

import io
import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pdfplumber
from fastapi import HTTPException
from pdfminer.pdfdocument import PDFPasswordIncorrect

from src import config
from src.extraction import extract_matches
from src.findings import Finding
from src.geometry.paths import page_segments, polyline_length, subpaths
from src.geometry.schedules import door_schedule_count
from src.geometry.semantics import (
    POINTS_PER_FOOT,
    door_swing_count,
    page_polygons,
    paired_wall_length,
    room_areas,
)
from src.measure.dimensions import find_dimensions
from src.measure.scale import find_scales, is_not_to_scale
from src.ocr import engine as ocr_engine
from src.ocr.preprocess import prepare
from src.ocr.rasterize import render_page
from src.vision.claude import analyze_image

MIN_TEXT_CHARS = 20  # fewer characters than this = no usable text layer
MAX_OCR_PIXELS_LONG_SIDE = 6000
MAX_DIMENSIONS_REPORTED = 25


@dataclass
class ScanResult:
    findings: Dict[str, Finding]
    metadata: dict


def _open(raw: bytes):
    try:
        return pdfplumber.open(io.BytesIO(raw))
    except Exception as exc:
        reason = exc.args[0] if exc.args else exc
        if isinstance(reason, PDFPasswordIncorrect):
            raise HTTPException(
                status_code=422,
                detail="PDF is password-protected; unlock it and re-upload",
            )
        raise HTTPException(status_code=422, detail=f"Not a valid PDF file: {exc}")


def _ocr_page(page, engine):
    long_side_in = max(float(page.width), float(page.height)) / 72.0
    dpi = max(100, min(300, int(MAX_OCR_PIXELS_LONG_SIDE / long_side_in)))
    wants_binary = getattr(engine, "wants_binary", True)
    return engine.read(prepare(render_page(page, dpi), binarize=wants_binary))


def _confidence_for_ocr(mean: float) -> str:
    return "medium" if mean >= ocr_engine.LOW_CONFIDENCE else "low"


def scan_pdf(raw: bytes, use_vision: bool = False) -> ScanResult:
    timings = {"text_ms": 0.0, "ocr_ms": 0.0, "geometry_ms": 0.0, "vision_ms": 0.0}
    vision_pages = vision_ok = 0
    vision_model: Optional[str] = None
    vision_findings: Dict[str, Finding] = {}
    vision_texts: List[str] = []
    vision_dims: List[dict] = []
    warnings: List[str] = []
    review: List[str] = []
    pdf = _open(raw)
    engine = ocr_engine.get_engine()

    texts: List[str] = []  # text-layer text
    ocr_texts: List[str] = []
    ocr_confidences: List[float] = []
    ocr_pages: List[int] = []
    failed_pages: List[int] = []
    pages_needing_ocr = 0
    stats = dict(images=0, lines=0, curves=0, rects=0, pages_with_text=0, image_only=0)
    path_pt = 0.0
    real_path_ft = 0.0
    pages_with_scale = 0
    scale_labels: List[str] = []
    ambiguous_scale_pages: List[int] = []
    wall_ft = wall_pairs = 0.0
    swing_candidates = swing_confirmed = 0
    rooms = []
    schedule_total: Optional[float] = None
    schedule_evidence: List[str] = []
    segments_truncated = False
    pages_scanned = 0

    with pdf:
        pages_total = len(pdf.pages)
        cap = config.MAX_PDF_PAGES
        if pages_total == 0:
            warnings.append("PDF has no pages")
        if pages_total > cap:
            warnings.append(
                f"Only the first {cap} of {pages_total} pages were scanned; "
                "quantities on later pages are not included"
            )
            review.append("page cap reached")

        for number, page in enumerate(pdf.pages[:cap], start=1):
            try:
                t0 = time.perf_counter()
                text = (page.extract_text() or "").strip()
                n_images = len(page.images)
                lines, curves, rects = page.lines, page.curves, page.rects
                timings["text_ms"] += (time.perf_counter() - t0) * 1000

                has_text = bool(text)
                # OCR when there is no text layer, or only a sliver of text next to
                # images (a scan with a stray stamp). Real text is never discarded.
                needs_ocr = not has_text or (
                    len(text) < MIN_TEXT_CHARS and n_images > 0
                )
                page_ocr_text = ""
                if needs_ocr:
                    pages_needing_ocr += 1
                    if engine is not None and len(ocr_pages) < config.OCR_MAX_PAGES:
                        t0 = time.perf_counter()
                        try:
                            result = _ocr_page(page, engine)
                            page_ocr_text = result.text
                            ocr_texts.append(result.text)
                            if result.word_count:  # a blank page has no confidence
                                ocr_confidences.append(result.mean_confidence)
                            ocr_pages.append(number)
                        except Exception as exc:  # OCR must never take the request down
                            warnings.append(f"OCR failed on page {number}: {exc}")
                        timings["ocr_ms"] += (time.perf_counter() - t0) * 1000
                    if (
                        use_vision
                        and not page_ocr_text
                        and vision_pages < config.VISION_MAX_PAGES
                    ):
                        t0 = time.perf_counter()
                        vision_pages += 1
                        vres = analyze_image(
                            render_page(page, 150),
                            f"page {number} of a construction PDF",
                        )
                        warnings.extend(vres.warnings)
                        vision_ok += not vres.warnings
                        vision_model = vres.model
                        vision_texts.extend(vres.text_found)
                        vision_dims.extend(vres.dimensions)
                        for cat, found in vres.findings.items():
                            vision_findings.setdefault(cat, found)
                        timings["vision_ms"] += (time.perf_counter() - t0) * 1000

                t0 = time.perf_counter()
                length_pt = sum(
                    math.hypot(
                        ln["pts"][-1][0] - ln["pts"][0][0],
                        ln["pts"][-1][1] - ln["pts"][0][1],
                    )
                    for ln in lines
                )
                curve_paths = [cv["path"] for cv in curves]
                for path in curve_paths:
                    length_pt += sum(
                        polyline_length(sp.points, sp.closed) for sp in subpaths(path)
                    )

                # Scale: only an explicit note on the same page is trusted.
                scan_text = text + "\n" + page_ocr_text
                scales = find_scales(scan_text)
                factor: Optional[float] = None
                if len(scales) == 1 and not is_not_to_scale(scan_text):
                    factor = scales[0].factor
                    pages_with_scale += 1
                    scale_labels.append(scales[0].label)
                elif scales:
                    ambiguous_scale_pages.append(number)

                swing_c, swing_ok = door_swing_count(curve_paths, factor)
                swing_candidates += swing_c
                swing_confirmed += swing_ok
                if factor:
                    real_path_ft += length_pt * factor / POINTS_PER_FOOT
                    segs, truncated = page_segments(page)
                    segments_truncated |= truncated
                    pt, pairs = paired_wall_length(segs, factor)
                    wall_ft += pt * factor / POINTS_PER_FOOT
                    wall_pairs += pairs
                    if has_text:
                        rooms.extend(
                            room_areas(
                                page_polygons(page), page.extract_words(), factor
                            )
                        )
                if has_text:
                    sched = door_schedule_count(page)
                    if sched is not None:
                        schedule_total = (schedule_total or 0.0) + sched.count
                        schedule_evidence.append(sched.evidence)
                timings["geometry_ms"] += (time.perf_counter() - t0) * 1000
            except Exception:
                failed_pages.append(number)
                continue

            pages_scanned += 1
            if has_text:
                texts.append(text)
                stats["pages_with_text"] += 1
            stats["image_only"] += bool(n_images and not has_text and not page_ocr_text)
            stats["images"] += n_images
            stats["lines"] += len(lines)
            stats["curves"] += len(curves)
            stats["rects"] += len(rects)
            path_pt += length_pt

    # ------------------------------------------------------------ warnings
    if failed_pages:
        warnings.append(f"Could not parse page(s) {failed_pages}; they were skipped")
        review.append("pages skipped")
    unread = pages_needing_ocr - len(ocr_pages) - vision_ok
    if unread > 0:
        if engine is None:
            warnings.append(
                f"No text layer found on {unread} page(s)"
                + (
                    f"; the PDF looks like scanned images ({stats['images']} embedded). "
                    if stats["images"]
                    else " (vector-only drawing with no text callouts). "
                )
                + "OCR is not installed (install Tesseract to read them)"
            )
        else:
            warnings.append(
                f"{unread} page(s) without a text layer were not OCR'd "
                f"(limit {config.OCR_MAX_PAGES} pages per request)"
            )
        review.append("pages not read")
    if ocr_confidences:
        mean_conf = sum(ocr_confidences) / len(ocr_confidences)
        if mean_conf < ocr_engine.LOW_CONFIDENCE:
            warnings.append(
                f"OCR confidence is low ({mean_conf:.0f}%); verify OCR-derived values"
            )
            review.append("low OCR confidence")
    else:
        mean_conf = None
    if ambiguous_scale_pages:
        warnings.append(
            f"Page(s) {ambiguous_scale_pages} have several scales or 'not to scale' "
            "notes; geometry on them was not converted to real units"
        )
        review.append("ambiguous scale")
    if segments_truncated:
        warnings.append(
            "Very dense geometry; wall pairing used only the first segments"
        )

    # ------------------------------------------------------------ findings
    findings: Dict[str, Finding] = {}
    for category, match in extract_matches("\n\n".join(ocr_texts)).items():
        findings[category] = Finding(
            category,
            match.quantity,
            "text",
            _confidence_for_ocr(mean_conf or 0.0),
            "OCR: " + match.snippet,
        )
    for category, match in extract_matches("\n\n".join(texts)).items():
        findings[category] = Finding(
            category, match.quantity, "text", "high", match.snippet
        )

    if schedule_total is not None:
        existing = findings.get("doors")
        if existing and existing.quantity != schedule_total:
            warnings.append(
                f"Door schedule ({schedule_total:g}) and text callout "
                f"({existing.quantity:g}) disagree; using the schedule"
            )
            review.append("door count conflict")
        findings["doors"] = Finding(
            "doors", schedule_total, "schedule", "high", "; ".join(schedule_evidence)
        )

    if "walls" not in findings and wall_pairs:
        findings["walls"] = Finding(
            "walls",
            round(wall_ft, 2),
            "geometry",
            "medium",
            f"{int(wall_pairs)} double-line wall segment pair(s) at drawn scale",
        )
    if "doors" not in findings and swing_confirmed:
        findings["doors"] = Finding(
            "doors",
            float(swing_confirmed),
            "geometry",
            "low",
            f"{swing_confirmed} door-swing arc(s) of plausible door width",
        )
    if "flooring" not in findings and rooms:
        findings["flooring"] = Finding(
            "flooring",
            round(sum(r.area_sqft for r in rooms), 2),
            "geometry",
            "low",
            "; ".join(f"{r.label} {r.area_sqft:.0f} sq ft" for r in rooms[:10]),
        )
    # Claude vision (opt-in) only fills categories nothing else found.
    for category, match in extract_matches("\n\n".join(vision_texts)).items():
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
    for category, found in vision_findings.items():
        findings.setdefault(category, found)
    for finding in findings.values():
        if finding.confidence != "high":
            review.append(
                f"{finding.category} is {finding.basis}-derived ({finding.confidence} confidence)"
            )

    if pages_scanned and not findings and (stats["pages_with_text"] or ocr_pages):
        warnings.append(
            "No walls/doors/flooring quantity with a compatible unit found in the text"
        )

    all_text = "\n\n".join(texts + ocr_texts)
    dims = find_dimensions(all_text)
    metadata = {
        "pages_total": pages_total,
        "pages_scanned": pages_scanned,
        "pages_with_text": stats["pages_with_text"],
        "image_count": stats["images"],
        "ocr": {
            "engine": engine.name if engine else None,
            "pages": ocr_pages,
            "mean_confidence": round(mean_conf, 1) if mean_conf is not None else None,
        },
        "vision": {
            "used": bool(vision_pages),
            "model": vision_model,
            "pages": vision_pages,
            "dimensions": vision_dims[:MAX_DIMENSIONS_REPORTED],
        },
        "pages_with_scale": pages_with_scale,
        "scales": sorted(set(scale_labels))[:10],
        "dimension_count": len(dims),
        "dimensions": [
            {"text": d.raw.strip(), "feet": round(d.feet, 4)}
            for d in dims[:MAX_DIMENSIONS_REPORTED]
        ],
        "vector_line_count": stats["lines"],
        "vector_curve_count": stats["curves"],
        "vector_rect_count": stats["rects"],
        "vector_path_length_ft_paper_space": round(path_pt / POINTS_PER_FOOT, 2),
        "vector_path_length_ft_real": (
            round(real_path_ft, 2) if pages_with_scale else None
        ),
        "door_swing_candidates": swing_candidates,
        "evidence": {c: f.evidence for c, f in findings.items()},
        "warnings": warnings,
        "review_reasons": sorted(set(review)),
        "needs_review": bool(review),
        "timings_ms": {k: round(v) for k, v in timings.items()},
        "note": (
            "Quantities come from text callouts (text layer or OCR), door schedules, "
            "or scaled geometry; see each item's basis and confidence. Paper-space "
            "lengths use 864 points per foot with no drawing scale; real lengths "
            "apply an explicit per-page scale note only."
        ),
    }
    return ScanResult(findings, metadata)
