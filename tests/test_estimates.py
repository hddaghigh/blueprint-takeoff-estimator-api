"""Integration test for the cost estimation sync endpoint, chained end-to-end
from a real text takeoff through to a priced proposal."""

import pytest


@pytest.mark.asyncio
async def test_estimate_end_to_end_from_real_text_takeoff(client, scope_txt_path):
    with open(scope_txt_path, "rb") as fh:
        takeoff_response = await client.post(
            "/api/v1/takeoffs/text",
            files={"file": ("scope.txt", fh, "text/plain")},
        )
    assert takeoff_response.status_code == 200
    takeoff_items = takeoff_response.json()["items"]

    estimate_request = {
        "items": [
            {"category": item["category"], "quantity": item["quantity"]}
            for item in takeoff_items
        ]
    }
    estimate_response = await client.post("/api/v1/estimates", json=estimate_request)

    assert estimate_response.status_code == 200
    body = estimate_response.json()

    walls = next(li for li in body["line_items"] if li["category"] == "walls")
    assert walls["material_cost"] == pytest.approx(120 * 4.50)
    assert walls["labor_cost"] == pytest.approx(120 * 0.25 * 65.00)

    expected_subtotal = sum(li["line_subtotal"] for li in body["line_items"])
    assert body["subtotal"] == pytest.approx(expected_subtotal)
    assert body["margin_rate"] == 0.15
    assert body["margin_amount"] == pytest.approx(body["subtotal"] * 0.15)
    assert body["total"] == pytest.approx(body["subtotal"] + body["margin_amount"])
