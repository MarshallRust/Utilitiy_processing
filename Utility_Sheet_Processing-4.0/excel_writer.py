"""
Everything that touches the actual spreadsheet: matching an OCR'd address
to a real tab name, and writing all of a run's results in one load/save
instead of V3's one load/save per page.
"""

from pathlib import Path

from openpyxl import load_workbook

EXCEL_PATH = Path(__file__).resolve().parent / "Utilities Billed to Tenants - Sept25.xlsx"  # TODO: pull from config instead of hardcoding

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
COMPANY_LABELS = {"enbridge": "Enbridge", "logan": "Logan City"}

# Column B label in the sheet -> the PageResult field written on that row.
# Each company block looks like:
#     row N-1:  (blank) | ' '          | Jan | Feb | ... | Dec
#     row N:    Enbridge | Total Bill
#     row N+1:           | Bill Date
#     row N+2:           | Service Dates
#     row N+3:           | Due Date
# Rows are found by these labels instead of fixed offsets, so a missing or
# moved row gets reported instead of silently writing into the wrong one
# (V4's old offsets put the bill date in Total Bill and the amount over the
# tenant split formulas).
FIELD_LABELS = {
    "total bill": "amount",
    "bill date": "bill_date",
    "service dates": "effective_dates",
    "due date": "due_date",
}
BLOCK_HEIGHT = 6  # how far below the company row to look for the field labels

_tab_lookup = None  # cache: {cleaned_tab_name: original_tab_name}


def _clean(s):
    """Lowercase, strip spaces/punctuation - so '230 W 100 N,' and '230w100n'
    compare equal."""
    if not s:
        return ""
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def _load_tabs():
    global _tab_lookup
    if _tab_lookup is None:
        wb = load_workbook(EXCEL_PATH, read_only=True)
        _tab_lookup = {_clean(tab): tab for tab in wb.sheetnames}
        wb.close()
    return _tab_lookup


def resolve_tab_name(address):
    """Given a raw OCR'd address, return (tab_name, None) if it should be
    split, or (None, reason) if not.

    Only an exact match to a tab counts. If the bill's address is a tracked
    property plus anything extra - a unit number ('61 W 500 N 101',
    '61 W 500 N #4') or a utility type ('61 W 500 N WATER') - that bill
    isn't split between tenants, so it's skipped. Comparison ignores spaces
    and punctuation, so OCR spacing like '438 E100 S' still matches."""
    if not address:
        return None, "no address read"

    cleaned_address = _clean(address)
    tabs = _load_tabs()

    if cleaned_address in tabs:
        return tabs[cleaned_address], None

    # Not an exact match - is it a tracked property with something extra on
    # the end? Longest tab first, so '1320 E 400 N' can't be claimed by a
    # shorter tab that happens to be its ending.
    for cleaned_tab in sorted(tabs, key=len, reverse=True):
        if cleaned_address.startswith(cleaned_tab):
            extra = address_suffix(address, tabs[cleaned_tab])
            return None, f"'{tabs[cleaned_tab]}' + '{extra}' (unit/utility bill - not split)"

    return None, "not a tracked property"


def address_suffix(address, tab_name):
    """Best-effort readable version of whatever follows the tab's address in
    the OCR'd text, for the run summary. e.g. ('61 W 500 N WATER', '61 W 500 N') -> 'WATER'."""
    target = _clean(tab_name)
    seen = ""
    for i, ch in enumerate(address):
        if ch.isalnum():
            seen += ch.lower()
        if seen == target:
            return address[i + 1:].strip() or "?"
    return "?"


def _parse_month(bill_date):
    """'10/13/2025' -> 'Oct' - the header row uses 3-letter month names."""
    month_num = int(bill_date.split("/")[0])
    return MONTHS[month_num - 1]


def _find_company_row(ws, label):
    target = _clean(label)
    for row in ws.iter_rows(min_col=1, max_col=1):
        if _clean(row[0].value) == target:
            return row[0].row
    return None


def _find_header_row(ws, company_row):
    """The month header sits just above the company row. Look a few rows up
    for the row that actually has month names in it, rather than assuming
    a fixed distance."""
    for row in range(company_row - 1, max(0, company_row - 4), -1):
        values = {str(ws.cell(row=row, column=c).value).strip() for c in range(2, ws.max_column + 1)}
        if "Jan" in values and "Dec" in values:
            return row
    return None


def _find_field_rows(ws, company_row):
    """Map each field to its row by the label in column B, e.g.
    {'amount': 13, 'bill_date': 14, ...}. Missing labels are left out."""
    rows = {}
    for row in range(company_row, company_row + BLOCK_HEIGHT):
        label = ws.cell(row=row, column=2).value
        field = FIELD_LABELS.get(str(label).strip().lower()) if label else None
        if field and field not in rows:
            rows[field] = row
    return rows


def _find_month_column(ws, header_row, month_name):
    for col in range(2, ws.max_column + 1):
        header = ws.cell(row=header_row, column=col).value
        if header and str(header).strip() == month_name:
            return col
    return None


def write_all_to_excel(results, save=True):
    """Writes every page's results into the workbook in one load and one
    save. Returns (written, skipped): the results that actually landed in
    the sheet, and (result, reason) pairs for the ones that didn't.

    A result is only written if every field was read - a partial row is
    worse than no row, because it looks done. Nothing is written for a
    result that would land on a tab+company already written earlier in
    the same run (a duplicate bill)."""
    if not results:
        print("Nothing to write - no processed pages")
        return [], []

    wb = load_workbook(EXCEL_PATH)
    written = []
    skipped = []
    filled = {}  # (tab, company) -> page number already written there this run

    for result in results:
        missing = [f for f in FIELD_LABELS.values() if getattr(result, f) in (None, "")]
        if missing:
            skipped.append((result, f"couldn't read: {', '.join(missing)}"))
            continue

        key = (result.address, result.company)
        if key in filled:
            skipped.append((result, f"duplicate - page {filled[key]} already filled this tab"))
            continue

        if result.address not in wb.sheetnames:
            skipped.append((result, "no matching tab in workbook"))
            continue
        ws = wb[result.address]

        label = COMPANY_LABELS.get(result.company)
        company_row = _find_company_row(ws, label)
        if company_row is None:
            skipped.append((result, f"no '{label}' section on this tab"))
            continue

        header_row = _find_header_row(ws, company_row)
        if header_row is None:
            skipped.append((result, f"no month header row above '{label}'"))
            continue

        field_rows = _find_field_rows(ws, company_row)
        missing_rows = [f for f in FIELD_LABELS.values() if f not in field_rows]
        if missing_rows:
            skipped.append((result, f"'{label}' section is missing rows for: {', '.join(missing_rows)}"))
            continue

        month_name = _parse_month(result.bill_date)
        month_col = _find_month_column(ws, header_row, month_name)
        if month_col is None:
            skipped.append((result, f"no column for {month_name}"))
            continue

        for field_name, row in field_rows.items():
            ws.cell(row=row, column=month_col, value=getattr(result, field_name))
        filled[key] = result.page
        written.append(result)

    if save:
        wb.save(EXCEL_PATH)
        print(f"Saved {len(written)} of {len(results)} results to {EXCEL_PATH}")
    else:
        print(f"Dry run - would have saved {len(written)} of {len(results)} results to {EXCEL_PATH}")
    return written, skipped
