"""
Throwaway - replicates extract_field's retry loop by hand for page 0's
service_address, printing what each attempt actually saw, so we can see
exactly which attempt (if any) finds the term and what breaks it if not.
"""

from ocr import _find_matching_line, _tesseract_config, get_field_config
from pdf_io import load_pdf_images
from pytesseract import pytesseract

PDF_PATH = "PDFs/Utilis 10-27-2025.pdf"
PAGE_INDEX = 0
FIELD = "service_address"
COMPANY = "logan"

images = load_pdf_images(PDF_PATH)
image = images[PAGE_INDEX]

cfg = get_field_config(FIELD, COMPANY)
left, top, right, bottom = cfg["box"]
size_offset = 0
add_offset = 25

for attempt in range(1, 6):
    width, height = image.size
    crop_box = (
        max(0, left - size_offset),
        max(0, top - size_offset),
        min(width, right + size_offset),
        min(height, bottom + size_offset),
    )
    cropped = image.crop(crop_box)
    cropped.save(f"debug_attempt_{attempt}.png")

    config = _tesseract_config(attempt, cfg)
    data = pytesseract.image_to_data(
        cropped, lang="eng", config=config, output_type=pytesseract.Output.DICT
    )
    raw_text = " ".join(w for w in data["text"] if w.strip())

    line_text = _find_matching_line(data, cfg["term"])

    print(f"--- attempt {attempt} ---")
    print(f"  crop box: {crop_box}")
    print(f"  config: {config}")
    print(f"  all OCR'd text: {raw_text!r}")
    print(f"  matched line: {line_text!r}")

    size_offset += add_offset
    add_offset += 25
