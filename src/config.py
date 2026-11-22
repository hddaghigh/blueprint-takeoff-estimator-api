"""Shared filesystem paths and runtime limits for the API."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PROJECT_ROOT / "assets"
DDC_CWICR_PATH = Path(__file__).resolve().parent / "database" / "ddc_cwicr.json"
LAYER_RULES_PATH = Path(__file__).resolve().parent / "database" / "layer_rules.json"

SCOPE_TXT_PATH = ASSETS_DIR / "scope.txt"
SAMPLE_BLUEPRINT_PDF_PATH = ASSETS_DIR / "sample_blueprint.pdf"
SAMPLE_SITE_PHOTO_PATH = ASSETS_DIR / "sample_site_photo.jpg"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# Limits (override with environment variables). Read at call time through the
# module attribute (e.g. `config.MAX_PDF_PAGES`) so tests can patch them.
MAX_PDF_PAGES = _int_env("MAX_PDF_PAGES", 100)
MAX_UPLOAD_MB = _int_env("MAX_UPLOAD_MB", 50)
OCR_MAX_PAGES = _int_env("OCR_MAX_PAGES", 20)
VISION_MAX_PAGES = _int_env("VISION_MAX_PAGES", 5)
