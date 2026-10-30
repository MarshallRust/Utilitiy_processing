"""
Reads the amount that goes in the sheet's "Total Bill" row: THIS month's
charges only, never an unpaid balance carried over from earlier months
(that would get split between the tenants a second time).

Every number is double-checked against the bill's own arithmetic before
it's trusted. A single misread digit (e.g. $14.62 read for $11.62) makes the
sum come out wrong, and the bill goes to a person instead of the sheet.

Enbridge - the Account Summary box, top right (NOT the payment stub at
the bottom of the page):
    Previous Balance Due - 10/09/26        48.01
    Payments Received - 10/19/26          -48.01
    Current Charges - Gas Service          58.59   <- this is what's entered
    Adjustments                             0.48
    Total Amount Due Upon Receipt         $59.07
  Check: previous + payments + current + adjustments = total
  Amount entered: the Current Charges line

Logan City - two tables on the right:
    top table:    Current Charges   105.21  (row just above Total Amount Due)
                  Total Amount Due  199.51
    lower table:  Past Due Amount    94.30  (row just above Bill Date)
  Check: past due + current charges = total
  (Logan's "Penalties" row is already included in Current Charges.)
  Amount entered: current charges
"""

import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageOps

from ocr import PRICE_RE, MAX_AMOUNT, locate_field, ocr_data, ocr_lines, offset_box

# Logan: the table rows are this many pixels apart on a 300 DPI page.
LOGAN_ROW_PITCH = 72
# Logan: the value column of the right-hand tables (pixels on a 300 DPI page).
LOGAN_VALUE_COLUMN = (1790, 2345)
VALUE_WHITELIST = "0123456789.,()-$"

# Enbridge: the Account Summary box, with room for scanner drift.
ENBRIDGE_SUMMARY_BOX = (1100, 260, 2350, 760)
SUMMARY_WHITELIST = "0123456789$.,ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz -/()*"

TOLERANCE = 0.005  # amounts must agree to the cent


@dataclass
class AmountRead:
    """What was read for the amount, and how much to trust it.

    amount  - this month's charges, or None if they couldn't be trusted
    note    - why not, in plain English (None when amount is set)
    check   - the arithmetic that confirmed it, e.g. "past due 94.30 +
              current 105.21 = total 199.51", for the reviewer to see
    box     - where the amount itself is on the page (300 DPI pixels)
    boxes   - every number used in the check: {"Total Amount Due": box, ...}
    """
    amount: float | None = None
    note: str | None = None
    check: str | None = None
    box: tuple | None = None
    boxes: dict = field(default_factory=dict)

    @property
    def verified(self):
        return self.amount is not None and self.check is not None


def read_current_charges(image, company):
    """Read this month's charges and check them against the bill's total."""
    if company == "enbridge":
        return _enbridge(image)
    return _logan(image)


def _money(text):
    """Every signed price in text, in order. '(360.12)' and '-48.01' are
    negative. Letters O/Q next to digits are read as 0 - a common OCR slip."""
    text = re.sub(r"(?<=[\d.,])[OoQ]|[OoQ](?=[\d.,])", "0", text)
    # "19,37" with a comma where the decimal point should be. Real thousands
    # separators ("1,758.87") have three digits after the comma, so they're
    # left alone. The bill's arithmetic check still has to pass.
    text = re.sub(r"(?<![\d,.])(\d{1,3}),(\d{2})(?![\d.,])", r"\1.\2", text)
    values = []
    for match in PRICE_RE.finditer(text.replace("$", "")):
        value = float(match.group(1).replace(",", "") + "." + match.group(2))
        before = text[:match.start()].rstrip()
        if before.endswith("-") or before.endswith("("):
            value = -value
        values.append(value)
    return values


def _fail(note, read):
    read.amount, read.note, read.check = None, note, None
    return read


def _check_range(amount):
    if amount < 0:
        return f"this month's charges read as a credit (${amount:,.2f}) - check by hand"
    if amount > MAX_AMOUNT:
        return f"this month's charges read as ${amount:,.2f}, more than ${MAX_AMOUNT:,.0f} - check by hand"
    return None


# --- Enbridge -------------------------------------------------------------

def _enbridge(image):
    crop_box = ENBRIDGE_SUMMARY_BOX
    data = ocr_data(
        image.crop(crop_box),
        ["--oem", "3", "--psm", "6", "-c", "preserve_interword_spaces=1",
         "-c", f"tessedit_char_whitelist={SUMMARY_WHITELIST}"],
    )
    read = AmountRead(box=crop_box)
    previous = payments = adjustments = 0.0
    current, total, unknown = [], None, []
    autopay = False

    for text, box in ocr_lines(data):
        low = text.lower()
        squashed = re.sub(r"[^a-z]", "", low)
        page_box = offset_box(box, crop_box)
        if "autopay" in squashed:
            autopay = True
            continue
        if "interest" in low or "as of" in low:
            continue  # the "...on or after 11/12/2026" and "Account Summary as of" lines
        amounts = _money(text)
        if not amounts:
            continue
        value = amounts[-1]
        if "previousbalance" in squashed:
            previous += value
            read.boxes["Previous Balance"] = page_box
        elif "payment" in squashed:
            payments += value
            read.boxes["Payments"] = page_box
        elif "currentcharges" in squashed:
            current.append(value)
            read.boxes["Current Charges"] = page_box
        elif "adjustment" in squashed:
            adjustments += value
            read.boxes["Adjustments"] = page_box
        elif "totalamount" in squashed:
            total = value
            read.boxes["Total Amount Due"] = page_box
        else:
            unknown.append(text)

    if read.boxes.get("Current Charges"):
        read.box = read.boxes["Current Charges"]
    if autopay:
        return _fail("Enbridge auto-pay bill - it prints \"AUTO PAY CUSTOMER\" instead of a total due "
                     "and has no due date", read)
    if not current:
        return _fail("couldn't read the Current Charges line", read)
    if len(current) > 1:
        return _fail("the bill has more than one Current Charges line", read)
    if unknown:
        return _fail(f"the summary has a line the app doesn't handle ('{unknown[0]}')", read)
    if total is None:
        return _fail("couldn't read the total due to double-check the charges", read)

    expected = previous + payments + current[0] + adjustments
    if abs(expected - total) > TOLERANCE:
        return _fail(f"the bill's numbers don't add up as read (previous {previous:.2f} + payments "
                     f"{payments:.2f} + current {current[0]:.2f} + adjustments {adjustments:.2f} "
                     f"!= total {total:.2f}) - probably a misread", read)

    amount = round(current[0], 2)
    if _check_range(amount):
        return _fail(_check_range(amount), read)
    read.amount = amount
    read.check = (f"previous {previous:.2f} + payments {payments:.2f} + current {current[0]:.2f} "
                  f"+ adjustments {adjustments:.2f} = total {total:.2f}")
    return read


