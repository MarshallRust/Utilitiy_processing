"""
One full run: read every page of a scanned PDF, work out which bills go in
the sheet, write them, and save a stamped copy of the PDF next to the
original. Used by both the window (gui.py) and the command line (main.py).

What gets written, and what doesn't:
- Only bills whose service address exactly matches a tab are entered
  (a unit number or utility type on the end means it isn't split).
- All four fields (amount, bill date, service dates, due date) must read
  cleanly, and the dates have to make sense.
- Nothing already in the sheet is ever overwritten. If the cells already
  hold the same values, the bill counts as done; if they hold something
  else, it's reported for a person to check.
- Pages that are in the sheet (entered now or already there) get the
  ENTERED stamp. Everything else is listed with the reason it was skipped.
"""

import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import sheet_layout
from app_paths import resource, user_dir
from classify import classify
from bill_amounts import read_current_charges
from ocr import FIELDS as FIELD_CONFIG, extract_field
from pdf_io import iter_pages, page_count, save_stamped_copy

# Where the ENTERED stamp goes, in pixels on a 300 DPI page: the blank area
# below the meter table on Logan bills, clear of every value we read.
STAMP_POSITION = (300, 2100)
STAMP_PATH = resource("Pictures", "Entered_no_bg.png")


@dataclass
class PageResult:
    page: int  # 1-based page number in the PDF
    company: str
    address: str  # the matching tab name
    bill_date: str | None = None
    amount: float | None = None
    effective_dates: str | None = None
    due_date: str | None = None
    note: str | None = None  # why the amount couldn't be trusted, e.g. an auto-pay bill

    def describe(self):
        amount = f"${self.amount:,.2f}" if self.amount is not None else "$?"
        return (f"{self.address} ({sheet_layout.COMPANY_LABELS[self.company]}) {amount}, "
                f"bill {self.bill_date}, due {self.due_date}, service {self.effective_dates}")


@dataclass
class RunSummary:
    pdf_path: Path
    sheet_title: str
    pages: int = 0
    dry_run: bool = False
    entered: list = field(default_factory=list)       # PageResult written this run
    already: list = field(default_factory=list)       # PageResult already in the sheet
    not_entered: dict = field(default_factory=dict)   # page -> reason
    errors: list = field(default_factory=list)        # tracebacks, for whoever maintains this
    output_pdf: Path | None = None
    results_file: Path | None = None

    def report(self):
        lines = [
            f"Utility bill run - {datetime.now():%Y-%m-%d %I:%M %p}",
            f"PDF:   {self.pdf_path}",
            f"Sheet: {self.sheet_title}",
            "PREVIEW ONLY - nothing was written to the sheet" if self.dry_run else "",
            "",
            f"{'WOULD BE ENTERED' if self.dry_run else 'ENTERED'} ({len(self.entered)}):",
        ]
        lines += [f"  page {r.page:>3}  {r.describe()}" for r in self.entered] or ["  (none)"]
        if self.already:
            lines += ["", f"ALREADY IN THE SHEET ({len(self.already)}):"]
            lines += [f"  page {r.page:>3}  {r.describe()}" for r in self.already]
        lines += ["", f"NOT ENTERED ({len(self.not_entered)}):"]
        lines += [f"  page {p:>3}  {reason}" for p, reason in sorted(self.not_entered.items())] or ["  (none)"]
        if self.output_pdf:
            lines += ["", f"Stamped PDF: {self.output_pdf}"]
        if self.errors:
            lines += ["", "ERROR DETAILS (for troubleshooting):"] + self.errors
        return "\n".join(line for line in lines if line is not None)


def process_page(image, page_num, tab_names):
    """Returns (PageResult, None) for a bill that should go in the sheet,
    or (None, reason) for one that shouldn't."""
    company = classify(image)
    if company is None:
        return None, "not recognized as a Logan City or Enbridge bill (back page or a different company?)"

    raw_address = extract_field(image, "service_address", company)
    tab, reason = sheet_layout.resolve_tab(raw_address, tab_names)
    if tab is None:
        return None, reason

    result = PageResult(page=page_num, company=company, address=tab)
    for field_name in FIELD_CONFIG:
        if field_name not in ("service_address", "amount"):
            setattr(result, field_name, extract_field(image, field_name, company))

    # The amount is this month's charges only - never an old unpaid balance -
    # and only if the bill's own numbers add up (see bill_amounts.py).
    result.amount, result.note = read_current_charges(image, company)
    return result, None


