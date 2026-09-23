"""
The OCR engine: given a page image, a field name, and a company, returns
that field's value. Doesn't know about PDFs, Excel, or the batch loop -
just image in, value out. All per-field/per-company specifics (crop box,
search term, invert flag) live in fields.yaml, not here.
"""

import re
import string
from datetime import datetime
from pathlib import Path

import yaml
from pytesseract import pytesseract

FIELDS_PATH = Path(__file__).parent / "fields.yaml"
with open(FIELDS_PATH) as f:
    FIELDS = yaml.safe_load(f)

MAX_RETRIES = 5
# Includes a space on purpose - without one, tesseract can't emit a space
# between words at all, so a multi-word search term like "Service Address"
# gets OCR'd as one merged word ("ServiceAddress") and can never match.
# Confirmed against a real bill: the very first, correctly-positioned crop
# was failing purely because of this, before any retry/widening even ran.
BASE_WHITELIST = "0123456789$.ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz "


def get_field_config(field_name, company):
    return FIELDS[field_name][company]


def extract_field(image, field_name, company):
    """Crop the page near the field's search term, OCR it, and pull out the
    value. Widens the crop and loosens OCR settings on each retry, same as
    V3 - real bill scans aren't always cropped tight enough on the first try."""
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

        config = _tesseract_config(attempt, cfg)
        data = pytesseract.image_to_data(
            cropped, lang="eng", config=config, output_type=pytesseract.Output.DICT
        )

        line_text = _find_matching_line(data, cfg["term"])
        if line_text:
            value = _extract_value(field_name, line_text, company)
            if value is not None:
                return value

        size_offset += add_offset
        add_offset += 25

    return None


def _tesseract_config(attempt, cfg):
    """Loosen whitelist/psm as attempts climb, unless the field's config
    specifies its own fixed whitelist (e.g. date fields need '/', which
    never appears in the generic ramp) - in that case, use it every attempt
    instead of ramping. Invert and psm-override always come from cfg."""
    if "whitelist" in cfg:
        whitelist, psm = cfg["whitelist"], cfg.get("psm", 6)
    elif attempt == 1:
        whitelist, psm = BASE_WHITELIST, cfg.get("psm", 6)
    elif attempt == 2:
        whitelist, psm = BASE_WHITELIST + "-,", cfg.get("psm", 6)
    elif attempt == 3:
        whitelist, psm = BASE_WHITELIST + "-, ", cfg.get("psm", 6)
    else:
        whitelist, psm = "", 11

    invert = 1 if cfg.get("invert") else 0
    extra = cfg.get("extra_config", "")
    return (
        f"--oem 3 --psm {psm} "
        f"-c preserve_interword_spaces=1 "
        f'-c tessedit_char_whitelist="{whitelist}" '
        f"-c tessedit_do_invert={invert} "
        f"{extra}"
    ).strip()


def _find_matching_line(ocr_data, term):
    """Scan OCR'd lines for the search term (word-boundary match, so 'Date'
    doesn't false-match inside 'BillDate') and return the matching line's text."""
    num_words = len(ocr_data["text"])
    lines_seen = set()
    pattern = r"\b" + re.escape(term.lower()) + r"\b"

    for i in range(num_words):
        line_num = ocr_data["line_num"][i]
        if line_num in lines_seen:
            continue
        lines_seen.add(line_num)

        line_words = [
            ocr_data["text"][j].strip()
            for j in range(num_words)
            if ocr_data["line_num"][j] == line_num and ocr_data["text"][j].strip()
        ]
        line_text = " ".join(line_words)

        if re.search(pattern, line_text.lower()):
            return line_text
    return None


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

def _parse_amount_enbridge(line_text, company):
    after_dollar = line_text.split("$", 1)
    if len(after_dollar) < 2:
        return None
    digits = "".join(c for c in after_dollar[1][:7] if c.isdigit() or c in ".,")
    return _to_float(digits)


def _parse_amount_logan(line_text, company):
    term_index = line_text.lower().find("total")
    if term_index == -1:
        return None
    digits = "".join(c for c in line_text[term_index:] if c.isdigit() or c in ".,")[:7]
    return _to_float(digits)


def _to_float(digits):
    cleaned = digits.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_amount(line_text, company):
    if company == "enbridge":
        return _parse_amount_enbridge(line_text, company)
    return _parse_amount_logan(line_text, company)


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
