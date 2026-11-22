"""Background takeoff jobs: submit a large document, poll for the result."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src import jobs, pipelines
from src.schemas import TakeoffResponse

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.post("/takeoffs", status_code=202)
async def submit_takeoff_job(
    file: UploadFile = File(...),
    units: Optional[str] = Form(default=None),
    wall_representation: str = Form(default="centerline"),
    use_vision: bool = Form(default=False),
) -> Dict[str, Any]:
    """Queue a takeoff for any supported file type; returns a job id at once.

    The file is validated and its type detected before queueing, so obviously
    bad uploads fail immediately (413/422) instead of becoming failed jobs.
    """
    raw = await pipelines.read_upload(file)
    name = file.filename or "upload"
    kind = pipelines.detect_kind(raw, name)
    job_id = jobs.submit(
        pipelines.run_takeoff, raw, name, units, wall_representation, use_vision
    )
    return {"job_id": job_id, "status": "queued", "kind": kind}


@router.get("/{job_id}")
async def get_job(job_id: str) -> Dict[str, Any]:
    """status: queued | running | done | failed. `result` holds the takeoff when done."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404, detail="Unknown job id (jobs are kept in memory only)"
        )
    result = job["result"]
    return {
        "job_id": job_id,
        "status": job["status"],
        "result": (
            result.model_dump() if isinstance(result, TakeoffResponse) else result
        ),
        "error": job["error"],
    }
