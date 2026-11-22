"""Render PDF pages to images (pypdfium2 via pdfplumber, already a dependency)."""

from PIL import Image


def render_page(page, dpi: int = 300) -> Image.Image:
    return page.to_image(resolution=dpi).original.convert("RGB")
