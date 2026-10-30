"""
A run happens in three stages:

1. READ   - every page is OCR'd: which company, the service address, and for
            bills that match a tab, the amount, dates and where on the page
            each one came from. Nothing is written.
2. REVIEW - a person checks it (review.py + the screens in gui.py): first
            every address next to its picture, then each bill that will be
            entered, value by value. They can fix anything the OCR got wrong.
3. WRITE  - the confirmed bills go into the sheet in one request, the
            stamped PDF and the report are saved.

The command line (main.py --cli) runs READ and WRITE with no review, using
the same rules.

What gets written, and what doesn't:
- Only bills whose service address exactly matches a tab are entered
  (a unit number or utility type on the end means it isn't split).
- All four values (amount, bill date, service dates, due date) must be
  there, and the dates have to make sense.
- The amount is this month's charges only, checked against the bill's own
  arithmetic (bill_amounts.py) - unless a reviewer typed it in themselves.
- Nothing already in the sheet is ever overwritten. Cells that already hold
  the same values mean the bill is done; anything else is reported.
- Pages that are in the sheet (entered now or already there) get the
  ENTERED stamp. Everything else is listed with the reason it was skipped.
"""

import re
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import sheet_layout
from app_paths import resource, user_dir
from bill_amounts import read_current_charges
from classify import classify
from ocr import field_search_box, locate_field
from pdf_io import iter_pages, page_count, render_page, save_stamped_copy

# Where the ENTERED stamp goes, in pixels on a 300 DPI page: the blank area
# below the meter table on Logan bills, clear of every value we read.
STAMP_POSITION = (300, 2100)
STAMP_PATH = resource("Pictures", "Entered_no_bg.png")

DATE_FIELDS = ("bill_date", "due_date", "effective_dates")
FIELD_LABELS = {
    "amount": "Total Bill",
    "bill_date": "Bill Date",
    "effective_dates": "Service Dates",
    "due_date": "Due Date",
}
FIELD_ORDER = ["amount", "bill_date", "effective_dates", "due_date"]

# What part of an unrecognized page to show the reviewer (the top of it).
TOP_OF_PAGE = (0, 0, 2550, 1000)


# --- what was read --------------------------------------------------------

@dataclass
class FieldRead:
    value: object = None          # current value (after any edit)
    read_value: object = None     # what the OCR read
    box: tuple | None = None      # where it came from, or where the app looked
    found: bool = False           # True if the OCR found it (box is the exact spot)
    edited: bool = False
    check: str | None = None      # amount only: the bill math that confirmed it
    check_boxes: dict = field(default_factory=dict)
    note: str | None = None       # amount only: why it couldn't be trusted

    @property
    def verified(self):
        """Confirmed by the bill's own arithmetic, and not changed since."""
        return self.check is not None and not self.edited and self.value is not None


@dataclass
class PageRead:
    page: int                       # 1-based page number in the PDF
    company: str | None = None      # 'logan', 'enbridge' or None
    raw_address: str | None = None  # what the OCR read
    address: str | None = None      # current (after any edit)
    address_box: tuple | None = None
    address_crop: object = None     # PIL image shown to the reviewer
    tab: str | None = None
    tab_reason: str | None = None   # why it didn't match a tab
    fields: dict | None = None      # name -> FieldRead, once read
    error: str | None = None

    @property
    def address_edited(self):
        return (self.address or "") != (self.raw_address or "")

    @property
    def is_bill(self):
        return self.company is not None

    def value(self, name):
        return self.fields[name].value if self.fields and name in self.fields else None

    def describe(self):
        amount = self.value("amount")
        amount = f"${amount:,.2f}" if isinstance(amount, (int, float)) else "$?"
        return (f"{self.tab} ({sheet_layout.COMPANY_LABELS[self.company]}) {amount}, "
                f"bill {self.value('bill_date')}, due {self.value('due_date')}, "
                f"service {self.value('effective_dates')}")


def _crop(image, box, pad=25):
    if image is None or box is None:
        return None
    left, top, right, bottom = box
    return image.crop((max(0, left - pad), max(0, top - pad),
                       min(image.width, right + pad), min(image.height, bottom + pad)))


