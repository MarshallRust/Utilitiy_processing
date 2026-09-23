"""
Handles everything PDF-shaped: loading pages as images, stamping processed
pages, and saving the output PDF. No OCR, no Excel, no classification logic
lives here - just PDF in, images out, images in, PDF out.
"""

from datetime import date
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

DEFAULT_DPI = 300


def load_pdf_images(pdf_path, dpi: int = DEFAULT_DPI) -> list[Image.Image]:
    """Render every page of a PDF to a PIL Image. Works the same on
    Windows, macOS, and Linux - PyMuPDF ships its own PDF engine, so there's
    no external binary (poppler, etc.) to install or point at."""
    pdf_path = Path(pdf_path)
    images = []
    doc = fitz.open(pdf_path)
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append(img)
    doc.close()
    return images


def add_stamp(image: Image.Image, stamp_path, position: tuple[int, int]) -> Image.Image:
    """Paste a transparent stamp image onto a page at the given (x, y)."""
    stamp_path = Path(stamp_path)
    stamp = Image.open(stamp_path).convert("RGBA")
    base = image.convert("RGBA")
    base.paste(stamp, position, mask=stamp)
    return base


def make_and_save_pdf(images: list[Image.Image], name_prefix: str, output_dir="PDFs/processed_pdfs") -> Path | None:
    """Save a list of images as one multi-page PDF, named
    '<name_prefix>_<today's date>.pdf'. Returns None (and saves nothing) if
    given an empty list, rather than erroring - an empty "unprocessed" batch
    is the good outcome, not a bug."""
    if not images:
        print(f"No images for '{name_prefix}' - nothing to save")
        return None

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    today_str = date.today().strftime("%Y-%m-%d")
    pdf_path = output_dir / f"{name_prefix}_{today_str}.pdf"

    rgb_images = [img.convert("RGB") for img in images]
    first_image, *rest = rgb_images
    first_image.save(pdf_path, save_all=True, append_images=rest)

    print(f"PDF created: {pdf_path}")
    return pdf_path
