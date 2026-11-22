"""Claude vision tests.

A fake client replaces anthropic.Anthropic at the SDK boundary, so the exact
request the app would send is asserted; image preparation, merging with OCR and
text results, failure handling and review flags around it are all real.
"""

import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from src.ocr import engine as ocr
from src.vision import claude
from tests.test_ocr import FakeEngine
from tests.test_pdf_robustness import quantities, upload_pdf

PHOTO = "/api/v1/takeoffs/photo"


class FakeClient:
    def __init__(self, payload=None, stop_reason="end_turn", error=None, raw_text=None):
        self.calls = []
        self.error = error
        text = raw_text if raw_text is not None else json.dumps(payload or EMPTY)
        self.response = SimpleNamespace(
            stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


EMPTY = {"elements": [], "text_found": [], "dimensions": [], "notes": ""}


def payload(elements=(), text=(), dims=(), notes="seen"):
    return {
        "elements": list(elements),
        "text_found": list(text),
        "dimensions": list(dims),
        "notes": notes,
    }


async def post_photo(client, path, **data):
    with open(path, "rb") as fh:
        return (
            await client.post(
                PHOTO, files={"file": ("p.jpg", fh, "image/jpeg")}, data=data
            )
        ).json()


@pytest.mark.asyncio
async def test_request_contract(client, sample_photo_path):
    fake = FakeClient()
    claude.set_client(fake)

    await post_photo(client, sample_photo_path, use_vision="true")

    (call,) = fake.calls
    assert call["model"] == "claude-sonnet-5-5"
    # Sonnet 5.5 rejects forced tool_choice, thinking-off and sampling params
    for forbidden in (
        "tool_choice",
        "thinking",
        "temperature",
        "top_p",
        "top_k",
        "tools",
    ):
        assert forbidden not in call
    assert call["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in call["betas"]
    fmt = call["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["properties"]["elements"]["items"]["properties"]["category"][
        "enum"
    ] == [
        "doors",
        "flooring",
        "walls",
    ]
    assert fmt["schema"]["additionalProperties"] is False
    assert call["system"]

    image_block = call["messages"][0]["content"][0]
    assert image_block["type"] == "image"
    assert image_block["source"]["media_type"] == "image/jpeg"
    sent = Image.open(io.BytesIO(base64.b64decode(image_block["source"]["data"])))
    assert sent.format == "JPEG"
    assert max(sent.size) <= claude.MAX_LONG_SIDE_PX  # 1280x853 source fits as-is


def test_large_images_are_downscaled():
    data = claude.prepare_image(Image.new("RGB", (6000, 4000), "gray"))

    sent = Image.open(io.BytesIO(data))
    assert max(sent.size) == claude.MAX_LONG_SIDE_PX
    assert sent.size == (1568, 1045)  # aspect ratio kept


def test_model_can_be_overridden(monkeypatch):
    fake = FakeClient()
    claude.set_client(fake)
    monkeypatch.setenv("VISION_MODEL", "claude-opus-5-5")

    claude.analyze_image(Image.new("RGB", (10, 10)))

    assert fake.calls[0]["model"] == "claude-opus-5-5"


@pytest.mark.asyncio
async def test_vision_is_not_called_unless_requested(client, sample_photo_path):
    fake = FakeClient()
    claude.set_client(fake)

    body = await post_photo(client, sample_photo_path)

    assert fake.calls == []
    assert body["metadata"]["vision"] == {"used": False}


@pytest.mark.asyncio
async def test_vision_results_are_low_confidence_and_flagged(client, sample_photo_path):
    claude.set_client(
        FakeClient(
            payload(
                elements=[
                    {
                        "category": "doors",
                        "quantity": 4,
                        "basis": "4 door leaves visible",
                    },
                    {
                        "category": "windows",
                        "quantity": 9,
                        "basis": "not a known category",
                    },
                    {"category": "walls", "quantity": 0, "basis": "zero is ignored"},
                ],
                text=["Erect 120 linear feet of walls."],
                dims=[{"text": "12'-6\"", "feet": 12.5}],
            )
        )
    )

    body = await post_photo(client, sample_photo_path, use_vision="true")

    assert quantities(body) == {"doors": 4.0, "walls": 120.0}
    for item in body["items"]:
        assert (item["basis"], item["confidence"]) == ("vision", "low")
        assert item["evidence"].startswith("Claude vision")
    meta = body["metadata"]
    assert meta["vision"]["used"] and meta["vision"]["dimensions"][0]["feet"] == 12.5
    assert meta["needs_review"] is True
    assert any("vision-derived" in r for r in meta["review_reasons"])


@pytest.mark.asyncio
async def test_ocr_text_outranks_vision(client, sample_photo_path):
    ocr.set_engine(FakeEngine(text="Provide 4 doors"))
    claude.set_client(
        FakeClient(
            payload(elements=[{"category": "doors", "quantity": 9, "basis": "x"}])
        )
    )

    body = await post_photo(client, sample_photo_path, use_vision="true")

    (door,) = body["items"]
    assert (door["quantity"], door["basis"]) == (4.0, "text")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fake, expected",
    [
        (FakeClient(error=RuntimeError("network down")), "Vision analysis failed"),
        (FakeClient(stop_reason="refusal"), "declined"),
        (FakeClient(raw_text="not json {"), "unreadable"),
        (None, "Vision analysis failed"),  # no client configured at all
    ],
)
async def test_vision_failures_never_break_the_request(
    client, sample_photo_path, fake, expected
):
    claude.set_client(fake)

    response = await client.post(
        PHOTO,
        files={"file": ("p.jpg", open(sample_photo_path, "rb"), "image/jpeg")},
        data={"use_vision": "true"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert any(expected in w for w in body["metadata"]["warnings"])


# ----------------------------------------------------------------- PDF pages
@pytest.fixture
def scanned_pdf(tmp_path):
    pages = [Image.new("RGB", (800, 1000), "white") for _ in range(3)]
    path = tmp_path / "scan.pdf"
    pages[0].save(path, "PDF", save_all=True, append_images=pages[1:])
    return path


@pytest.mark.asyncio
async def test_vision_reads_scanned_pdf_pages_only_when_requested(client, scanned_pdf):
    fake = FakeClient(
        payload(elements=[{"category": "doors", "quantity": 4, "basis": "x"}])
    )
    claude.set_client(fake)

    off = (await upload_pdf(client, scanned_pdf)).json()
    assert fake.calls == [] and off["items"] == []

    with open(scanned_pdf, "rb") as fh:
        on = (
            await client.post(
                "/api/v1/takeoffs/drawing-pdf",
                files={"file": ("s.pdf", fh, "application/pdf")},
                data={"use_vision": "true"},
            )
        ).json()

    assert len(fake.calls) == 3  # three scanned pages, all within the page limit
    assert quantities(on) == {"doors": 4.0}
    assert on["items"][0]["basis"] == "vision"
    assert on["metadata"]["vision"]["pages"] == 3
    assert "pages not read" not in on["metadata"]["review_reasons"]


@pytest.mark.asyncio
async def test_vision_page_limit_is_reported(client, scanned_pdf, monkeypatch):
    monkeypatch.setattr("src.config.VISION_MAX_PAGES", 1)
    fake = FakeClient()
    claude.set_client(fake)

    with open(scanned_pdf, "rb") as fh:
        body = (
            await client.post(
                "/api/v1/takeoffs/drawing-pdf",
                files={"file": ("s.pdf", fh, "application/pdf")},
                data={"use_vision": "true"},
            )
        ).json()

    assert len(fake.calls) == 1
    assert any(
        "No text layer found on 2 page(s)" in w for w in body["metadata"]["warnings"]
    )


@pytest.mark.asyncio
async def test_pages_with_text_are_never_sent_to_vision(client, make_pdf):
    fake = FakeClient()
    claude.set_client(fake)
    pdf = make_pdf(lambda c, _: c.drawString(72, 700, "Install 4 doors."))

    with open(pdf, "rb") as fh:
        await client.post(
            "/api/v1/takeoffs/drawing-pdf",
            files={"file": ("t.pdf", fh, "application/pdf")},
            data={"use_vision": "true"},
        )

    assert fake.calls == []
