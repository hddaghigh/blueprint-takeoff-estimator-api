"""Loader for the local DDC-CWICR rate database."""

import json
from functools import lru_cache
from typing import Dict

from src.config import DDC_CWICR_PATH


@lru_cache(maxsize=1)
def get_ddc_cwicr_rates() -> Dict[str, dict]:
    """Load and cache the DDC-CWICR category rate table from disk."""
    with open(DDC_CWICR_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)
