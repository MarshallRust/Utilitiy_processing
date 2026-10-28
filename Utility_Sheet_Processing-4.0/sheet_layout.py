"""
Works out where a bill's values go in the billing sheet, using only the
cell values of a tab as a grid (a list of rows, each a list of cells).
Doesn't talk to Google or Excel - that keeps all the "which cell?" logic in
one place that can be tested without signing in to anything.

Each property has its own tab. Inside a tab, each company has a block:

    row N-1:            | ' '           | Jan | Feb | ... | Dec
    row N:   Enbridge   | Total Bill
    row N+1:            | Bill Date
    row N+2:            | Service Dates
    row N+3:            | Due Date
    (tenant split formula rows below)

Rows are found by their labels, never by fixed offsets, so a moved or
missing row is reported instead of written into the wrong place.
"""

from dataclasses import dataclass
from datetime import date, datetime

COMPANY_LABELS = {"enbridge": "Enbridge", "logan": "Logan City"}

# Column B label -> the bill field written on that row.
FIELD_LABELS = {
    "total bill": "amount",
    "bill date": "bill_date",
    "service dates": "effective_dates",
    "due date": "due_date",
}
FIELDS = list(FIELD_LABELS.values())
BLOCK_HEIGHT = 6  # how far below the company row to look for the field labels

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

# A bill dated outside this window from today is almost certainly a misread
# or the wrong year's paperwork, so it isn't written.
MAX_BILL_AGE_DAYS = 120
MAX_BILL_FUTURE_DAYS = 15


def clean(s):
    """Lowercase, strip spaces/punctuation - so '230 W 100 N,' and '230w100n'
    compare equal."""
    if s is None:
        return ""
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


# --- matching a bill's address to a tab ---------------------------------

def resolve_tab(address, tab_names):
    """Return (tab_name, None) if the bill should be split between tenants,
    or (None, reason) if not.

    Only an exact match to a tab counts. A tracked property plus anything
    extra - a unit number ('61 W 500 N 101', '61 W 500 N #4') or a utility
    type ('61 W 500 N WATER') - is a bill that isn't split, so it's skipped.
    Spaces and punctuation are ignored, so OCR spacing like '438 E100 S'
    still matches."""
    if not address:
        return None, "couldn't read the service address"

    tabs = {clean(t): t for t in tab_names}
    cleaned = clean(address)
    if cleaned in tabs:
        return tabs[cleaned], None

    # Longest tab first, so '1320 E 400 N' can't be claimed by a shorter tab.
    for cleaned_tab in sorted(tabs, key=len, reverse=True):
        if cleaned_tab and cleaned.startswith(cleaned_tab):
            extra = _suffix(address, cleaned_tab)
            return None, f"'{tabs[cleaned_tab]}' + '{extra}' - unit or utility bill, not split"

    return None, f"'{address}' - not a property in the sheet"


def _suffix(address, cleaned_tab):
    seen = ""
    for i, ch in enumerate(address):
        if ch.isalnum():
            seen += ch.lower()
        if seen == cleaned_tab:
            return address[i + 1:].strip() or "?"
    return "?"


# --- finding cells inside a tab ------------------------------------------

def _cell(grid, row, col):
    """0-based row/col lookup that tolerates ragged rows (Google's API drops
    trailing empty cells)."""
    if row < 0 or row >= len(grid):
        return None
    r = grid[row]
    if col < 0 or col >= len(r):
        return None
    value = r[col]
    return None if value == "" else value


def _month_key(value):
    text = str(value).strip().lower()[:3] if value is not None else ""
    return text if text in MONTHS else None


@dataclass
class Target:
    """Where one bill goes: 0-based row for each field, and the month column."""
    rows: dict  # field name -> 0-based row
    col: int    # 0-based column


