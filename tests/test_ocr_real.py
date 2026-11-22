"""End-to-end tests with the REAL OCR engine (RapidOCR, a pip dependency).

Scanned pages are drawn as pixels (no text layer anywhere), saved as PDFs or
photos, uploaded through the API and read by the real engine: rasterizing,
preprocessing, OCR, spacing repair, extraction, confidence and warnings.
"""

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from src.ocr import engine as ocr
from tests.test_pdf_robustness import quantities, upload_pdf

SCOPE = [
    "Install 4 doors and 120 linear feet of walls.",
    "Provide 1,500 sq ft of vinyl tile flooring.",
]


def font(size):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)  # portable vector font


def scanned_page(lines, size=(1700, 2200), text_size=46, top=200, step=170, x=150):
    page = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(page)
    for i, line in enumerate(lines):
        draw.text((x, top + i * step), line, fill="black", font=font(text_size))
    return page


@pytest.fixture(autouse=True)
def real_engine():
    ocr.set_engine(None, enabled=False)  # undo the suite-wide "no engine" default
    engine = ocr.get_engine()
    assert (
        engine is not None and engine.name == "rapidocr"
    ), "rapidocr must be installed"
    yield engine


@pytest.mark.asyncio
async def test_scanned_scope_page_is_read_exactly(client, tmp_path):
    path = tmp_path / "scan.pdf"
    scanned_page(SCOPE).save(path, "PDF", resolution=200)

    body = (await upload_pdf(client, path)).json()

    assert quantities(body) == {"doors": 4.0, "walls": 120.0, "flooring": 1500.0}
    for item in body["items"]:
        assert item["basis"] == "text" and item["evidence"].startswith("OCR:")
        assert item["confidence"] == "medium"
    meta = body["metadata"]
    assert meta["ocr"]["engine"] == "rapidocr" and meta["ocr"]["pages"] == [1]
    assert meta["ocr"]["mean_confidence"] > 85
    assert not any("OCR" in w for w in meta["warnings"])


@pytest.mark.asyncio
async def test_drawing_labels_do_not_corrupt_quantities(client, tmp_path):
    """The scale note's 1'-0" and 8-inch / 12'-6" dimensions must not become quantities."""
    lines = [
        'SCALE: 1/4" = 1\'-0"',
        "Erect 8-inch CMU walls.",
        "CORRIDOR 12'-6\" x 8'-0\"",
        SCOPE[0],
    ]
    path = tmp_path / "labels.pdf"
    scanned_page(lines).save(path, "PDF", resolution=200)

    body = (await upload_pdf(client, path)).json()

    assert quantities(body) == {"doors": 4.0, "walls": 120.0}
    meta = body["metadata"]
    assert meta["pages_with_scale"] == 1  # the scale note was found through OCR
    assert [s.replace(" ", "") for s in meta["scales"]] == ['1/4"=1\'-0"']


@pytest.mark.asyncio
@pytest.mark.parametrize("angle", [90, 270])
async def test_page_scanned_sideways_is_still_read(client, tmp_path, angle):
    page = scanned_page(SCOPE, size=(2200, 1700), text_size=44)
    path = tmp_path / "sideways.pdf"
    page.rotate(angle, expand=True).save(path, "PDF", resolution=200)

    body = (await upload_pdf(client, path)).json()

    assert quantities(body) == {"doors": 4.0, "walls": 120.0, "flooring": 1500.0}


@pytest.mark.asyncio
async def test_slightly_skewed_scan_is_deskewed_and_read(client, tmp_path):
    skewed = scanned_page(SCOPE).rotate(
        2.0, expand=True, fillcolor="white", resample=Image.BICUBIC
    )
    path = tmp_path / "skewed.pdf"
    skewed.save(path, "PDF", resolution=200)

    body = (await upload_pdf(client, path)).json()

    assert quantities(body) == {"doors": 4.0, "walls": 120.0, "flooring": 1500.0}


@pytest.mark.asyncio
async def test_mixed_pdf_reads_text_layer_and_scanned_page(client, make_pdf, tmp_path):
    scan = tmp_path / "page2.png"
    scanned_page([SCOPE[1]], size=(1200, 900)).save(scan)

    def draw(c, page):
        if page == 0:
            c.drawString(72, 700, "Install 4 doors.")  # real text layer
        else:
            c.drawImage(str(scan), 0, 0, 600, 450)  # pixels only

    body = (
        await upload_pdf(client, make_pdf(draw, pages=2, pagesize=(600, 800)))
    ).json()
    by_cat = {i["category"]: i for i in body["items"]}

    assert (
        by_cat["doors"]["confidence"] == "high" and by_cat["doors"]["basis"] == "text"
    )
    assert by_cat["flooring"]["quantity"] == 1500.0
    assert by_cat["flooring"]["evidence"].startswith("OCR:")
    assert body["metadata"]["ocr"]["pages"] == [2]  # page 1 was not OCR'd


@pytest.mark.asyncio
async def test_blank_scanned_page_gives_no_items_and_no_crash(client, tmp_path):
    path = tmp_path / "blank.pdf"
    Image.new("RGB", (1700, 2200), "white").save(path, "PDF", resolution=200)

    response = await upload_pdf(client, path)

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["metadata"]["ocr"]["mean_confidence"] is None


@pytest.mark.asyncio
async def test_photo_of_a_sign_is_read(client):
    sign = Image.new("RGB", (1400, 500), (30, 70, 140))
    ImageDraw.Draw(sign).text((80, 180), "Provide 4 doors", fill="white", font=font(90))
    buf = io.BytesIO()
    sign.save(buf, "JPEG", quality=85)

    body = (
        await client.post(
            "/api/v1/takeoffs/photo",
            files={"file": ("sign.jpg", buf.getvalue(), "image/jpeg")},
        )
    ).json()

    assert quantities(body) == {"doors": 4.0}
    assert body["items"][0]["confidence"] in ("medium", "low")
    assert body["metadata"]["ocr"]["engine"] == "rapidocr"


@pytest.mark.asyncio
async def test_two_requests_at_once_share_the_engine_safely(client, tmp_path):
    import asyncio

    path = tmp_path / "scan.pdf"
    scanned_page(SCOPE).save(path, "PDF", resolution=200)

    results = await asyncio.gather(*(upload_pdf(client, path) for _ in range(3)))

    assert [quantities(r.json()) for r in results] == [
        {"doors": 4.0, "walls": 120.0, "flooring": 1500.0}
    ] * 3
