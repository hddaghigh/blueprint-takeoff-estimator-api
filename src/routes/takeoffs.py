"""Multi-modal takeoff input pipelines: text, photo, and drawing PDF."""

from pathlib import Path
from typing import Optional, Tuple

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from src import cad, pipelines
from src.config import SAMPLE_BLUEPRINT_PDF_PATH, SAMPLE_SITE_PHOTO_PATH, SCOPE_TXT_PATH
from src.schemas import TakeoffResponse

router = APIRouter(prefix="/api/v1/takeoffs", tags=["takeoffs"])


async def load_input(
    file: Optional[UploadFile], default_path: Path, default_label: str
) -> Tuple[bytes, str]:
    """The uploaded bytes, or the bundled reference asset when nothing is uploaded."""
    if file is not None:
        return await pipelines.read_upload(file), file.filename or "upload"
    if not default_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"{default_label} not found; run bootstrap_assets.py",
        )
    return default_path.read_bytes(), str(default_path)


@router.post("/text", response_model=TakeoffResponse)
async def takeoff_from_text(
    file: Optional[UploadFile] = File(default=None),
) -> TakeoffResponse:
    """Isolate wall/door/flooring quantities from a scope-of-work text file.

    With no upload, streams the bundled assets/scope.txt reference asset.
    """
    raw, source = await load_input(file, SCOPE_TXT_PATH, "assets/scope.txt")
    return pipelines.process_text(raw, source)


@router.post("/photo", response_model=TakeoffResponse)
async def takeoff_from_photo(
    file: Optional[UploadFile] = File(default=None),
    use_vision: bool = Form(default=False),
) -> TakeoffResponse:
    """Decode a jobsite photo (RGB matrix + luminance) and read visible text."""
    raw, source = await load_input(
        file, SAMPLE_SITE_PHOTO_PATH, "assets/sample_site_photo.jpg"
    )
    return await run_in_threadpool(pipelines.process_photo, raw, source, use_vision)


@router.post("/drawing-cad", response_model=TakeoffResponse)
async def takeoff_from_cad(
    file: UploadFile = File(...),
    units: Optional[str] = Form(default=None),
    wall_representation: str = Form(default="centerline"),
    include_blocks: Optional[str] = Form(default=None),
) -> TakeoffResponse:
    """Quantities from a DXF drawing (DWG via a configured converter).

    Walls, doors and flooring come from layers, named blocks and closed shapes
    (rules in src/database/layer_rules.json), converted to feet using the
    drawing's units. `units` (in|ft|mm|cm|m) is required for unitless drawings;
    `wall_representation` is `centerline` (default) or `double_line`.
    `include_blocks` (comma-separated globs, e.g. `1F-BLOCK_100`) restricts the
    read to those top-level blocks: use it to take off one floor of a drawing
    that holds several (the response lists `top_level_blocks`).
    """
    raw = await pipelines.read_upload(file)
    return await run_in_threadpool(
        cad.process_cad,
        raw,
        file.filename or "upload.dxf",
        units,
        wall_representation,
        include_blocks,
    )


@router.post("/drawing-pdf", response_model=TakeoffResponse)
async def takeoff_from_pdf(
    file: Optional[UploadFile] = File(default=None),
    use_vision: bool = Form(default=False),
) -> TakeoffResponse:
    """Read a PDF's text layer (OCR for scanned pages), scale, geometry and schedules.

    Every item reports its `basis` and `confidence`; limits and problems are in
    `metadata.warnings`. Unreadable, corrupt or password-protected files return 422.
    """
    raw, source = await load_input(
        file, SAMPLE_BLUEPRINT_PDF_PATH, "assets/sample_blueprint.pdf"
    )
    return await run_in_threadpool(pipelines.process_pdf, raw, source, use_vision)
