"""
Orchestrates a full run: load a PDF, work out what's on each page, write
everything to Excel, and produce one output PDF with every page in its
original order - pages entered into the spreadsheet are stamped "ENTERED",
everything else is left as-is.

Run with --dry-run to see what would happen without saving the
spreadsheet or making any PDFs.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

from classify import classify
from excel_writer import resolve_tab_name, write_all_to_excel
from ocr import FIELDS, extract_field
from pdf_io import add_stamp, load_pdf_images, make_and_save_pdf

# All paths are relative to this script's folder, not wherever you run it from.
BASE_DIR = Path(__file__).resolve().parent
PDF_PATH = BASE_DIR / "PDFs" / "Utilis 10-27-2025.pdf"  # TODO: pull from config instead of hardcoding
STAMP_PATH = BASE_DIR / "Pictures" / "Entered_no_bg.png"
OUTPUT_DIR = BASE_DIR / "PDFs" / "processed_pdfs"
STAMP_POSITION = (650, 1280)


@dataclass
class PageResult:
    page: int  # 1-based page number in the PDF
    company: str
    address: str
    bill_date: str | None = None
    amount: float | None = None
    effective_dates: str | None = None
    due_date: str | None = None


def process_page(image, page_num) -> tuple[PageResult | None, str | None]:
    """Returns (result, None) for a page that should be split between
    tenants, or (None, reason) for one that shouldn't: unrecognized company,
    unreadable address, untracked property, or a unit/utility-specific bill."""
    company = classify(image)
    if company is None:
        return None, "unrecognized company"

    raw_address = extract_field(image, "service_address", company)
    if raw_address is None:
        return None, "couldn't read the service address"

    tab_name, reason = resolve_tab_name(raw_address)
    if tab_name is None:
        return None, f"{raw_address!r}: {reason}"

    result = PageResult(page=page_num, company=company, address=tab_name)
    for field_name in FIELDS:
        if field_name == "service_address":
            continue
        setattr(result, field_name, extract_field(image, field_name, company))
    return result, None


def main(dry_run=False):
    images = load_pdf_images(PDF_PATH)

    candidates = []
    not_entered = {}  # page number -> reason it wasn't entered

    for page_num, image in enumerate(images, start=1):
        result, reason = process_page(image, page_num)
        if result is None:
            not_entered[page_num] = reason
        else:
            candidates.append(result)

    # Only pages that actually landed in the spreadsheet get stamped.
    written, skipped = write_all_to_excel(candidates, save=not dry_run)
    for result, reason in skipped:
        not_entered[result.page] = f"{result.address} ({result.company}): {reason}"

    written_pages = {r.page for r in written}
    print()
    print("ENTERED:")
    for r in written:
        print(f"  page {r.page:>3}  {r.address} ({r.company})  ${r.amount:,.2f}  "
              f"bill {r.bill_date}  due {r.due_date}  service {r.effective_dates}")
    print("NOT ENTERED:")
    for page_num in sorted(not_entered):
        print(f"  page {page_num:>3}  {not_entered[page_num]}")

    if not dry_run:
        output_images = [
            add_stamp(image, STAMP_PATH, STAMP_POSITION) if page_num in written_pages else image
            for page_num, image in enumerate(images, start=1)
        ]
        make_and_save_pdf(output_images, "processed", OUTPUT_DIR)

    print(f"\nEntered: {len(written_pages)}  Not entered: {len(not_entered)}"
          f"{'  (dry run - nothing saved)' if dry_run else ''}")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