def read_address(image, page):
    """Company + service address + tab match for one page."""
    page.company = classify(image)
    if page.company is None:
        page.address_crop = image.crop(TOP_OF_PAGE)
        return
    raw, box = locate_field(image, "service_address", page.company)
    page.raw_address = page.address = raw
    page.address_box = box or field_search_box("service_address", page.company)
    page.address_crop = _crop(image, page.address_box)


def match_tab(page, tab_names):
    if not page.is_bill:
        page.tab, page.tab_reason = None, "not recognized as a Logan City or Enbridge bill"
        return
    page.tab, page.tab_reason = sheet_layout.resolve_tab(page.address, tab_names)


def read_fields(image, page):
    """The four values for a bill, with where each one came from."""
    fields = {}
    for name in DATE_FIELDS:
        value, box = locate_field(image, name, page.company)
        fields[name] = FieldRead(value=value, read_value=value, found=value is not None,
                                 box=box or field_search_box(name, page.company))
    amount = read_current_charges(image, page.company)
    fields["amount"] = FieldRead(
        value=amount.amount, read_value=amount.amount, found=amount.amount is not None,
        box=amount.box or field_search_box("amount", page.company),
        check=amount.check, check_boxes=amount.boxes, note=amount.note,
    )
    page.fields = fields


def read_pages(pdf_path, tab_names, progress=print):
    """Stage 1: read every page. Values are only read for pages whose address
    matches a tab - read_missing_fields() catches up any that match later
    because a reviewer fixed the address."""
    pdf_path = Path(pdf_path)
    total = page_count(pdf_path)
    pages = []
    for page_num, image in iter_pages(pdf_path):
        progress(f"Reading page {page_num} of {total}...")
        page = PageRead(page=page_num)
        pages.append(page)
        if image is None:
            page.error = "page couldn't be opened (damaged scan?)"
            continue
        try:
            read_address(image, page)
            match_tab(page, tab_names)
            if page.tab:
                read_fields(image, page)
        except Exception as e:
            page.error = f"error while reading this page: {e}"
            page.tab = None
            traceback.print_exc()
    return pages


def read_missing_fields(pdf_path, pages, progress=print):
    """Read the values for bills that now match a tab but weren't read yet."""
    todo = [p for p in pages if p.tab and p.fields is None and not p.error]
    for i, page in enumerate(todo, start=1):
        progress(f"Reading bill {i} of {len(todo)} (page {page.page})...")
        image = render_page(pdf_path, page.page)
        if image is not None:
            read_fields(image, page)


# --- deciding -------------------------------------------------------------

def parse_amount(text):
    """'$1,234.56' / '58.59' / '58.5' -> float, or None if it isn't a price."""
    cleaned = str(text).strip().replace("$", "").replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d{1,2})?", cleaned):
        return None
    return round(float(cleaned), 2)


def parse_service_dates(text):
    """'9/18/2026 - 10/19/2026' (any spacing) -> normalized string, or None."""
    match = re.fullmatch(r"\s*(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})\s*", str(text))
    if not match:
        return None
    start, end = (sheet_layout.parse_date(d) for d in match.groups())
    if start is None or end is None or end < start:
        return None
    return f"{match.group(1)} - {match.group(2)}"


def value_problems(page):
    """Everything wrong with a bill's values that stops it being entered."""
    if not page.fields:
        return ["its values haven't been read"]
    problems = []
    for name in FIELD_ORDER:
        value = page.value(name)
        label = FIELD_LABELS[name]
        if value in (None, ""):
            note = page.fields[name].note if name == "amount" else None
            problems.append(f"{label} is missing" + (f" ({note})" if note else ""))
        elif name == "amount" and not isinstance(value, (int, float)):
            problems.append(f"{label} isn't a dollar amount")
        elif name == "amount" and value < 0:
            problems.append(f"{label} is a credit (${value:,.2f})")
        elif name in ("bill_date", "due_date") and sheet_layout.parse_date(value) is None:
            problems.append(f"{label} isn't a date (m/d/yyyy)")
        elif name == "effective_dates" and parse_service_dates(value) is None:
            problems.append(f"{label} should look like 9/18/2026 - 10/19/2026")
    if not problems:
        date_problem = sheet_layout.check_dates(_DateView(page))
        if date_problem:
            problems.append(date_problem)
    return problems


