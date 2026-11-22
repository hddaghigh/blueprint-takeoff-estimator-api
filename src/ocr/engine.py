"""OCR engines behind one interface. Missing engines degrade, never crash.

Default engine is Tesseract (local, offline). Set TESSERACT_CMD to point at a
specific binary. `set_engine()` lets tests inject a stand-in at this boundary.
"""

import os
import shutil
from dataclasses import dataclass
from typing import List, Optional, Protocol

from PIL import Image

MIN_WORD_CONFIDENCE = 60.0
LOW_CONFIDENCE = 75.0  # mean confidence below this => results need human review

_WINDOWS_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


@dataclass
class OcrResult:
    text: str
    mean_confidence: float
    word_count: int


class OcrEngine(Protocol):
    name: str

    def read(self, image: Image.Image) -> OcrResult: ...


def find_tesseract() -> Optional[str]:
    explicit = os.environ.get("TESSERACT_CMD")
    if explicit and os.path.isfile(explicit):
        return explicit
    found = shutil.which("tesseract")
    if found:
        return found
    return next((p for p in _WINDOWS_PATHS if os.path.isfile(p)), None)


class TesseractEngine:
    name = "tesseract"

    def __init__(self, command: str):
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = command
        self._pt = pytesseract

    def _pass(self, image: Image.Image, psm: int) -> List[tuple]:
        data = self._pt.image_to_data(
            image, config=f"--psm {psm}", output_type=self._pt.Output.DICT
        )
        words = []
        for i, text in enumerate(data["text"]):
            conf = float(data["conf"][i])
            if text.strip() and conf >= MIN_WORD_CONFIDENCE:
                key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
                words.append((key, text, conf))
        return words

    @staticmethod
    def _lines(words: List[tuple]) -> str:
        lines, last = [], None
        for key, text, _ in words:
            if key != last:
                lines.append([])
                last = key
            lines[-1].append(text)
        return "\n".join(" ".join(line) for line in lines)

    def read(self, image: Image.Image) -> OcrResult:
        # upright text, then rotated 90 degrees for vertical dimension strings
        upright = self._pass(image, 6)
        rotated = self._pass(image.rotate(90, expand=True), 6)
        words = upright + rotated
        if not words:
            return OcrResult("", 0.0, 0)
        text = "\n".join(t for t in (self._lines(upright), self._lines(rotated)) if t)
        mean = sum(w[2] for w in words) / len(words)
        return OcrResult(text, mean, len(words))


_override: Optional[OcrEngine] = None
_use_override = False
_cached: Optional[OcrEngine] = None
_cached_checked = False


def set_engine(engine: Optional[OcrEngine], enabled: bool = True) -> None:
    """Inject an engine (or None to force 'no engine'). `enabled=False` resets."""
    global _override, _use_override
    _override, _use_override = engine, enabled


def get_engine() -> Optional[OcrEngine]:
    """The active OCR engine, or None when none is installed."""
    global _cached, _cached_checked
    if _use_override:
        return _override
    if not _cached_checked:
        _cached = _build_engine(os.environ.get("OCR_ENGINE", "auto").lower())
        _cached_checked = True
    return _cached


def _make_rapidocr() -> Optional[OcrEngine]:
    try:
        from src.ocr.rapid import RapidOcrEngine

        return RapidOcrEngine()
    except Exception:  # not installed, or its runtime failed to load
        return None


def _make_tesseract() -> Optional[OcrEngine]:
    command = find_tesseract()
    return TesseractEngine(command) if command else None


def _build_engine(mode: str) -> Optional[OcrEngine]:
    """OCR_ENGINE: auto (default) | rapidocr | tesseract | none.

    `auto` prefers RapidOCR (pip-installable, the engine the accuracy numbers
    in the README were measured on) and falls back to a Tesseract binary.
    """
    makers = {"rapidocr": _make_rapidocr, "tesseract": _make_tesseract}
    if mode == "none":
        return None
    for name in ("rapidocr", "tesseract") if mode == "auto" else (mode,):
        maker = makers.get(name)
        engine = maker() if maker else None
        if engine is not None:
            return engine
    return None
