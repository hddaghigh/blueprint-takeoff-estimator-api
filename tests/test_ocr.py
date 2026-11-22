"""OCR orchestration tests.

A stand-in engine is injected at the OcrEngine boundary (src/ocr/engine.py);
rasterization, preprocessing, text/scale/geometry handling and failure paths
around it are all real. Tests of the real Tesseract binary are marked
`ocr_engine` (see pytest.ini) and need Tesseract installed.
"""

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from src.ocr import engine as ocr
from src.ocr.preprocess import deskew, estimate_skew, prepare
from tests.test_pdf_robustness import quantities, upload_pdf

SCAN_TEXT = "Install 4 doors and 120 linear feet of walls."


class FakeEngine:
    name = "fake"

    def __init__(self, text=SCAN_TEXT, confidence=90.0, error=None):
        self.text, self.confidence, self.error = text, confidence, error
        self.images = []

    def read(self, image):
        self.images.append(image)
        if self.error:
            raise self.error
        return ocr.OcrResult(self.text, self.confidence, len(self.text.split()))


@pytest.fixture
def scanned_pdf(tmp_path):
    """An image-only PDF (no text layer) with real dark pixels on the page."""
    page = Image.new("RGB", (1700, 2200), "white")
    ImageDraw.Draw(page).rectangle([200, 200, 1500, 600], outline="black", width=8)
    path = tmp_path / "scan.pdf"
    page.save(path, "PDF", resolution=200)
    return path


@pytest.mark.asyncio
async def test_scanned_pdf_is_ocrd_and_quantities_extracted(client, scanned_pdf):
    engine = FakeEngine()
    ocr.set_engine(engine)

    body = (await upload_pdf(client, scanned_pdf)).json()

    assert quantities(body) == {"doors": 4.0, "walls": 120.0}
    for it in body["items"]:
        assert it["basis"] == "text"
        assert it["confidence"] == "medium"
        assert it["evidence"].startswith("OCR:")
    meta = body["metadata"]
    assert meta["ocr"] == {"engine": "fake", "pages": [1], "mean_confidence": 90.0}
    assert not any("scanned" in w for w in meta["warnings"])


@pytest.mark.asyncio
async def test_engine_receives_a_real_rendered_preprocessed_page(client, scanned_pdf):
    engine = FakeEngine()
    ocr.set_engine(engine)

    await upload_pdf(client, scanned_pdf)

    (image,) = engine.images
    pixels = np.asarray(image)
    assert image.mode == "L" and image.width >= 1600
    assert set(np.unique(pixels)) <= {0, 255}  # binarized
    assert (pixels == 0).any() and (pixels == 255).any()  # drawn rectangle survived


@pytest.mark.asyncio
async def test_low_ocr_confidence_is_flagged_for_review(client, scanned_pdf):
    ocr.set_engine(FakeEngine(confidence=50.0))

    body = (await upload_pdf(client, scanned_pdf)).json()

    assert all(i["confidence"] == "low" for i in body["items"])
    assert any("OCR confidence is low" in w for w in body["metadata"]["warnings"])
    assert body["metadata"]["needs_review"] is True


@pytest.mark.asyncio
async def test_ocr_failure_never_breaks_the_request(client, scanned_pdf):
    ocr.set_engine(FakeEngine(error=RuntimeError("engine exploded")))

    response = await upload_pdf(client, scanned_pdf)

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert any("OCR failed on page 1" in w for w in body["metadata"]["warnings"])


@pytest.mark.asyncio
async def test_pages_with_a_text_layer_are_not_ocrd(client, make_pdf):
    engine = FakeEngine()
    ocr.set_engine(engine)

    def draw(c, _):
        c.drawString(72, 700, "Install 4 doors.")

    body = (await upload_pdf(client, make_pdf(draw))).json()

    assert engine.images == []
    assert quantities(body) == {"doors": 4.0}
    assert body["items"][0]["confidence"] == "high"


@pytest.mark.asyncio
async def test_ocr_page_limit_is_reported(client, tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.OCR_MAX_PAGES", 1)
    engine = FakeEngine()
    ocr.set_engine(engine)
    pages = [Image.new("RGB", (800, 1000), "white") for _ in range(3)]
    path = tmp_path / "three.pdf"
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:])

    body = (await upload_pdf(client, path)).json()

    assert len(engine.images) == 1
    assert any("not OCR'd" in w for w in body["metadata"]["warnings"])


