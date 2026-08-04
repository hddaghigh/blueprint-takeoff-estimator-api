"""FastAPI entrypoint for the blueprint-takeoff-estimator-api."""

from fastapi import FastAPI

from src.routes import estimates, takeoffs

app = FastAPI(
    title="Blueprint Takeoff Estimator API",
    description=(
        "Multi-modal construction takeoff and cost estimation API. Ingests "
        "scope text, jobsite photos, and drawing PDFs, and prices the "
        "resulting takeoff against the local DDC-CWICR rate database."
    ),
    version="1.0.0",
)

app.include_router(takeoffs.router)
app.include_router(estimates.router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}
