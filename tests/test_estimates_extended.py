"""Estimate engine: adjustments, unit safety, review flags, one-shot endpoint.

Hand-computed from src/database/ddc_cwicr.json for walls 120 / doors 4 /
flooring 1500:
  walls    material 540.00   labor 1,950.00 -> 2,490.00
  doors    material 1,800.00 labor   390.00 -> 2,190.00
  flooring material 4,650.00 labor 6,600.00 -> 11,250.00      base = 15,930.00
"""

import ezdxf
import pytest
from ezdxf import units

ITEMS = [
    {"category": "walls", "quantity": 120},
    {"category": "doors", "quantity": 4},
    {"category": "flooring", "quantity": 1500},
]


async def estimate(client, **extra):
    return await client.post("/api/v1/estimates", json={"items": ITEMS, **extra})


@pytest.mark.asyncio
async def test_default_pricing_is_unchanged(client):
    body = (await estimate(client)).json()

    assert body["subtotal"] == 15930.0
    assert body["margin_amount"] == 2389.5
    assert body["total"] == 18319.5
    assert body["needs_review"] == []


@pytest.mark.asyncio
async def test_waste_applies_to_material_only(client):
    body = (await estimate(client, waste_percent=10)).json()

    # +10% of material 540 + 1800 + 4650 = 6990 -> +699; labor unchanged
    assert body["subtotal"] == pytest.approx(16629.0)
    assert body["total"] == pytest.approx(16629.0 * 1.15, abs=0.01)


@pytest.mark.asyncio
async def test_region_index_scales_material_and_labor(client):
    body = (await estimate(client, region_index=1.2)).json()

    assert body["subtotal"] == pytest.approx(19116.0)
    assert body["total"] == pytest.approx(19116.0 * 1.15, abs=0.01)
    walls = next(li for li in body["line_items"] if li["category"] == "walls")
    assert walls["material_cost"] == pytest.approx(648.0)
    assert walls["labor_cost"] == pytest.approx(2340.0)


@pytest.mark.asyncio
async def test_itemized_adjustments_and_tax_order(client):
    body = (
        await estimate(
            client,
            overhead_rate=0.10,
            profit_rate=0.05,
            contingency_rate=0.03,
            tax_rate=0.08,
        )
    ).json()

    adj = body["adjustments"]
    assert adj["overhead_amount"] == pytest.approx(1593.0)
    assert adj["profit_amount"] == pytest.approx(796.5)
    assert adj["contingency_amount"] == pytest.approx(477.9)
    # tax is charged on base + margin + overhead + profit + contingency = 21,186.90
    assert adj["tax_amount"] == pytest.approx(1694.95, abs=0.01)  # 21,186.90 x 8%
    assert body["total"] == pytest.approx(22881.85, abs=0.01)  # 21,186.90 x 1.08


@pytest.mark.asyncio
async def test_unit_mismatch_is_rejected(client):
    response = await client.post(
        "/api/v1/estimates",
        json={"items": [{"category": "walls", "quantity": 120, "unit": "square_feet"}]},
    )

    assert response.status_code == 422
    assert "Unit mismatch" in response.json()["detail"]


@pytest.mark.asyncio
async def test_matching_unit_is_accepted_and_negative_quantity_rejected(client):
    ok = await client.post(
        "/api/v1/estimates",
        json={"items": [{"category": "walls", "quantity": 10, "unit": "linear_feet"}]},
    )
    negative = await client.post(
        "/api/v1/estimates", json={"items": [{"category": "walls", "quantity": -5}]}
    )

    assert ok.status_code == 200
    assert negative.status_code == 422


@pytest.mark.asyncio
async def test_low_confidence_lines_are_flagged(client):
    body = (
        await client.post(
            "/api/v1/estimates",
            json={
                "items": [
                    {"category": "walls", "quantity": 120, "confidence": "high"},
                    {"category": "flooring", "quantity": 300, "confidence": "low"},
                ]
            },
        )
    ).json()

    assert body["needs_review"] == ["flooring: quantity 300 has low confidence"]
    flooring = next(li for li in body["line_items"] if li["category"] == "flooring")
    assert flooring["confidence"] == "low"


# ------------------------------------------------------------ one-shot endpoint
FROM_DOC = "/api/v1/estimates/from-document"


@pytest.mark.asyncio
async def test_from_document_text_matches_the_two_step_flow(client, scope_txt_path):
    with open(scope_txt_path, "rb") as fh:
        body = (
            await client.post(FROM_DOC, files={"file": ("scope.txt", fh, "text/plain")})
        ).json()

    assert body["estimate"]["total"] == 18319.5
    assert body["needs_review"] == []


@pytest.mark.asyncio
async def test_from_document_applies_form_adjustments(client, scope_txt_path):
    with open(scope_txt_path, "rb") as fh:
        body = (
            await client.post(
                FROM_DOC,
                files={"file": ("scope.txt", fh, "text/plain")},
                data={"region_index": "1.2"},
            )
        ).json()

    assert body["estimate"]["total"] == pytest.approx(21983.4, abs=0.01)


@pytest.mark.asyncio
async def test_from_document_dxf_prices_cad_quantities(client, tmp_path):
    doc = ezdxf.new("R2018")
    doc.units = units.FT
    doc.layers.add("A-WALL")
    doc.modelspace().add_line((0, 0), (120, 0), dxfattribs={"layer": "A-WALL"})
    path = tmp_path / "plan.dxf"
    doc.saveas(path)

    with open(path, "rb") as fh:
        body = (
            await client.post(
                FROM_DOC, files={"file": ("plan.dxf", fh, "application/dxf")}
            )
        ).json()

    assert body["takeoff"]["items"][0]["basis"] == "cad"
    assert body["estimate"]["line_items"][0]["line_subtotal"] == pytest.approx(2490.0)
    assert body["estimate"]["total"] == pytest.approx(2490.0 * 1.15)


@pytest.mark.asyncio
async def test_from_document_pdf_geometry_is_flagged_for_review(client, make_pdf):
    def draw(c, _):
        c.drawString(50, 40, 'SCALE: 1/4" = 1\'-0"')
        c.rect(100, 100, 360, 270)
        c.drawString(200, 200, "OFFICE 101")

    pdf = make_pdf(draw, pagesize=(1000, 800))
    with open(pdf, "rb") as fh:
        body = (
            await client.post(
                FROM_DOC, files={"file": ("p.pdf", fh, "application/pdf")}
            )
        ).json()

    line = body["estimate"]["line_items"][0]
    assert line["category"] == "flooring" and line["confidence"] == "low"
    assert any("flooring" in r and "low" in r for r in body["needs_review"])


@pytest.mark.asyncio
async def test_from_document_without_quantities_returns_no_estimate(client, make_pdf):
    pdf = make_pdf(lambda c, _: c.drawString(72, 700, "Nothing to price here."))
    with open(pdf, "rb") as fh:
        response = await client.post(
            FROM_DOC, files={"file": ("p.pdf", fh, "application/pdf")}
        )

    body = response.json()
    assert response.status_code == 200
    assert body["estimate"] is None
    assert any("no quantities" in r for r in body["needs_review"])


@pytest.mark.asyncio
async def test_from_document_rejects_unknown_binary(client):
    response = await client.post(
        FROM_DOC,
        files={
            "file": ("blob.bin", b"\x00\x01\x02\x03" * 100, "application/octet-stream")
        },
    )

    assert response.status_code == 422
    assert "Unsupported" in response.json()["detail"]