@pytest.mark.asyncio
async def test_ocr_text_scale_applies_to_outlined_vector_geometry(client, make_pdf):
    """CAD sheets often outline their text: lines are vectors, the scale note is not."""
    ocr.set_engine(FakeEngine(text='SCALE: 1/4" = 1\'-0"'))

    def draw(c, _):  # no drawString at all: no text layer
        c.line(100, 500, 460, 500)
        c.line(100, 509, 460, 509)

    body = (await upload_pdf(client, make_pdf(draw, pagesize=(1000, 800)))).json()
    walls = next(i for i in body["items"] if i["category"] == "walls")

    assert walls["quantity"] == pytest.approx(20.0, abs=0.1)
    assert body["metadata"]["ocr"]["pages"] == [1]


@pytest.mark.asyncio
async def test_no_engine_installed_degrades_with_a_warning(client, scanned_pdf):
    ocr.set_engine(None)

    response = await upload_pdf(client, scanned_pdf)

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["metadata"]["ocr"]["engine"] is None
    assert any("OCR is not installed" in w for w in body["metadata"]["warnings"])


@pytest.mark.asyncio
async def test_photo_text_is_read_with_ocr(client, sample_photo_path):
    ocr.set_engine(FakeEngine(text="SITE NOTE: provide 4 doors"))
    with open(sample_photo_path, "rb") as fh:
        body = (
            await client.post(
                "/api/v1/takeoffs/photo", files={"file": ("p.jpg", fh, "image/jpeg")}
            )
        ).json()

    assert quantities(body) == {"doors": 4.0}
    assert body["items"][0]["confidence"] == "medium"
    assert body["metadata"]["ocr"]["engine"] == "fake"


@pytest.mark.asyncio
async def test_photo_without_engine_warns(client, sample_photo_path):
    with open(sample_photo_path, "rb") as fh:
        body = (
            await client.post(
                "/api/v1/takeoffs/photo", files={"file": ("p.jpg", fh, "image/jpeg")}
            )
        ).json()

    assert any("OCR is not installed" in w for w in body["metadata"]["warnings"])


# ------------------------------------------------------------ preprocessing
def _text_page():
    img = Image.new("L", (1700, 1100), 255)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 38)
    except OSError:
        font = ImageFont.load_default()
    for i in range(14):
        draw.text(
            (120, 80 + i * 65),
            "Install 4 doors and 120 linear feet of walls",
            fill=0,
            font=font,
        )
    return img


@pytest.mark.parametrize("skew", [2.0, -3.5, 4.0])
def test_deskew_levels_a_rotated_page(skew):
    rotated = _text_page().rotate(
        skew, expand=True, fillcolor=255, resample=Image.BICUBIC
    )

    assert estimate_skew(rotated) == pytest.approx(skew, abs=0.3)
    assert abs(estimate_skew(deskew(rotated))) < 0.3


def test_prepare_upscales_small_images_and_binarizes():
    small = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(small).rectangle([50, 50, 350, 250], outline="black", width=3)

    out = prepare(small)

    assert out.width >= 1600
    assert set(np.unique(np.asarray(out))) <= {0, 255}


# -------------------------------------------------- upload size limit (413)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path, name",
    [("/api/v1/takeoffs/text", "a.txt"), ("/api/v1/takeoffs/drawing-pdf", "a.pdf")],
)
async def test_oversized_upload_is_rejected_with_413(client, monkeypatch, path, name):
    monkeypatch.setattr("src.config.MAX_UPLOAD_MB", 0)

    response = await client.post(
        path, files={"file": (name, b"x", "application/octet-stream")}
    )

    assert response.status_code == 413
    assert "limit" in response.json()["detail"]


# ------------------------------------------------ real Tesseract (opt-in)
@pytest.mark.ocr_engine
@pytest.mark.asyncio
async def test_real_tesseract_reads_a_scanned_scope(client, tmp_path):
    """Needs the Tesseract binary: run with `pytest -m ocr_engine`."""
    command = ocr.find_tesseract()
    assert command, "Tesseract is not installed"
    ocr.set_engine(ocr.TesseractEngine(command))  # the real Tesseract, explicitly
    page = Image.new("RGB", (1700, 2200), "white")
    font = ImageFont.truetype("arial.ttf", 56)
    draw = ImageDraw.Draw(page)
    draw.text(
        (150, 200),
        "Install 4 doors and 120 linear feet of walls.",
        fill="black",
        font=font,
    )
    draw.text(
        (150, 320),
        "Provide 1,500 sq ft of vinyl tile flooring.",
        fill="black",
        font=font,
    )
    path = tmp_path / "scope_scan.pdf"
    page.save(path, "PDF", resolution=200)

    body = (await upload_pdf(client, path)).json()

    assert quantities(body) == {"doors": 4.0, "walls": 120.0, "flooring": 1500.0}
