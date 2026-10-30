"""
The OCR engine: given a page image, a field name, and a company, returns
that field's value. Doesn't know about PDFs, Excel, or the batch loop -
just image in, value out. All per-field/per-company specifics (crop box,
search term, invert flag) live in fields.yaml, not here.
"""

import os
import re
import string
import subprocess
import sys
import tempfile
from datetime import datetime

import yaml
from pytesseract import pytesseract

from app_paths import override

# A fields.yaml placed next to the .exe overrides the bundled one, so a
# crop box can be adjusted without rebuilding the app.
FIELDS_PATH = override("fields.yaml")
with open(FIELDS_PATH, encoding="utf-8") as f:
    FIELDS = yaml.safe_load(f)

MAX_RETRIES = 5
# Includes a space on purpose - without one, tesseract can't emit a space
# between words at all, so a multi-word search term like "Service Address"
# gets OCR'd as one merged word ("ServiceAddress") and can never match.
# Confirmed against a real bill: the very first, correctly-positioned crop
# was failing purely because of this, before any retry/widening even ran.
BASE_WHITELIST = "0123456789$.,ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz "


def get_field_config(field_name, company):
    return FIELDS[field_name][company]


def extract_field(image, field_name, company):
    """Crop the page near the field's search term, OCR it, and pull out the
    value. Widens the crop and loosens OCR settings on each retry, same as
    V3 - real bill scans aren't always cropped tight enough on the first try."""
    return locate_field(image, field_name, company)[0]


def field_search_box(field_name, company):
    """The area of the page a field is looked for in (left, top, right,
    bottom, 300 DPI pixels) - shown to the reviewer when a value wasn't
    found, so they can see where the app looked."""
    return tuple(get_field_config(field_name, company)["box"])


def locate_field(image, field_name, company):
    """Same as extract_field, but returns (value, box): box is where on the
    page the line holding the value was found, as (left, top, right,
    bottom) in 300 DPI pixels, or None if the value wasn't found or its
    position isn't known."""
    cfg = get_field_config(field_name, company)
    left, top, right, bottom = cfg["box"]
    size_offset = 0
    add_offset = 25

    for attempt in range(1, MAX_RETRIES + 1):
        width, height = image.size
        crop_box = (
            max(0, left - size_offset),
            max(0, top - size_offset),
            min(width, right + size_offset),
            min(height, bottom + size_offset),
        )
        cropped = image.crop(crop_box)

        data = ocr_data(cropped, _tesseract_args(attempt, cfg))

        # Try every line that has the term, not just the first - if the
        # first one doesn't have a usable value, a later one might.
        for line_text, box in _matching_lines_with_position(data, cfg["term"]):
            value = _extract_value(field_name, line_text, company)
            if value is not None:
                return value, offset_box(box, crop_box)

        # Some values are distinctive enough to trust without their label
        # (a 'd/d/yyyy - d/d/yyyy' range only shows up as the service
        # period). For those, if the label itself didn't OCR - e.g. a dark
        # shaded label cell - accept the value from anywhere in the crop.
        if cfg.get("term_optional"):
            all_text = " ".join(w.strip() for w in data["text"] if w.strip())
            value = _extract_value(field_name, all_text, company)
            if value is not None:
                return value, crop_box  # found somewhere in this area

        size_offset += add_offset
        add_offset += 25

    return None, None


def _tesseract_args(attempt, cfg):
    """Loosen whitelist/psm as attempts climb, unless the field's config
    specifies its own fixed whitelist (e.g. date fields need '/', which
    never appears in the generic ramp) - in that case, use it every attempt
    instead of ramping. Invert and psm-override always come from cfg.

    Returned as a list of separate arguments, not one config string - see
    ocr_data() for why."""
    if "whitelist" in cfg:
        whitelist, psm = cfg["whitelist"], cfg.get("psm", 6)
    elif attempt == 1:
        whitelist, psm = BASE_WHITELIST, cfg.get("psm", 6)
    elif attempt == 2:
        whitelist, psm = BASE_WHITELIST + "-", cfg.get("psm", 6)
    elif attempt == 3:
        whitelist, psm = BASE_WHITELIST + "-/", cfg.get("psm", 6)
    else:
        whitelist, psm = "", 11  # empty whitelist = no restriction

    invert = 1 if cfg.get("invert") else 0
    return [
        "--oem", "3",
        "--psm", str(psm),
        "-c", "preserve_interword_spaces=1",
        "-c", f"tessedit_char_whitelist={whitelist}",
        "-c", f"tessedit_do_invert={invert}",
        *cfg.get("extra_config", "").split(),
    ]


def ocr_data(image, args):
    """Run tesseract on an image and return its word-by-word results as a
    dict of lists (text, block_num, par_num, line_num, ...), the same shape
    as pytesseract's image_to_data(..., output_type=DICT).

    This calls tesseract directly instead of going through
    pytesseract.image_to_data, because pytesseract takes its options as one
    string and splits it with shlex - and on Windows it splits with
    different quoting rules, so a whitelist containing a space (which ours
    needs) breaks with "No closing quotation". Passing each argument
    separately avoids any splitting, on every OS."""
    fd, image_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        image.save(image_path, "PNG")
        cmd = [pytesseract.tesseract_cmd, image_path, "stdout", "-l", "eng", *args, "tsv"]
        extra = {}
        if sys.platform == "win32":
            extra["creationflags"] = subprocess.CREATE_NO_WINDOW  # no console flashing up per call
        proc = subprocess.run(cmd, capture_output=True, timeout=120, **extra)
    finally:
        try:
            os.remove(image_path)
        except OSError:
            pass
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"tesseract failed ({proc.returncode}): {message[-300:]}")
    return _parse_tsv(proc.stdout.decode("utf-8", errors="replace"))


