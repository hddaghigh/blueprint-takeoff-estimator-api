"""Optional Claude vision pass for drawings and photos (opt-in per request).

Sends the image to the Anthropic API ONLY when the caller sets use_vision=true.
Documents leave this machine in that case. Everything vision returns is marked
basis="vision", confidence="low": it is a model's reading, not a measurement.

Model: VISION_MODEL (default claude-sonnet-5-5). Sonnet 5.5 rejects forced
`tool_choice`, so structured JSON comes from `output_config.format` instead,
and server-side refusal fallbacks are enabled.
"""

import base64
import io
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from PIL import Image

from src.database import get_ddc_cwicr_rates
from src.findings import Finding

DEFAULT_MODEL = "claude-sonnet-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_LONG_SIDE_PX = 1568  # larger images are downscaled by the API anyway
MAX_OUTPUT_TOKENS = 4000

SYSTEM_PROMPT = (
    "You read construction drawings and jobsite photos for a quantity takeoff. "
    "Report only what is visible or written. Never guess a measurement: give a "
    "quantity only when it can be counted (doors) or when a dimension or area is "
    "legible (walls in linear feet, flooring in square feet). Copy legible text "
    "and dimension strings verbatim. If nothing qualifies, return empty lists."
)

_client: Optional[Any] = None
_use_client = False


def set_client(client: Optional[Any], enabled: bool = True) -> None:
    """Inject an Anthropic-compatible client (tests). `enabled=False` resets."""
    global _client, _use_client
    _client, _use_client = client, enabled


@dataclass
class VisionResult:
    findings: Dict[str, Finding] = field(default_factory=dict)
    text_found: List[str] = field(default_factory=list)
    dimensions: List[dict] = field(default_factory=list)
    notes: str = ""
    warnings: List[str] = field(default_factory=list)
    model: str = DEFAULT_MODEL


def build_schema() -> dict:
    categories = sorted(get_ddc_cwicr_rates())
    return {
        "type": "object",
        "properties": {
            "elements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": categories},
                        "quantity": {"type": "number"},
                        "basis": {"type": "string"},
                    },
                    "required": ["category", "quantity", "basis"],
                    "additionalProperties": False,
                },
            },
            "text_found": {"type": "array", "items": {"type": "string"}},
            "dimensions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "feet": {"type": "number"},
                    },
                    "required": ["text", "feet"],
                    "additionalProperties": False,
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["elements", "text_found", "dimensions", "notes"],
        "additionalProperties": False,
    }


def prepare_image(image: Image.Image) -> bytes:
    """Downscale and JPEG-encode (keeps the request small and within API limits)."""
    rgb = image.convert("RGB")
    rgb.thumbnail((MAX_LONG_SIDE_PX, MAX_LONG_SIDE_PX))
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _get_client():
    if _use_client:
        return _client
    import anthropic

    return anthropic.Anthropic()  # ANTHROPIC_API_KEY / ant auth profile


def analyze_image(image: Image.Image, context: str = "") -> VisionResult:
    """Ask Claude to read one image. Never raises: problems become warnings."""
    model = os.environ.get("VISION_MODEL", DEFAULT_MODEL)
    result = VisionResult(model=model)
    try:
        client = _get_client()
        if client is None:
            raise RuntimeError("no client configured")
        data = base64.standard_b64encode(prepare_image(image)).decode("ascii")
        response = client.beta.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            output_config={"format": {"type": "json_schema", "schema": build_schema()}},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": data,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Read this image for a construction takeoff."
                            + (f" Context: {context}" if context else ""),
                        },
                    ],
                }
            ],
        )
    except Exception as exc:
        result.warnings.append(f"Vision analysis failed: {type(exc).__name__}: {exc}")
        return result

    if response.stop_reason == "refusal":
        result.warnings.append("Vision analysis was declined by the model")
        return result
    if response.stop_reason == "max_tokens":
        result.warnings.append(
            "Vision analysis was cut off (max_tokens); results may be incomplete"
        )
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        result.warnings.append("Vision analysis returned unreadable output")
        return result

    rates = get_ddc_cwicr_rates()
    for element in payload.get("elements", []):
        category, quantity = element.get("category"), element.get("quantity")
        if category in rates and isinstance(quantity, (int, float)) and quantity > 0:
            if category not in result.findings:
                result.findings[category] = Finding(
                    category,
                    float(quantity),
                    "vision",
                    "low",
                    f"Claude vision ({model}): {element.get('basis', '')}"[:200],
                )
    result.text_found = [t for t in payload.get("text_found", []) if isinstance(t, str)]
    result.dimensions = [
        d for d in payload.get("dimensions", []) if isinstance(d, dict) and "text" in d
    ]
    result.notes = str(payload.get("notes", ""))
    return result