def find_target(grid, company, bill_month):
    """Return (Target, None), or (None, reason) if the tab doesn't have the
    expected layout."""
    label = COMPANY_LABELS[company]
    company_row = next(
        (r for r in range(len(grid)) if clean(_cell(grid, r, 0)) == clean(label)), None
    )
    if company_row is None:
        return None, f"no '{label}' section on this tab"

    header_row = None
    for r in range(company_row - 1, max(-1, company_row - 4), -1):
        months = {_month_key(_cell(grid, r, c)) for c in range(1, len(grid[r]) if r < len(grid) else 0)}
        if "jan" in months and "dec" in months:
            header_row = r
            break
    if header_row is None:
        return None, f"no Jan-Dec header row above '{label}'"

    rows = {}
    for r in range(company_row, company_row + BLOCK_HEIGHT):
        field = FIELD_LABELS.get(str(_cell(grid, r, 1) or "").strip().lower())
        if field and field not in rows:
            rows[field] = r
    missing = [f for f in FIELDS if f not in rows]
    if missing:
        return None, f"'{label}' section is missing rows for: {', '.join(missing)}"

    target_month = MONTHS[bill_month - 1]
    col = next(
        (c for c in range(1, len(grid[header_row])) if _month_key(_cell(grid, header_row, c)) == target_month),
        None,
    )
    if col is None:
        return None, f"no column for {target_month.title()}"

    return Target(rows=rows, col=col), None


# --- deciding whether to write -------------------------------------------

def parse_date(text):
    try:
        return datetime.strptime(str(text).strip(), "%m/%d/%Y").date()
    except (ValueError, TypeError):
        return None


def check_dates(bill, today=None):
    """Return a reason the bill's dates look wrong, or None if they're fine."""
    today = today or date.today()
    bill_date = parse_date(bill.bill_date)
    due_date = parse_date(bill.due_date)
    if bill_date is None:
        return f"bill date '{bill.bill_date}' isn't a real date"
    if (today - bill_date).days > MAX_BILL_AGE_DAYS:
        return f"bill date {bill.bill_date} is more than {MAX_BILL_AGE_DAYS} days old - wrong year or misread?"
    if (bill_date - today).days > MAX_BILL_FUTURE_DAYS:
        return f"bill date {bill.bill_date} is in the future - misread?"
    if due_date is not None and due_date < bill_date:
        return f"due date {bill.due_date} is before bill date {bill.bill_date} - misread?"
    return None


def _same(field, existing, new):
    """Whether a value already in the sheet matches what we'd write, allowing
    for formatting ('$206.14' vs 206.14, '9/15/2025-10/14/2025' vs
    '9/15/2025 - 10/14/2025', '10/22/2025' vs '10/22/2025')."""
    if field == "amount":
        try:
            return round(float(str(existing).replace("$", "").replace(",", "").strip()), 2) == round(float(new), 2)
        except ValueError:
            return False
    if field in ("bill_date", "due_date"):
        a, b = parse_date(existing), parse_date(new)
        return a is not None and a == b
    return clean(existing) == clean(new)


def plan_write(grid, target, bill):
    """Compare the target cells with the bill. Returns one of:
        ('write', None)          - all four cells empty, safe to fill
        ('already', None)        - the sheet already has exactly these values
        ('skip', reason)         - some cells already have something else in them
    Never overwrites: a cell someone typed by hand, or an earlier run's
    value, is left alone and reported."""
    existing = {f: _cell(grid, target.rows[f], target.col) for f in FIELDS}
    filled = {f: v for f, v in existing.items() if v not in (None, "")}
    if not filled:
        return "write", None
    if len(filled) == len(FIELDS) and all(_same(f, filled[f], getattr(bill, f)) for f in FIELDS):
        return "already", None
    different = [f for f in filled if not _same(f, filled[f], getattr(bill, f))]
    if not different:
        # Some cells match and the rest are empty - a half-finished earlier entry.
        return "skip", "some of these cells are already filled in - finish or clear them by hand"
    return "skip", "the sheet already has different values here - check the bill and fix by hand"


def a1(tab, row, col):
    """0-based row/col -> "'Tab Name'!L4"."""
    letters = ""
    n = col + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return f"'{tab.replace(chr(39), chr(39) * 2)}'!{letters}{row + 1}"