def _parse_tsv(tsv):
    lines = tsv.splitlines()
    if not lines:
        return {"text": [], "block_num": [], "par_num": [], "line_num": []}
    header = lines[0].split("\t")
    data = {name: [] for name in header}
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < len(header) - 1:
            continue
        parts += [""] * (len(header) - len(parts))  # a word with no text drops the last column
        for name, value in zip(header, parts):
            data[name].append(int(value) if name != "text" and value.lstrip("-").isdigit() else value)
    return data


def _term_pattern(term):
    r"""Word-boundary match for the term, so 'Date' doesn't false-match inside
    'BillDate'. \b only goes on an edge that's a word character - a term
    like '$' has no word characters, and '\b\$\b' can never match
    '... Due $45.67' because there's no word boundary between a space and '$'."""
    term = term.lower()
    start = r"\b" if re.match(r"\w", term[0]) else ""
    end = r"\b" if re.match(r"\w", term[-1]) else ""
    return start + re.escape(term) + end


def _find_matching_lines(ocr_data, term):
    """Return the text of every OCR'd line containing the search term, in
    reading order."""
    return [text for text, _ in _matching_lines_with_position(ocr_data, term)]


def _matching_lines_with_position(ocr_data, term):
    """(text, box) for every OCR'd line containing the search term, in
    reading order, with box = (left, top, right, bottom) within the OCR'd
    image. Lines are keyed on (block, paragraph, line) - tesseract restarts
    line_num inside every block, so line_num on its own merges unrelated
    lines from different parts of the crop into one."""
    pattern = _term_pattern(term)
    return [line for line in ocr_lines(ocr_data) if re.search(pattern, line[0].lower())]


def ocr_lines(ocr_data):
    """Group tesseract's word results into lines: [(text, box), ...] with
    box = (left, top, right, bottom) in the OCR'd image's pixels, or None
    if tesseract didn't report positions."""
    lines = {}
    for i, word in enumerate(ocr_data["text"]):
        word = str(word).strip()
        if not word:
            continue
        key = (ocr_data["block_num"][i], ocr_data["par_num"][i], ocr_data["line_num"][i])
        entry = lines.setdefault(key, {"words": [], "box": None})
        entry["words"].append(word)
        if all(k in ocr_data for k in ("left", "top", "width", "height")):
            left, top = ocr_data["left"][i], ocr_data["top"][i]
            right, bottom = left + ocr_data["width"][i], top + ocr_data["height"][i]
            b = entry["box"]
            entry["box"] = (left, top, right, bottom) if b is None else (
                min(b[0], left), min(b[1], top), max(b[2], right), max(b[3], bottom))
    return [(" ".join(e["words"]), e["box"]) for e in lines.values()]


def offset_box(box, crop_box):
    """A box found inside a crop -> the same box in page coordinates."""
    if box is None:
        return None
    return (box[0] + crop_box[0], box[1] + crop_box[1], box[2] + crop_box[0], box[3] + crop_box[1])


def _extract_value(field_name, line_text, company):
    """Route to a field's custom parser if it has one, otherwise pull the
    value out with its regex pattern."""
    field = FIELDS[field_name]

    if "parser" in field:
        return PARSERS[field["parser"]](line_text, company)

    match = re.search(field["pattern"], line_text)
    if not match:
        return None
    candidate = match.group(0)

    validator = VALIDATORS.get(field.get("validate"))
    if validator and not validator(candidate):
        return None
    return candidate


# --- validators -------------------------------------------------------

def validate_date(candidate):
    try:
        datetime.strptime(candidate, "%m/%d/%Y")
        return True
    except ValueError:
        return False


VALIDATORS = {"date": validate_date}


# --- custom field parsers ----------------------------------------------
# amount and service_address can't be pulled out with a plain regex - each
# company formats them differently enough that they need real logic.

# A price: digits (optionally with thousands commas) and exactly two
# decimal places, e.g. 206.14 or 1,234.56. Not preceded by another digit,
# comma or period, and not followed by a third decimal digit, so pieces of
# longer numbers (account numbers, dates, 206.145) don't count.
PRICE_RE = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})(?!\d)")

# Anything outside this range is treated as a misread, not a real bill.
# $0.00 is rejected on purpose - V3's silent "$0.00 on failure" problem
# means a zero should always get a human look.
MIN_AMOUNT = 0.01
MAX_AMOUNT = 5000.00


def _first_price(text):
    """Return the first valid price in text as a float, or None."""
    for match in PRICE_RE.finditer(text):
        value = float(match.group(1).replace(",", "") + "." + match.group(2))
        if MIN_AMOUNT <= value <= MAX_AMOUNT:
            return value
        return None  # the first price-shaped thing is out of range - don't go hunting for another
    return None


def parse_amount(line_text, company):
    """Read the amount from the text after the label: '$' on Enbridge
    bills, 'Total Amount Due' on Logan bills. Only accepts something
    shaped like a price, read up to its second decimal place."""
    if company == "enbridge":
        parts = line_text.split("$", 1)
        if len(parts) < 2:
            return None
        return _first_price(parts[1])

    term_index = line_text.lower().find("total")
    if term_index == -1:
        return None
    return _first_price(line_text[term_index:])


def parse_address(line_text, company):
    """Pulls the digit-led address out of the OCR'd line (e.g. '230 W 100 N').
    Doesn't check it against known Excel tabs - that's excel_writer's job,
    since it's the one that knows what tabs exist."""
    new_string = ""
    recording = False
    for char in line_text:
        if char.isdigit() and not recording:
            recording = True
        if recording:
            if char in ",.":
                break
            new_string += char
    return new_string.strip() or None


PARSERS = {"amount": parse_amount, "address": parse_address}
