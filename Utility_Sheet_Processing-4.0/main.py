"""
Entry point.

    python main.py                          opens the window (what the .exe does)
    python main.py --cli BILLS.pdf          runs from the terminal
    python main.py --cli BILLS.pdf --dry-run   ...without writing anything
    python main.py --selftest [REPORT.txt]  checks OCR + PDF handling work
                                            (the Windows build runs this)
    python main.py --ui-selftest [FOLDER]   opens the window with sample bills,
                                            clicks through every screen, saves
                                            screenshots (the Windows build runs this)
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


def ui_selftest(out_dir):
    """Drive the real window through every screen with made-up bills - no
    OCR, no Google. Fails (returns 1) if anything raises. Screenshots of
    each screen go in out_dir when the platform allows it."""
    import time
    from datetime import date, timedelta
    from types import SimpleNamespace

    from PIL import Image, ImageDraw

    import gui
    import pdf_io
    import pipeline
    import review
    from pipeline import FieldRead, PageRead

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log, errors = [], []

    # Dialogs would block an unattended run: answer "yes" and record them.
    gui.messagebox = SimpleNamespace(
        showerror=lambda *a, **k: errors.append(f"error dialog: {a[1] if len(a) > 1 else a}"),
        showinfo=lambda *a, **k: log.append(f"info dialog: {a[1] if len(a) > 1 else a}"),
        askyesno=lambda *a, **k: True,
    )

    def fake_page(lines):
        image = Image.new("RGB", (2550, 3300), "white")
        draw = ImageDraw.Draw(image)
        for (x, y), text in lines:
            draw.text((x, y), text, fill="black")
        return image

    bill_date = date.today() - timedelta(days=5)
    due = bill_date + timedelta(days=21)
    start = bill_date - timedelta(days=30)
    fmt = lambda d: f"{d.month}/{d.day}/{d.year}"
    images = {
        1: fake_page([((1450, 560), "Service Address 100 Main St"), ((1450, 760), "Total Amount Due 206.14")]),
        2: fake_page([((1150, 330), f"Account Summary as of {fmt(bill_date)}"), ((90, 650), "Service Address: 200 Oak Ave")]),
        3: fake_page([((1450, 560), "Service Address 100 Main St 2")]),
        4: fake_page([((100, 100), "Some other company")]),
    }
    pdf_io.render_page = lambda pdf, n, dpi=300: images.get(n)
    pipeline.render_page = pdf_io.render_page
    pipeline.save_stamped_copy = lambda *a, **k: None

    def bill_fields(amount, check, due_value):
        def f(value, box, found=True):
            return FieldRead(value=value, read_value=value, box=box, found=found)
        return {
            "amount": FieldRead(value=amount, read_value=amount, box=(1800, 740, 2340, 800), found=amount is not None,
                                check=check, note=None if amount else "Enbridge auto-pay bill"),
            "bill_date": f(fmt(bill_date), (1400, 1320, 2340, 1380)),
            "effective_dates": f(f"{fmt(start)} - {fmt(bill_date)}", (100, 1380, 1300, 1440)),
            "due_date": f(due_value, (1400, 1400, 2340, 1460), found=due_value is not None),
        }

    def crop(n, box):
        return images[n].crop(box)

    pages = [
        PageRead(page=1, company="logan", raw_address="100 Main St", address="100 Main St",
                 address_box=(1400, 540, 2340, 620), address_crop=crop(1, (1400, 540, 2340, 620)),
                 tab="100 Main St", fields=bill_fields(206.14, "past due 0.00 + current 206.14 = total 206.14", fmt(due))),
        PageRead(page=2, company="enbridge", raw_address="200 Oak Ave", address="200 Oak Ave",
                 address_box=(60, 630, 1200, 720), address_crop=crop(2, (60, 630, 1200, 720)),
                 tab="200 Oak Ave", fields=bill_fields(None, None, None)),
        PageRead(page=3, company="logan", raw_address="100 Main St 2", address="100 Main St 2",
                 address_box=(1400, 540, 2340, 620), address_crop=crop(3, (1400, 540, 2340, 620)),
                 tab=None, tab_reason="'100 Main St' + '2' - unit or utility bill, not split"),
        PageRead(page=4, company=None, address_crop=crop(4, (0, 0, 2550, 1000)),
                 tab_reason="not recognized as a Logan City or Enbridge bill"),
    ]

    def grid(company_label):
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return [["x"], ["Account Number:"], ["", " "] + months, [company_label, "Total Bill"],
                ["", "Bill Date"], ["", "Service Dates"], ["", "Due Date"], ["", "#1 = 50%"]]

    class FakeSheet:
        writes = []
        def read_tabs(self, tabs):
            return {t: grid("Logan City" if t == "100 Main St" else "Enbridge") for t in tabs}
        def write_cells(self, cells):
            self.writes.extend(cells)

    sheet = FakeSheet()
    tabs = ["100 Main St", "200 Oak Ave"]
    session = review.ReviewSession(out_dir / "sample.pdf", sheet, "Sample sheet", tabs, pages,
                                   sheet.read_tabs(tabs))

    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as e:
        # No desktop to open a window on (some build machines) - not a code problem.
        (out_dir / "ui_selftest_report.txt").write_text(f"SKIPPED: couldn't open a window here ({e})\n", encoding="utf-8")
        return 0
    root.report_callback_exception = lambda *exc: errors.append("".join(traceback.format_exception(*exc)))
    app = gui.App(root)

    def pump(seconds=0.5, until=None):
        end = time.time() + 30
        stop = time.time() + seconds
        while time.time() < (end if until else stop):
            root.update()
            if until and until():
                break
            time.sleep(0.02)

    def shot(name):
        pump(0.6)
        try:
            from PIL import ImageGrab
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(out_dir / f"{name}.png")
            log.append(f"screenshot {name}.png")
        except Exception as e:
            log.append(f"no screenshot for {name}: {e}")

    try:
        pump(1)
        shot("0-start")
        app.start_review(session)
        shot("1-addresses")

        # Reviewer "fixes" the unit bill into a duplicate, sees it, puts it back.
        app._address_edited(3, tk.StringVar(value="100 Main St"))
        assert session.decisions()[1].group == "skip", "duplicate address should block page 1"
        app._address_edited(3, tk.StringVar(value="100 Main St 2"))
        assert session.decisions()[1].group == "enter", "page 1 should be back after undoing the edit"
        assert str(app.continue_button.cget("state")) == "disabled", "Continue must wait for the tick"
        app.addresses_checked_var.set(True)
        app._addresses_ticked()
        app._addresses_done()
        pump(until=lambda: getattr(app, "bill_page", None) == 1)
        assert app.bill_page == 1, "should be on the first bill"
        shot("2-bill-logan")

        app._bill_confirm()  # dates not ticked yet - must refuse
        assert 1 not in session.confirmed, "confirmed without ticks"
        for name, var in app.tick_vars.items():
            var.set(True)
            app._ticked(1, name)
        app._bill_confirm()
        assert 1 in session.confirmed, f"page 1 not confirmed: {session.confirm_problems(1)}"
        pump(until=lambda: getattr(app, "bill_page", None) == 2)
        shot("3-bill-autopay")

        app.field_vars["amount"].set("34.05")
        app.field_vars["due_date"].set(fmt(due))
        for name, var in app.tick_vars.items():
            var.set(True)
            app._ticked(2, name)
        app._bill_confirm()
        assert 2 in session.confirmed, f"page 2 not confirmed: {session.confirm_problems(2)}"
        pump(0.5)
        shot("4-summary")

        app._write()
        pump(until=lambda: app.last_summary is not None)
        assert app.last_summary is not None, "write didn't finish"
        assert len(app.last_summary.entered) == 2, f"expected 2 entered, got {len(app.last_summary.entered)}"
        assert len(sheet.writes) == 8, f"expected 8 cells written, got {len(sheet.writes)}"
        shot("5-done")
        log.append("edits logged: " + " | ".join(app.last_summary.edits))
    except Exception:
        errors.append(traceback.format_exc())
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    (out_dir / "ui_selftest_report.txt").write_text(
        "\n".join(log + ["", "ERRORS:" if errors else "No errors."] + errors) + "\n", encoding="utf-8")
    return 1 if errors else 0


def main():
    args = sys.argv[1:]
    if "--ui-selftest" in args:
        rest = [a for a in args if a != "--ui-selftest"]
        return ui_selftest(rest[0] if rest else "ui_selftest")
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
