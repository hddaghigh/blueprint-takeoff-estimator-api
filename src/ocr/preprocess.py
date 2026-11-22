"""Image clean-up before OCR: grayscale, upscale small scans, deskew, binarize."""

import numpy as np
from PIL import Image

MAX_SKEW_DEG = 5.0


def estimate_skew(image: Image.Image) -> float:
    """Skew angle in degrees (positive = text rotated counter-clockwise).

    Projection-profile search: the rotation that makes text rows sharpest
    (highest variance of the row sums) is the one that levels the page.
    """
    small = image.convert("L")
    small.thumbnail((800, 800))
    arr = np.asarray(small, dtype=np.uint8)
    ink = Image.fromarray(((arr < 128) * 255).astype(np.uint8))
    if np.count_nonzero(np.asarray(ink)) < 50:
        return 0.0
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-MAX_SKEW_DEG, MAX_SKEW_DEG + 0.01, 0.25):
        rotated = np.asarray(
            ink.rotate(angle, resample=Image.BILINEAR), dtype=np.float64
        )
        score = float(np.var(rotated.sum(axis=1)))
        if score > best_score:
            best_angle, best_score = float(angle), score
    return -best_angle


def deskew(image: Image.Image) -> Image.Image:
    angle = estimate_skew(image)
    if abs(angle) < 0.25:
        return image
    # `angle` is the text's skew, so rotate the opposite way to level it
    return image.rotate(
        -angle,
        resample=Image.BICUBIC,
        expand=True,
        fillcolor=255 if image.mode == "L" else (255, 255, 255),
    )


def prepare(
    image: Image.Image, min_width: int = 1600, binarize: bool = True
) -> Image.Image:
    """Grayscale, upscale small images, deskew, and (optionally) Otsu-binarize."""
    gray = image.convert("L")
    if gray.width < min_width:
        factor = min_width / gray.width
        gray = gray.resize(
            (int(gray.width * factor), int(gray.height * factor)), Image.LANCZOS
        )
    gray = deskew(gray)
    if not binarize:
        return gray
    arr = np.asarray(gray, dtype=np.uint8)
    hist = np.bincount(arr.ravel(), minlength=256).astype(np.float64)
    total = arr.size
    sum_all = float(np.dot(np.arange(256), hist))
    w_b = sum_b = 0.0
    best_var, threshold = -1.0, 127
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b, m_f = sum_b / w_b, (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best_var:
            best_var, threshold = var, t
    return Image.fromarray(((arr > threshold) * 255).astype(np.uint8))