def run(pdf_path, sheet, dry_run=False, progress=print):
    pdf_path = Path(pdf_path)
    sheet_title, tab_names = sheet.title_and_tabs()
    summary = RunSummary(pdf_path=pdf_path, sheet_title=sheet_title, dry_run=dry_run)
    summary.pages = page_count(pdf_path)

    # 1. Read every page.
    candidates = []
    for page_num, image in iter_pages(pdf_path):
        progress(f"Reading page {page_num} of {summary.pages}...")
        if image is None:
            summary.not_entered[page_num] = "page couldn't be opened (damaged scan?)"
            continue
        try:
            result, reason = process_page(image, page_num, tab_names)
        except Exception as e:
            summary.not_entered[page_num] = f"error while reading this page: {e}"
            summary.errors.append(f"page {page_num}:\n{traceback.format_exc()}")
            continue
        if result is None:
            summary.not_entered[page_num] = reason
        else:
            candidates.append(result)

    # 2. Decide what to write.
    progress("Checking the sheet...")
    grids = sheet.read_tabs(c.address for c in candidates)
    per_tab = {}
    for c in candidates:
        per_tab.setdefault((c.address, c.company), []).append(c.page)

    cells = []
    for c in candidates:
        label = f"{c.address} ({sheet_layout.COMPANY_LABELS[c.company]})"
        missing = [f for f in sheet_layout.FIELDS if getattr(c, f) in (None, "")]
        if c.note:
            summary.not_entered[c.page] = f"{label}: {c.note}"
            continue
        if missing:
            summary.not_entered[c.page] = f"{label}: couldn't read {', '.join(missing).replace('_', ' ')}"
            continue
        pages = per_tab[(c.address, c.company)]
        if len(pages) > 1:
            summary.not_entered[c.page] = (f"{label}: {len(pages)} bills matched this tab "
                                           f"(pages {', '.join(map(str, pages))}) - enter by hand")
            continue
        date_problem = sheet_layout.check_dates(c)
        if date_problem:
            summary.not_entered[c.page] = f"{label}: {date_problem}"
            continue

        month = sheet_layout.parse_date(c.bill_date).month
        target, reason = sheet_layout.find_target(grids.get(c.address, []), c.company, month)
        if target is None:
            summary.not_entered[c.page] = f"{label}: {reason}"
            continue

        action, reason = sheet_layout.plan_write(grids[c.address], target, c)
        if action == "already":
            summary.already.append(c)
        elif action == "skip":
            summary.not_entered[c.page] = f"{label}: {reason}"
        else:
            for f in sheet_layout.FIELDS:
                cells.append((sheet_layout.a1(c.address, target.rows[f], target.col), getattr(c, f)))
            summary.entered.append(c)

    # 3. Write, stamp, and save the results.
    if not dry_run:
        if cells:
            progress("Writing to the sheet...")
            sheet.write_cells(cells)
        done_pages = {r.page for r in summary.entered + summary.already}
        progress("Saving the stamped PDF...")
        out_pdf = pdf_path.with_name(f"{pdf_path.stem} - processed {datetime.now():%Y-%m-%d %H%M}.pdf")
        summary.output_pdf = save_stamped_copy(pdf_path, out_pdf, done_pages, STAMP_PATH, STAMP_POSITION)
        summary.results_file = out_pdf.with_suffix(".txt")
        summary.results_file.write_text(summary.report(), encoding="utf-8")

    _save_log(summary)
    return summary


def _save_log(summary):
    """Keep a copy of every run's report (previews too) in the app's own
    folder, so there's a history even if the PDFs get moved."""
    try:
        log_dir = user_dir() / "logs"
        log_dir.mkdir(exist_ok=True)
        kind = "preview" if summary.dry_run else "run"
        (log_dir / f"{datetime.now():%Y-%m-%d_%H%M%S}_{kind}.txt").write_text(summary.report(), encoding="utf-8")
    except OSError:
        pass  # a log that can't be saved shouldn't fail the run