# --- Logan City -----------------------------------------------------------

def _clean_cell(crop):
    """Cut the table's grid lines (and the edge of the blue label cell) off
    a value-cell crop. Left in, OCR reads them as digits or loses the
    decimal point: '61.73' came back as '6173', '5.44' as '644'."""
    gray = np.array(ImageOps.grayscale(crop))
    dark = gray < 110
    height, width = dark.shape
    column_dark, row_dark = dark.mean(axis=0), dark.mean(axis=1)
    right = next((x for x in range(width - 1, width // 2, -1) if column_dark[x] > 0.5), width)
    left = 0
    for x in range(width // 2):
        if column_dark[x] > 0.5 or gray[:, x].mean() < 200:
            left = x + 1
    top = 0
    for y in range(height // 2):
        if row_dark[y] > 0.5:
            top = y + 1
    bottom = next((y for y in range(height - 1, height // 2, -1) if row_dark[y] > 0.5), height)
    pad = 4
    return crop.crop((min(left + pad, right - 1), min(top + pad, bottom - 1),
                      max(right - pad, left + 1), max(bottom - pad, top + 1)))


def _value_cell_box(top, bottom):
    middle = (top + bottom) // 2
    left, right = LOGAN_VALUE_COLUMN
    return (left, middle - 36, right, middle + 36)


def _value_row_candidates(image, top, bottom):
    """Every distinct number read from one row of the value column, trying
    a few crops: the cell with its grid lines trimmed off, the same
    enlarged, and plain crops. Different crops misread different cells, so
    the caller keeps whichever reads make the bill add up."""
    middle = (top + bottom) // 2
    left, right = LOGAN_VALUE_COLUMN
    cell = _clean_cell(image.crop(_value_cell_box(top, bottom)))
    attempts = [cell, cell.resize((cell.width * 2, cell.height * 2), Image.LANCZOS)]
    attempts += [image.crop((left, middle - 30, right - trim, middle + 30)) for trim in (0, 15, 30)]
    found = []
    for crop in attempts:
        data = ocr_data(crop, ["--oem", "3", "--psm", "7", "-c", f"tessedit_char_whitelist={VALUE_WHITELIST}"])
        values = _money("".join(str(w) for w in data["text"] if str(w).strip()))
        if values and values[-1] not in found:
            found.append(values[-1])
    return found


def _logan(image):
    read = AmountRead()
    total, total_box = locate_field(image, "amount", "logan")
    if total is None or total_box is None:
        return _fail("couldn't read Total Amount Due", read)
    _, bill_box = locate_field(image, "bill_date", "logan")
    if bill_box is None:
        return _fail("couldn't find the Bill Date row to read Past Due Amount", read)

    total_top, total_bottom = total_box[1], total_box[3]
    bill_top, bill_bottom = bill_box[1], bill_box[3]
    current_rows = (total_top - LOGAN_ROW_PITCH, total_bottom - LOGAN_ROW_PITCH)
    past_rows = (bill_top - LOGAN_ROW_PITCH, bill_bottom - LOGAN_ROW_PITCH)
    read.boxes = {
        "Current Charges": _value_cell_box(*current_rows),
        "Total Amount Due": _value_cell_box(total_top, total_bottom),
        "Past Due Amount": _value_cell_box(*past_rows),
    }
    read.box = read.boxes["Current Charges"]

    # Total: from its value cell (keeps the sign of a credit like "(44.83)")
    # and from the label line, either sign.
    totals = set(_value_row_candidates(image, total_top, total_bottom)) | {total, -total}
    currents = _value_row_candidates(image, *current_rows)
    past_dues = _value_row_candidates(image, *past_rows)
    if not currents:
        return _fail("couldn't read Current Charges", read)
    if not past_dues:
        return _fail("couldn't read Past Due Amount", read)

    matches = {(cur, past, t) for cur in currents for past in past_dues for t in totals
               if abs(past + cur - t) <= TOLERANCE}
    if not matches:
        return _fail(f"the bill's numbers don't add up as read (past due {past_dues[0]:.2f} + current "
                     f"{currents[0]:.2f} != total {total:.2f}) - probably a misread", read)
    if len({m[0] for m in matches}) > 1:
        return _fail("Current Charges read more than one way", read)
    current, past, t = sorted(matches)[0]
    if _check_range(current):
        return _fail(_check_range(current), read)
    read.amount = current
    read.check = f"past due {past:.2f} + current {current:.2f} = total {t:.2f}"
    return read
