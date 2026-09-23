"""
Orchestrates a full run: load a PDF, work out what's on each page, write
everything to Excel, and produce two output PDFs - one of pages that were
successfully processed, one of pages that need a human to look at them.
"""

from dataclasses import dataclass

from classify import classify
from excel_writer import resolve_tab_name, write_all_to_excel
from ocr import FIELDS, extract_field
from pdf_io import add_stamp, load_pdf_images, make_and_save_pdf

PDF_PATH = "PDFs/Utilis 10-27-2025.pdf"  # TODO: pull from config instead of hardcoding
STAMP_PATH = "Pictures/Entered_no_bg.png"
STAMP_POSITION = (650, 1280)


@dataclass
class PageResult:
    company: str
    address: str
    bill_date: str | None = None
    amount: float | None = None
    effective_dates: str | None = None
    due_date: str | None = None


def process_page(image) -> PageResult | None:
    """Returns a filled PageResult on success, or None if this page should
    be skipped: unrecognized company, address didn't OCR at all, or the
    address doesn't match any tracked tab."""
    company = classify(image)
    if company is None:
        return None

    raw_address = extract_field(image, "service_address", company)
    if raw_address is None:
        return None  # OCR couldn't read an address after 5 tries - needs a human look

    tab_name = resolve_tab_name(raw_address)
    if tab_name is None:
        return None  # read it fine, but it's not a tenant we track - skip the rest

    result = PageResult(company=company, address=tab_name)
    for field_name in FIELDS:
        if field_name == "service_address":
            continue
        setattr(result, field_name, extract_field(image, field_name, company))
    return result


def main():
    images = load_pdf_images(PDF_PATH)

    all_results = []
    processed_images = []
    unprocessed_images = []

    for image in images:
        result = process_page(image)
        if result is None:
            unprocessed_images.append(image)
            continue
        all_results.append(result)
        processed_images.append(add_stamp(image, STAMP_PATH, STAMP_POSITION))

    write_all_to_excel(all_results)
    make_and_save_pdf(processed_images, "processed")
    make_and_save_pdf(unprocessed_images, "unprocessed")

    print(f"Processed: {len(processed_images)}  Needs review: {len(unprocessed_images)}")


if __name__ == "__main__":
    main()
