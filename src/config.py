"""Shared filesystem paths for the API."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PROJECT_ROOT / "assets"
DDC_CWICR_PATH = Path(__file__).resolve().parent / "database" / "ddc_cwicr.json"

SCOPE_TXT_PATH = ASSETS_DIR / "scope.txt"
SAMPLE_BLUEPRINT_PDF_PATH = ASSETS_DIR / "sample_blueprint.pdf"
SAMPLE_SITE_PHOTO_PATH = ASSETS_DIR / "sample_site_photo.jpg"
