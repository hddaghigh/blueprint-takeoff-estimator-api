"""RapidOCR engine (ONNX runtime, pip-installable, no system binary needed).

Reading strategy, chosen by measurement on synthetic scans of known text:
  1. one pass over the whole page,
  2. plus overlapping horizontal bands (a second look at a different scale),
  3. merged so that, where boxes overlap, the LONGEST text wins (engines
     sometimes clip a line's tail; the other pass usually has it whole),
  4. page orientation is tried at 90/270 degrees when the upright read finds
     almost no text (landscape sheets scanned sideways).
Spacing errors are repaired afterwards by `normalize.unglue`.

NEVER pass keyword arguments such as `unclip_ratio` per call: RapidOCR keeps
them on the instance, silently changing every later read.
"""

import threading
from dataclasses import dataclass
from typing import List

import numpy as np
from PIL import Image

from src.ocr.normalize import unglue

BAND_PX = 900
BAND_OVERLAP = 0.25
MIN_LINE_CONFIDENCE = 0.5  # 0..1, below this a line is dropped as noise
MIN_ALPHA_FOR_UPRIGHT = 30  # fewer letters than this => try other orientations


@dataclass
class Line:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    conf: float  # 0..1

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


def _overlaps(a: Line, b: Line) -> bool:
    ix = min(a.x1, b.x1) - max(a.x0, b.x0)
    iy = min(a.y1, b.y1) - max(a.y0, b.y0)
    if ix <= 0 or iy <= 0:
        return False
    smaller = min((a.x1 - a.x0) * (a.y1 - a.y0), (b.x1 - b.x0) * (b.y1 - b.y0))
    return ix * iy >= 0.5 * smaller


def merge_lines(lines: List[Line]) -> List[Line]:
    """Collapse overlapping detections of the same line, keeping the longest text."""
    kept: List[Line] = []
    for line in sorted(lines, key=lambda l: (-len(l.text), -l.conf)):
        if not any(_overlaps(line, k) for k in kept):
            kept.append(line)
    return sorted(kept, key=lambda l: (round(l.cy / 12), l.x0))


def _spans(height: int):
    if height <= BAND_PX * 1.2:
        return []
    step = int(BAND_PX * (1 - BAND_OVERLAP))
    spans, y = [], 0
    while True:
        spans.append((y, min(height, y + BAND_PX)))
        if y + BAND_PX >= height:
            return spans
        y += step


class RapidOcrEngine:
    name = "rapidocr"
    wants_binary = False  # reads grayscale better than hard-thresholded pages

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR

        self._ocr = RapidOCR()
        self._lock = threading.Lock()  # one read at a time; sessions are shared

    def _detect(self, arr: np.ndarray, y_offset: int = 0) -> List[Line]:
        with self._lock:
            result, _ = self._ocr(arr)
        lines = []
        for box, text, conf in result or []:
            conf = float(conf)
            if not text.strip() or conf < MIN_LINE_CONFIDENCE:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] + y_offset for p in box]
            lines.append(Line(min(xs), min(ys), max(xs), max(ys), text, conf))
        return lines

    def _read_upright(self, arr: np.ndarray) -> List[Line]:
        lines = self._detect(arr)
        for a, b in _spans(arr.shape[0]):
            lines += self._detect(arr[a:b], a)
        return merge_lines(lines)

    @staticmethod
    def _alpha(lines: List[Line]) -> int:
        return sum(ch.isalpha() for l in lines for ch in l.text)

    def read(self, image: Image.Image):
        from src.ocr.engine import OcrResult

        arr = np.asarray(image.convert("RGB"))
        best = self._read_upright(arr)
        if self._alpha(best) < MIN_ALPHA_FOR_UPRIGHT:
            for angle in (90, 270):
                turned = np.asarray(Image.fromarray(arr).rotate(angle, expand=True))
                candidate = self._read_upright(turned)
                if self._alpha(candidate) > 2 * max(self._alpha(best), 1):
                    best = candidate
        if not best:
            return OcrResult("", 0.0, 0)
        # one text row per output line: join boxes that share a row
        rows: List[List[Line]] = []
        for line in best:
            if rows and abs(line.cy - rows[-1][0].cy) < 0.6 * max(line.y1 - line.y0, 1):
                rows[-1].append(line)
            else:
                rows.append([line])
        text = "\n".join(" ".join(l.text for l in row) for row in rows)
        mean = 100.0 * sum(l.conf for l in best) / len(best)
        return OcrResult(unglue(text), mean, sum(len(l.text.split()) for l in best))
