"""
Temporary, throwaway diagnostic - doesn't write anything, doesn't touch
main.py. Prints what classify/extract_field/resolve_tab_name actually
returned for the first few pages, so we can see exactly which step is
coming back empty for every page.
"""

from classify import classify
from excel_writer import resolve_tab_name
from ocr import extract_field
from pdf_io import load_pdf_images

PDF_PATH = "PDFs/Utilis 10-27-2025.pdf"
PAGES_TO_CHECK = 5

images = load_pdf_images(PDF_PATH)
print(f"Loaded {len(images)} pages\n")

for i, image in enumerate(images[:PAGES_TO_CHECK]):
    print(f"--- page {i} ---")
    company = classify(image)
    print(f"  classify() -> {company!r}")

    if company is None:
        continue

    raw_address = extract_field(image, "service_address", company)
    print(f"  raw address -> {raw_address!r}")

    if raw_address is None:
        continue

    tab_name = resolve_tab_name(raw_address)
    print(f"  resolved tab -> {tab_name!r}")
