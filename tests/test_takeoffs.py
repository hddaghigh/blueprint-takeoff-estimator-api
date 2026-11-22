"""Integration tests for the multi-modal takeoff pipelines.

Each test uploads the real, physically downloaded assets in assets/
(produced by bootstrap_assets.py) directly into the API endpoints via
multipart/form-data, exercising the actual parsing code paths.
"""

import pytest


@pytest.mark.asyncio
async def test_text_takeoff_extracts_scope_quantities(client, scope_txt_path):
    with open(scope_txt_path, "rb") as fh:
        response = await client.post(
            "/api/v1/takeoffs/text",
            files={"file": ("scope.txt", fh, "text/plain")},
        )

    assert response.status_code == 200
    body = response.json()
    items = {item["category"]: item for item in body["items"]}

    assert items["walls"]["quantity"] == 120.0
    assert items["walls"]["cwicr_code"] == "04-2200-0010"
    assert items["doors"]["quantity"] == 4.0
    assert items["flooring"]["quantity"] == 1500.0


@pytest.mark.asyncio
async def test_text_takeoff_defaults_to_bundled_scope_asset(client):
    response = await client.post("/api/v1/takeoffs/text")

    assert response.status_code == 200
    body = response.json()
    assert body["source"].endswith("scope.txt")
    assert len(body["items"]) == 3


@pytest.mark.asyncio
async def test_photo_takeoff_parses_real_jpeg(client, sample_photo_path):
    with open(sample_photo_path, "rb") as fh:
        response = await client.post(
            "/api/v1/takeoffs/photo",
            files={"file": ("sample_site_photo.jpg", fh, "image/jpeg")},
        )

    assert response.status_code == 200
    body = response.json()

    assert body["metadata"]["image_width"] == 1280
    assert body["metadata"]["image_height"] == 853
    assert body["metadata"]["average_luminance"] > 0
    # No quantity may be invented from pixel counts (the old pixels/500 "sq ft").
    assert body["items"] == []
    assert body["metadata"]["pixel_count"] == 1280 * 853


@pytest.mark.asyncio
async def test_pdf_takeoff_scans_real_pdf_vectors_and_text(client, sample_pdf_path):
    with open(sample_pdf_path, "rb") as fh:
        response = await client.post(
            "/api/v1/takeoffs/drawing-pdf",
            files={"file": ("sample_blueprint.pdf", fh, "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()

    # The real asset is a guidebook, not a takeoff set: assert what was actually
    # read (pages, images, warnings), not fabricated quantities. Detailed
    # ground-truth PDF checks live in tests/test_pdf_robustness.py.
    assert body["metadata"]["pages_scanned"] > 0
    assert body["metadata"]["image_count"] > 0
    for item in body["items"]:
        assert item["quantity"] > 0
        assert item["cwicr_code"]


@pytest.mark.asyncio
async def test_photo_takeoff_rejects_non_image_upload(client, scope_txt_path):
    with open(scope_txt_path, "rb") as fh:
        response = await client.post(
            "/api/v1/takeoffs/photo",
            files={"file": ("scope.txt", fh, "text/plain")},
        )

    assert response.status_code == 422
