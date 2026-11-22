"""FastAPI entrypoint for the blueprint-takeoff-estimator-api."""

import os

from fastapi import FastAPI

from src import cad, config
from src.ocr import engine as ocr_engine
from src.routes import estimates, jobs, takeoffs

app = FastAPI(
    title="Blueprint Takeoff Estimator API",
    description=(
        "Multi-modal construction takeoff and cost estimation API. Reads scope "
        "text, jobsite photos, PDF drawings (text layer, OCR, scale, geometry, "
        "schedules) and DXF/DWG drawings, and prices the resulting takeoff "
        "against the local DDC-CWICR rate table."
    ),
    version="2.0.0",
)

app.include_router(takeoffs.router)
app.include_router(estimates.router)
app.include_router(jobs.router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Liveness plus which optional capabilities are available on this server."""
    engine = ocr_engine.get_engine()
    return {
        "status": "ok",
        "ocr": {
            "available": engine is not None,
            "engine": engine.name if engine else None,
        },
        "vision": {
            "configured": bool(
                os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            ),
            "model": os.environ.get("VISION_MODEL", "claude-sonnet-5-5"),
        },
        "dwg_converter": bool(cad.dwg_converters()),
        "dwg_converters": [name for name, _ in cad.dwg_converters()],
        "limits": {
            "max_upload_mb": config.MAX_UPLOAD_MB,
            "max_pdf_pages": config.MAX_PDF_PAGES,
            "ocr_max_pages": config.OCR_MAX_PAGES,
        },
    }
