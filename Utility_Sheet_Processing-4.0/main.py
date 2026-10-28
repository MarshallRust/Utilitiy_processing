"""
Entry point.

    python main.py                          opens the window (what the .exe does)
    python main.py --cli BILLS.pdf          runs from the terminal
    python main.py --cli BILLS.pdf --dry-run   ...without writing anything
    python main.py --selftest [REPORT.txt]  checks OCR + PDF handling work
                                            (the Windows build runs this)
"""

import sys
import traceback
from pathlib import Path


def run_cli(args):
    import google_sheets
    import pipeline
    import settings
    from app_paths import configure_tesseract

    pdfs = [a for a in args if not a.startswith("--")]
    if not pdfs:
        print(__doc__)
        return 2
    configure_tesseract()

    creds = google_sheets.load_saved_credentials() or google_sheets.sign_in()
    sheet_id = google_sheets.sheet_id_from_link(settings.load()["sheet_link"])
    summary = pipeline.run(
        Path(pdfs[0]).expanduser().resolve(),
        google_sheets.Sheet(creds, sheet_id),
        dry_run="--dry-run" in args,
    )
    print()
    print(summary.report())
    return 0


def selftest(report_path):
    """Exercise everything that can break in packaging, without Google:
    the bundled Tesseract reads text, PyMuPDF renders and writes a PDF,
    and the bundled files are present. Writes a report and returns 0/1."""
    import tempfile

    lines, ok = [], True

    def check(name, fn):
        nonlocal ok
        try:
            lines.append(f"PASS  {name}: {fn()}")
        except Exception:
            ok = False
            lines.append(f"FAIL  {name}:\n{traceback.format_exc()}")

    from app_paths import configure_tesseract, override, resource

    check("tesseract found", configure_tesseract)

    def ocr_reads_text():
        from PIL import Image, ImageDraw, ImageFont
        from pytesseract import pytesseract
        image = Image.new("RGB", (1400, 220), "white")
        try:
            font = ImageFont.truetype("arial.ttf", 72)
        except OSError:
            font = ImageFont.load_default(size=72)
        ImageDraw.Draw(image).text((40, 60), "Total Amount Due 206.14", fill="black", font=font)
        text = pytesseract.image_to_string(image)
        assert "206.14" in text, f"OCR read {text!r}"
        return text.strip()
    check("OCR reads a test image", ocr_reads_text)

    def field_reads():
        # The same calls the real run makes, with the same whitelists
        # (spaces, '/', numeric mode). These are what broke on Windows the
        # first time, so the build fails if they ever break again.
        from PIL import Image, ImageDraw, ImageFont
        import ocr
        try:
            font = ImageFont.truetype("arial.ttf", 64)
        except OSError:
            font = ImageFont.load_default(size=64)
        results = []
        for text, cfg, term, check_value in [
            ("Total Amount Due 206.14", {"psm": 6}, "Total Amount Due",
             lambda line: ocr.parse_amount(line, "logan") == 206.14),
            ("Bill Date 10/22/2025", ocr.get_field_config("bill_date", "logan"), "Bill Date",
             lambda line: "10/22/2025" in line),
            ("Service Address 420 N 400 E", {"psm": 6}, "Service Address",
             lambda line: ocr.parse_address(line, "logan") == "420 N 400 E"),
        ]:
            image = Image.new("RGB", (1800, 200), "white")
            ImageDraw.Draw(image).text((40, 50), text, fill="black", font=font)
            cfg = {k: v for k, v in cfg.items() if k != "invert"}  # test image is dark-on-light
            lines = ocr._find_matching_lines(ocr.ocr_data(image, ocr._tesseract_args(1, cfg)), term)
            assert lines and check_value(lines[0]), f"{text!r} read as {lines!r}"
            results.append(lines[0])
        return " | ".join(results)
    check("field OCR with whitelists", field_reads)

    def pdf_round_trip():
        import fitz
        from pdf_io import iter_pages, save_stamped_copy
        import pipeline
        tmp = Path(tempfile.mkdtemp())
        src = tmp / "test.pdf"
        with fitz.open() as doc:
            for i in range(3):
                doc.new_page(width=612, height=792).insert_text((72, 72), f"page {i + 1}")
            doc.save(src)
        pages = [(n, img.size) for n, img in iter_pages(src)]
        assert pages == [(n, (2550, 3300)) for n in (1, 2, 3)], pages
        out = save_stamped_copy(src, tmp / "out.pdf", {2}, pipeline.STAMP_PATH, pipeline.STAMP_POSITION)
        with fitz.open(out) as doc:
            sizes = [(round(p.rect.width), round(p.rect.height)) for p in doc]
        assert sizes == [(612, 792)] * 3, sizes
        return f"rendered 3 pages, stamped page 2, output pages {sizes}"
    check("PDF render + stamp", pdf_round_trip)

    check("fields.yaml loads", lambda: f"{len(__import__('ocr').FIELDS)} fields from {override('fields.yaml')}")
    check("stamp image bundled", lambda: str(resource("Pictures", "Entered_no_bg.png").stat().st_size) + " bytes")
    check("Google libraries import", lambda: __import__("google_sheets").API)
    check("window code imports", lambda: __import__("gui").APP_NAME)

    secret = override("client_secret.json")
    lines.append(("PASS  " if secret.exists() else "WARN  ") + f"client_secret.json present: {secret.exists()}")

    Path(report_path).write_text("\n".join(lines) + f"\n\nRESULT: {'OK' if ok else 'FAILED'}\n", encoding="utf-8")
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        rest = [a for a in args if a != "--selftest"]
        return selftest(rest[0] if rest else "selftest_report.txt")
    if "--cli" in args:
        return run_cli([a for a in args if a != "--cli"])
    import gui
    gui.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
