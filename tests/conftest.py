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