class _DateView:
    """What sheet_layout.check_dates expects: .bill_date and .due_date."""
    def __init__(self, page):
        self.bill_date, self.due_date = page.value("bill_date"), page.value("due_date")


@dataclass
class Decision:
    group: str        # 'enter', 'already', 'skip'
    reason: str | None = None


def decide(pages, grids):
    """Which group each page is in right now: 'enter' (will be entered,
    after review), 'already' (the sheet already has exactly these values),
    or 'skip' (won't be entered, with the reason). For 'enter' pages whose
    values are incomplete, the reason says what's missing - the reviewer can
    fill it in."""
    per_tab = {}
    for p in pages:
        if p.tab:
            per_tab.setdefault((p.tab, p.company), []).append(p.page)

    decisions = {}
    for p in pages:
        label = f"{p.tab} ({sheet_layout.COMPANY_LABELS.get(p.company, '?')})"
        if p.error:
            decisions[p.page] = Decision("skip", p.error)
        elif not p.tab:
            decisions[p.page] = Decision("skip", p.tab_reason)
        elif len(per_tab[(p.tab, p.company)]) > 1:
            others = ", ".join(map(str, per_tab[(p.tab, p.company)]))
            decisions[p.page] = Decision("skip", f"{label}: {len(per_tab[(p.tab, p.company)])} bills "
                                                 f"matched this tab (pages {others}) - fix the address on "
                                                 "the wrong one, or enter by hand")
        else:
            decisions[p.page] = _decide_bill(p, grids.get(p.tab), label)
    return decisions


def _decide_bill(page, grid, label):
    if grid is None:
        return Decision("enter", None)  # tab not loaded yet; checked again before writing
    bill_date = sheet_layout.parse_date(page.value("bill_date")) if page.fields else None
    if bill_date is None:
        problems = value_problems(page)
        return Decision("enter", "; ".join(problems) if problems else None)
    target, reason = sheet_layout.find_target(grid, page.company, bill_date.month)
    if target is None:
        return Decision("skip", f"{label}: {reason}")
    action, reason = sheet_layout.plan_write(grid, target, _SheetView(page))
    if action == "already":
        return Decision("already", None)
    if action == "skip":
        return Decision("skip", f"{label}: {reason}")
    problems = value_problems(page)
    return Decision("enter", "; ".join(problems) if problems else None)


class _SheetView:
    """What sheet_layout.plan_write expects: the four values as attributes."""
    def __init__(self, page):
        for name in FIELD_ORDER:
            value = page.value(name)
            if name == "effective_dates" and value:
                value = parse_service_dates(value) or value
            setattr(self, name, value)


# --- writing --------------------------------------------------------------

@dataclass
class RunSummary:
    pdf_path: Path
    sheet_title: str
    pages: int = 0
    dry_run: bool = False
    reviewed: bool = False
    entered: list = field(default_factory=list)       # PageRead written this run
    already: list = field(default_factory=list)       # PageRead already in the sheet
    not_entered: dict = field(default_factory=dict)   # page -> reason
    edits: list = field(default_factory=list)         # what the reviewer changed
    errors: list = field(default_factory=list)
    output_pdf: Path | None = None
    results_file: Path | None = None

    def report(self):
        lines = [
            f"Utility bill run - {datetime.now():%Y-%m-%d %I:%M %p}",
            f"PDF:   {self.pdf_path}",
            f"Sheet: {self.sheet_title}",
        ]
        if self.dry_run:
            lines.append("PREVIEW ONLY - nothing was written to the sheet")
        if self.reviewed:
            lines.append("Every address and every entered bill was checked by a person before writing.")
        lines += ["", f"{'WOULD BE ENTERED' if self.dry_run else 'ENTERED'} ({len(self.entered)}):"]
        lines += [f"  page {p.page:>3}  {p.describe()}" for p in self.entered] or ["  (none)"]
        if self.already:
            lines += ["", f"ALREADY IN THE SHEET ({len(self.already)}):"]
            lines += [f"  page {p.page:>3}  {p.describe()}" for p in self.already]
        lines += ["", f"NOT ENTERED ({len(self.not_entered)}):"]
        lines += [f"  page {n:>3}  {reason}" for n, reason in sorted(self.not_entered.items())] or ["  (none)"]
        if self.edits:
            lines += ["", f"CHANGED DURING REVIEW ({len(self.edits)}):"] + [f"  {e}" for e in self.edits]
        if self.output_pdf:
            lines += ["", f"Stamped PDF: {self.output_pdf}"]
        if self.errors:
            lines += ["", "ERROR DETAILS (for troubleshooting):"] + self.errors
        return "\n".join(lines)


