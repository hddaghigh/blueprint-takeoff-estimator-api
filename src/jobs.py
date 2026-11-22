"""In-memory background jobs for large documents (no external queue needed).

Jobs live in this process only: they are lost on restart and are not shared
between worker processes. For multi-process deployments put the same
interface on a real queue (Redis/RQ, Celery).
"""

import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException

MAX_STORED_JOBS = 200
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="takeoff-job")
_lock = threading.Lock()
_jobs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def _evict() -> None:
    while len(_jobs) > MAX_STORED_JOBS:
        oldest_done = next(
            (k for k, v in _jobs.items() if v["status"] in ("done", "failed")), None
        )
        if oldest_done is None:
            break
        del _jobs[oldest_done]


def submit(fn: Callable[..., Any], *args: Any) -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {
            "status": "queued",
            "result": None,
            "error": None,
            "created": time.time(),
        }
        _evict()

    def run() -> None:
        with _lock:
            _jobs[job_id]["status"] = "running"
        try:
            result = fn(*args)
            outcome = {"status": "done", "result": result, "error": None}
        except HTTPException as exc:
            outcome = {
                "status": "failed",
                "result": None,
                "error": {"status_code": exc.status_code, "detail": exc.detail},
            }
        except Exception as exc:  # a job must always end in a terminal state
            outcome = {
                "status": "failed",
                "result": None,
                "error": {"status_code": 500, "detail": f"{type(exc).__name__}: {exc}"},
            }
        with _lock:
            if job_id in _jobs:
                _jobs[job_id].update(outcome)

    _executor.submit(run)
    return job_id


def get(job_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None
