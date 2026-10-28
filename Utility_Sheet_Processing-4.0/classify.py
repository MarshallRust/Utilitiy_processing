"""
Figures out which company a page belongs to.

First tries the quick check carried over from V3: the average color of
the page (Logan's bills are a slightly blue-gray overall, Enbridge's are a
neutral light gray). That's fast, but scanner brightness and contrast can
push a page outside the range. When it doesn't match, falls back to
reading the text across the top of the page and looking for the company's
name - slower (about a second) but doesn't care about color.
"""

import numpy as np
from pytesseract import pytesseract

ENBRIDGE_RANGE = ((239, 239, 239), (249, 249, 249))
LOGAN_RANGE = ((222, 226, 231), (232, 236, 241))

# Checked in this order. Enbridge goes first on purpose: every bill is
# mailed to Catalyst in Logan, so "Logan" shows up on Enbridge bills too.
# Enbridge Gas Utah used to be Dominion Energy - older bills may say that.
TEXT_MARKERS = [
    ("enbridge", ("enbridge", "dominion energy")),
    ("logan", ("logan city", "loganutah", "monthly utility bill", "city united in service")),
]


def _in_range(avg, low, high):
    return all(low[i] <= avg[i] <= high[i] for i in range(3))


def _by_color(image):
    avg = tuple(int(x) for x in np.array(image).mean(axis=(0, 1)))
    if _in_range(avg, *ENBRIDGE_RANGE):
        return "enbridge"
    if _in_range(avg, *LOGAN_RANGE):
        return "logan"
    return None


def _by_text(image):
    width, height = image.size
    top = image.crop((0, 0, width, int(height * 0.4)))
    top = top.resize((width // 2, int(height * 0.4) // 2))
    text = pytesseract.image_to_string(top, config="--oem 3 --psm 3").lower()
    text = " ".join(text.split())
    for company, markers in TEXT_MARKERS:
        if any(marker in text for marker in markers):
            return company
    return None


def classify(image):
    """Returns 'enbridge', 'logan', or None if the page doesn't look like
    either company's bill."""
    return _by_color(image) or _by_text(image)
