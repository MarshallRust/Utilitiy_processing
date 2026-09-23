"""
Throwaway - saves the exact crop box ocr.py uses for service_address on
page 0, plus the full page, so we can look at them and see whether the
box is actually pointed at the address.
"""

from ocr import get_field_config
from pdf_io import load_pdf_images

PDF_PATH = "PDFs/Utilis 10-27-2025.pdf"

images = load_pdf_images(PDF_PATH)
image = images[0]

print(f"Full page size: {image.size}")

cfg = get_field_config("service_address", "logan")
box = tuple(cfg["box"])
print(f"Configured box: {box}")

image.crop(box).save("debug_crop_box.png")
image.save("debug_full_page.png")
print("Saved debug_crop_box.png and debug_full_page.png")
