"""Background jobs, health/capabilities and file-type detection."""

import asyncio

import pytest

from src.pipelines import detect_kind
from src.ocr import engine as ocr
from tests.test_ocr import FakeEngine


async def wait_for(client, job_id, timeout=20.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
        if job["status"] in ("done", "failed"):
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("job did not finish in time")


@pytest.mark.asyncio
async def test_job_runs_in_background_and_matches_the_sync_result(client, make_pdf):
    pdf = make_pdf(
        lambda c, _: c.drawString(
            72, 700, "Install 4 doors and 120 linear feet of walls."
        )
    )

    with open(pdf, "rb") as fh:
        submitted = await client.post(
            "/api/v1/jobs/takeoffs", files={"file": ("a.pdf", fh, "application/pdf")}
        )
    assert submitted.status_code == 202
    queued = submitted.json()
    assert queued["status"] == "queued" and queued["kind"] == "pdf"

    job = await wait_for(client, queued["job_id"])
    with open(pdf, "rb") as fh:
        sync = (
            await client.post(
                "/api/v1/takeoffs/drawing-pdf",
                files={"file": ("a.pdf", fh, "application/pdf")},
            )
        ).json()

    assert job["status"] == "done" and job["error"] is None
    assert job["result"]["items"] == sync["items"]


@pytest.mark.asyncio
async def test_failed_job_reports_the_error(client, tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\nnot really a pdf")

    with open(bad, "rb") as fh:
        queued = (
            await client.post(
                "/api/v1/jobs/takeoffs",
                files={"file": ("bad.pdf", fh, "application/pdf")},
            )
        ).json()
    job = await wait_for(client, queued["job_id"])

    assert job["status"] == "failed" and job["result"] is None
    assert job["error"]["status_code"] == 422


@pytest.mark.asyncio
async def test_job_submission_validates_up_front(client, monkeypatch):
    blob = await client.post(
        "/api/v1/jobs/takeoffs",
        files={"file": ("b.bin", b"\x00\x01" * 50, "application/octet-stream")},
    )
    monkeypatch.setattr("src.config.MAX_UPLOAD_MB", 0)
    big = await client.post(
        "/api/v1/jobs/takeoffs", files={"file": ("a.txt", b"x", "text/plain")}
    )
    unknown = await client.get("/api/v1/jobs/does-not-exist")

    assert blob.status_code == 422
    assert big.status_code == 413
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_health_reports_capabilities(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "set")
    ocr.set_engine(None)
    without = (await client.get("/health")).json()
    ocr.set_engine(FakeEngine())
    with_engine = (await client.get("/health")).json()

    assert without["status"] == "ok" and without["ocr"] == {
        "available": False,
        "engine": None,
    }
    assert with_engine["ocr"] == {"available": True, "engine": "fake"}
    assert with_engine["vision"]["configured"] is True
    assert with_engine["limits"]["max_pdf_pages"] >= 1


@pytest.mark.parametrize(
    "raw, name, kind",
    [
        (b"%PDF-1.7 ...", "x", "pdf"),
        (b"\xff\xd8\xff\xe0 jpeg", "x", "photo"),
        (b"\x89PNG\r\n\x1a\n....", "x", "photo"),
        (b"  0\nSECTION\n  2\nHEADER", "plan.dxf", "cad"),
        (b"AC1032\x00\x00", "plan.bin", "cad"),  # real DWG signature
        (
            b"ACME scope: install 4 doors",
            "scope.txt",
            "text",
        ),  # starts with AC but is text
        (b"Install 4 doors", "scope.txt", "text"),
    ],
)
def test_detect_kind(raw, name, kind):
    assert detect_kind(raw, name) == kind
