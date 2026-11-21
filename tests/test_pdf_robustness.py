"""PDF pipeline tests against PDFs with known ground truth.

Each PDF is a real file (generated with reportlab) uploaded through a normal
file handle, so vector geometry, embedded images and text layers are parsed by
the real pdfplumber code path.
"""

import pytest
from PIL import Image, ImageDraw

PDF_URL = "/api/v1/takeoffs/drawing-pdf"
FOOT = 864  # PDF points per foot (72 pt/inch * 12)


async def upload_pdf(client, path):
    with open(path, "rb") as fh:
        return await client.post(
            PDF_URL, files={"file": (path.name, fh, "application/pdf")}
        )


def quantities(body):
    return {item["category"]: item["quantity"] for item in body["items"]}


@pytest.mark.asyncio
async def test_drawing_with_vectors_image_and_callouts(client, make_pdf, tmp_path):
    """One PDF holding drawing + text + image: all three are read correctly."""
    logo = tmp_path / "logo.png"
    Image.new("RGB", (200, 120), (200, 30, 30)).save(logo)

    def draw(c, _):
        c.line(100, 100, 100 + FOOT, 100)  # two 1 ft lines
        c.line(100, 200, 100 + FOOT, 200)
        path = c.beginPath()  # polyline (CAD walls): two 1 ft segments
        path.moveTo(100, 400)
        path.lineTo(100 + FOOT, 400)
        path.lineTo(100 + FOOT, 400 + FOOT)
        c.drawPath(path, stroke=1, fill=0)
        c.rect(300, 600, 36, 80)  # two door symbols
        c.rect(500, 600, 36, 80)
        c.drawImage(str(logo), 700, 700, 100, 60)  # embedded raster image
        c.drawString(120, 120, "Install 4 doors and 120 linear feet of walls.")
        c.drawString(120, 140, "Provide 1,500 sq ft of vinyl tile flooring.")

    pdf = make_pdf(draw, pagesize=(1400, 1400))
    response = await upload_pdf(client, pdf)

    assert response.status_code == 200
    body = response.json()
    assert quantities(body) == {"doors": 4.0, "walls": 120.0, "flooring": 1500.0}

    meta = body["metadata"]
    assert meta["image_count"] == 1
    assert meta["vector_line_count"] == 2
    assert meta["vector_curve_count"] == 1  # the polyline
    assert meta["vector_rect_count"] == 2
    # 2 lines + 2 polyline segments, each exactly 1 ft: checks 864 pt/ft and polylines
    assert meta["vector_path_length_ft_paper_space"] == pytest.approx(4.0, abs=0.01)
    assert meta["warnings"] == []


@pytest.mark.asyncio
async def test_drawing_without_callouts_invents_no_quantities(client, make_pdf):
    """Shapes and page size alone must never become doors/walls/flooring."""

    def draw(c, _):
        c.rect(100, 100, 36, 80)
        c.line(100, 300, 100 + FOOT, 300)

    response = await upload_pdf(client, make_pdf(draw, pagesize=(1200, 1200)))

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["metadata"]["vector_rect_count"] == 1


@pytest.mark.asyncio
async def test_scanned_image_only_pdf_is_flagged(client, tmp_path):
    scan = Image.new("RGB", (1700, 2200), "white")
    ImageDraw.Draw(scan).text((300, 300), "Install 4 doors", fill="black")
    path = tmp_path / "scan.pdf"
    scan.save(path, "PDF", resolution=200)

    response = await upload_pdf(client, path)

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["metadata"]["image_count"] == 1
    assert any("scanned" in w for w in body["metadata"]["warnings"])


@pytest.mark.asyncio
async def test_callout_on_late_page_is_found(client, make_pdf):
    def draw(c, page):
        c.drawString(72, 700, "Install 4 doors." if page == 24 else f"Notes {page}")

    response = await upload_pdf(client, make_pdf(draw, pages=30))

    body = response.json()
    assert quantities(body) == {"doors": 4.0}
    assert body["metadata"]["pages_scanned"] == 30
    assert body["metadata"]["warnings"] == []


@pytest.mark.asyncio
async def test_page_cap_is_reported_not_silent(client, make_pdf, monkeypatch):
    monkeypatch.setattr("src.routes.takeoffs.MAX_PDF_PAGES", 5)

    def draw(c, page):
        c.drawString(72, 700, "Install 4 doors." if page == 6 else f"Notes {page}")

    response = await upload_pdf(client, make_pdf(draw, pages=8))

    body = response.json()
    assert body["metadata"]["pages_total"] == 8
    assert body["metadata"]["pages_scanned"] == 5
    assert body["items"] == []  # the callout is on page 7, beyond the cap
    assert any("first 5 of 8 pages" in w for w in body["metadata"]["warnings"])


@pytest.mark.asyncio
async def test_rotated_page_text_is_read(client, make_pdf):
    def draw(c, _):
        c.setPageRotation(90)
        c.drawString(72, 700, "Install 4 doors.")

    response = await upload_pdf(client, make_pdf(draw))

    assert quantities(response.json()) == {"doors": 4.0}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["empty", "garbage", "truncated", "encrypted"])
async def test_unreadable_pdfs_return_422_not_500(client, make_pdf, tmp_path, kind):
    def draw(c, _):
        c.drawString(72, 700, "Install 4 doors.")

    if kind == "encrypted":
        path = make_pdf(draw, encrypt="secret")
    else:
        valid = make_pdf(draw).read_bytes()
        path = tmp_path / "bad.pdf"
        path.write_bytes(
            {
                "empty": b"",
                "garbage": b"%PDF-1.4\nnot really a pdf",
                "truncated": valid[: len(valid) // 2],
            }[kind]
        )

    response = await upload_pdf(client, path)

    assert response.status_code == 422
    if kind == "encrypted":
        assert "password" in response.json()["detail"]


@pytest.mark.asyncio
async def test_real_downloaded_pdf_reports_honest_metadata(client, sample_pdf_path):
    """The real 548-page guidebook has no takeoff callouts: expect no invented items."""
    response = await upload_pdf(client, sample_pdf_path)

    assert response.status_code == 200
    body = response.json()
    meta = body["metadata"]
    assert meta["pages_total"] > meta["pages_scanned"] > 0
    assert meta["image_count"] > 0
    assert any("pages were scanned" in w for w in meta["warnings"])
    for item in body["items"]:  # if any callout matches, it must be evidenced
        assert item["category"] in meta["evidence"]


@pytest.mark.asyncio
async def test_text_endpoint_rejects_binary_and_accepts_legacy_encoding(
    client, sample_photo_path, tmp_path
):
    with open(sample_photo_path, "rb") as fh:
        binary = await client.post(
            "/api/v1/takeoffs/text", files={"file": ("a.jpg", fh, "image/jpeg")}
        )
    assert binary.status_code == 422

    legacy = tmp_path / "legacy.txt"
    legacy.write_bytes("Install 4 doors \xe9t\xe9.".encode("cp1252"))
    with open(legacy, "rb") as fh:
        ok = await client.post(
            "/api/v1/takeoffs/text", files={"file": ("legacy.txt", fh, "text/plain")}
        )
    assert ok.status_code == 200
    assert quantities(ok.json()) == {"doors": 4.0}
