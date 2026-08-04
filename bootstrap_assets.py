"""Isolated setup script that establishes real construction documentation assets.

Run this once before starting the API:

    python bootstrap_assets.py

It populates the root-level `assets/` directory with:
  - scope.txt              a DDC-CWICR benchmark validation scope narrative
  - sample_blueprint.pdf   a real PDF downloaded from datadrivenconstruction.io
  - sample_site_photo.jpg  a real JPEG photo downloaded from raw.githubusercontent.com

All three files are consumed directly by the API's multi-modal takeoff pipelines
and by the integration test suite in tests/.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

SCOPE_TEXT = (
    "Project Scope: Install commercial resilient vinyl tile flooring across a "
    "1500 sq ft main interior hall. Erect 120 linear feet of 8-inch concrete "
    "masonry unit block walls along the structural boundary. Fit 4 standard "
    "pre-hung commercial hollow metal doors."
)

# Real DataDrivenConstruction free guidebook PDF (English edition), served from
# the datadrivenconstruction.io domain, used here as a real-world PDF with
# extractable text/vector content for the drawing-pdf pipeline to parse.
BLUEPRINT_PDF_URL = "https://datadrivenconstruction.io/download/4936/?tmstv=1744488957"

# Real JPEG photo served from raw.githubusercontent.com, used as a stand-in
# construction site photo for the photo takeoff pipeline to parse.
SITE_PHOTO_URL = (
    "https://raw.githubusercontent.com/yavuzceliker/sample-images/main/docs/image-1.jpg"
)

REQUEST_HEADERS = {
    "User-Agent": "blueprint-takeoff-estimator-api/1.0 (+https://github.com)"
}


def _download(url: str, destination: Path, min_bytes: int = 1024) -> None:
    print(f"Downloading {url} -> {destination}")
    with requests.get(
        url, headers=REQUEST_HEADERS, stream=True, timeout=60
    ) as response:
        response.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "wb") as fh:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)

    size = destination.stat().st_size
    if size < min_bytes:
        raise RuntimeError(
            f"Downloaded file {destination} is only {size} bytes; expected a real asset."
        )
    print(f"  -> saved {size:,} bytes")


def write_scope_txt() -> None:
    path = ASSETS_DIR / "scope.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SCOPE_TEXT, encoding="utf-8")
    print(f"Wrote {path} ({len(SCOPE_TEXT)} chars)")


def download_blueprint_pdf() -> None:
    destination = ASSETS_DIR / "sample_blueprint.pdf"
    _download(BLUEPRINT_PDF_URL, destination, min_bytes=1024)
    with open(destination, "rb") as fh:
        header = fh.read(5)
    if header != b"%PDF-":
        raise RuntimeError(
            f"{destination} does not start with a PDF header ({header!r}); "
            "the download did not return a real PDF."
        )


def download_site_photo() -> None:
    destination = ASSETS_DIR / "sample_site_photo.jpg"
    _download(SITE_PHOTO_URL, destination, min_bytes=1024)
    with open(destination, "rb") as fh:
        header = fh.read(3)
    if header != b"\xff\xd8\xff":
        raise RuntimeError(
            f"{destination} does not start with a JPEG header; "
            "the download did not return a real JPEG."
        )


def main() -> int:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    write_scope_txt()
    try:
        download_blueprint_pdf()
        download_site_photo()
    except (requests.RequestException, RuntimeError) as exc:
        print(f"ERROR: asset bootstrap failed: {exc}", file=sys.stderr)
        return 1
    print("Asset bootstrap complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
