"""
Handles everything PDF-shaped: rendering pages as images, stamping entered
pages, and saving the output PDF. No OCR, no spreadsheet, no
classification logic lives here.

Pages are rendered one at a time instead of all up front - a 300 DPI page
is about 25 MB in memory, so loading a 100-page scan at once could need
several GB on an office PC.
"""

import io
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

DPI = 300


def page_count(pdf_path) -> int:
    with fitz.open(Path(pdf_path)) as doc:
        return doc.page_count


def iter_pages(pdf_path, dpi: int = DPI):
    """Yield (page_number, image) for every page, 1-based. A page that can't
    be rendered yields (page_number, None) instead of stopping the run."""
    with fitz.open(Path(pdf_path)) as doc:
        for index in range(doc.page_count):
            try:
                pix = doc[index].get_pixmap(dpi=dpi)
                image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            except Exception:  # a damaged page shouldn't take the whole batch down
                image = None
            yield index + 1, image


def add_stamp(image: Image.Image, stamp: Image.Image, position: tuple[int, int]) -> Image.Image:
    """Paste a transparent stamp onto a page at (x, y) in 300 DPI pixels."""
    base = image.convert("RGBA")
    base.paste(stamp, position, mask=stamp)
    return base.convert("RGB")


def save_stamped_copy(pdf_path, out_path, stamp_pages, stamp_path, position):
    """Write a copy of the whole PDF, in its original order, with the stamp
    on each page number in stamp_pages.

    Unstamped pages are copied straight from the original scan, so they're
    byte-for-byte the same. Stamped pages are rendered, stamped, and added
    as a JPEG at 300 DPI - one page at a time, so memory stays flat no
    matter how long the scan is."""
    stamp = Image.open(stamp_path).convert("RGBA")
    out_path = Path(out_path)

    with fitz.open(Path(pdf_path)) as src, fitz.open() as out:
        for index in range(src.page_count):
            if index + 1 not in stamp_pages:
                out.insert_pdf(src, from_page=index, to_page=index)
                continue
            pix = src[index].get_pixmap(dpi=DPI)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            stamped = add_stamp(image, stamp, position)
            buffer = io.BytesIO()
            stamped.save(buffer, "JPEG", quality=85)
            # Page size in points (1/72 inch) from the 300 DPI pixel size.
            page = out.new_page(width=stamped.width * 72 / DPI, height=stamped.height * 72 / DPI)
            page.insert_image(page.rect, stream=buffer.getvalue())
        if out.page_count == 0:
            return None
        out.save(out_path, garbage=3, deflate=True)
    return out_path
