import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.config import (
    SAMPLE_BLUEPRINT_PDF_PATH,
    SAMPLE_SITE_PHOTO_PATH,
    SCOPE_TXT_PATH,
)
from src.main import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def no_ocr_engine_by_default():
    """Tests are deterministic whether or not Tesseract is installed.

    OCR tests inject an engine explicitly with `set_engine(...)`.
    """
    from src.ocr import engine
    from src.vision import claude

    engine.set_engine(None)
    claude.set_client(None)  # tests must never reach the network by accident
    yield
    engine.set_engine(None, enabled=False)
    claude.set_client(None, enabled=False)


@pytest.fixture(scope="session")
def scope_txt_path():
    assert SCOPE_TXT_PATH.exists(), "run bootstrap_assets.py before the test suite"
    return SCOPE_TXT_PATH


@pytest.fixture(scope="session")
def sample_photo_path():
    assert (
        SAMPLE_SITE_PHOTO_PATH.exists()
    ), "run bootstrap_assets.py before the test suite"
    return SAMPLE_SITE_PHOTO_PATH


@pytest.fixture(scope="session")
def sample_pdf_path():
    assert (
        SAMPLE_BLUEPRINT_PDF_PATH.exists()
    ), "run bootstrap_assets.py before the test suite"
    return SAMPLE_BLUEPRINT_PDF_PATH


@pytest.fixture
def make_pdf(tmp_path):
    """Write a real PDF with known ground-truth content and return its path.

    `draw(canvas, page_index)` paints one page with the reportlab canvas API
    (requirements-dev.txt). The file is uploaded via a normal file handle.
    """
    from reportlab.pdfgen import canvas

    def _make(draw, name="doc.pdf", pages=1, **canvas_kwargs):
        path = tmp_path / name
        pdf = canvas.Canvas(str(path), **canvas_kwargs)
        for index in range(pages):
            draw(pdf, index)
            pdf.showPage()
        pdf.save()
        return path

    return _make