def build_summary(pdf_path, sheet_title, pages, grids, to_enter, dry_run=False, reviewed=False,
                  skipped_by_reviewer=None, edits=None):
    """Final decision for every page, plus the cells to write. to_enter is
    the set of page numbers allowed in (every 'enter' page for an
    unreviewed run, the confirmed ones after review)."""
    skipped_by_reviewer = skipped_by_reviewer or {}
    summary = RunSummary(pdf_path=Path(pdf_path), sheet_title=sheet_title, pages=len(pages),
                         dry_run=dry_run, reviewed=reviewed, edits=list(edits or []))
    decisions = decide(pages, grids)
    cells = []
    for p in pages:
        d = decisions[p.page]
        if p.page in skipped_by_reviewer:
            summary.not_entered[p.page] = skipped_by_reviewer[p.page]
        elif d.group == "already":
            summary.already.append(p)
        elif d.group == "skip":
            summary.not_entered[p.page] = d.reason
        elif p.page not in to_enter:
            summary.not_entered[p.page] = f"{p.tab}: not confirmed in review"
        else:
            problems = value_problems(p)
            if problems:
                summary.not_entered[p.page] = f"{p.tab} ({sheet_layout.COMPANY_LABELS[p.company]}): " + "; ".join(problems)
                continue
            month = sheet_layout.parse_date(p.value("bill_date")).month
            target, reason = sheet_layout.find_target(grids[p.tab], p.company, month)
            if target is None:
                summary.not_entered[p.page] = f"{p.tab}: {reason}"
                continue
            view = _SheetView(p)
            for name in FIELD_ORDER:
                cells.append((sheet_layout.a1(p.tab, target.rows[name], target.col), getattr(view, name)))
            summary.entered.append(p)
        if p.error:
            summary.errors.append(f"page {p.page}: {p.error}")
    return summary, cells


def commit(summary, cells, sheet, progress=print):
    """Stage 3: write the cells in one request, then stamp and save."""
    if summary.dry_run:
        _save_log(summary)
        return summary
    if cells:
        progress("Writing to the sheet...")
        sheet.write_cells(cells)
    done_pages = {p.page for p in summary.entered + summary.already}
    progress("Saving the stamped PDF...")
    pdf_path = summary.pdf_path
    out_pdf = pdf_path.with_name(f"{pdf_path.stem} - processed {datetime.now():%Y-%m-%d %H%M}.pdf")
    summary.output_pdf = save_stamped_copy(pdf_path, out_pdf, done_pages, STAMP_PATH, STAMP_POSITION)
    summary.results_file = out_pdf.with_suffix(".txt")
    summary.results_file.write_text(summary.report(), encoding="utf-8")
    _save_log(summary)
    return summary


def run(pdf_path, sheet, dry_run=False, progress=print):
    """No-review run (command line): read, then write every bill that passes."""
    sheet_title, tab_names = sheet.title_and_tabs()
    pages = read_pages(pdf_path, tab_names, progress)
    progress("Checking the sheet...")
    grids = sheet.read_tabs(p.tab for p in pages if p.tab)
    decisions = decide(pages, grids)
    to_enter = {p.page for p in pages if decisions[p.page].group == "enter"}
    summary, cells = build_summary(pdf_path, sheet_title, pages, grids, to_enter, dry_run=dry_run)
    return commit(summary, cells, sheet, progress)


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
