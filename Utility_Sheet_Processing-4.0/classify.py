"""
Figures out which company a page belongs to. Still the color-average
approach from V3 - the README flags this as fragile (scan brightness/
contrast can misclassify a page) and worth replacing with a text-based
check eventually, but that's a separate task from getting V4's structure
in place.
"""

import numpy as np

ENBRIDGE_RANGE = ((239, 239, 239), (249, 249, 249))
LOGAN_RANGE = ((222, 226, 231), (232, 236, 241))


def _in_range(avg, low, high):
    return all(low[i] <= avg[i] <= high[i] for i in range(3))


def classify(image):
    """Returns 'enbridge', 'logan', or None if the page's background color
    doesn't match either company."""
    avg = tuple(int(x) for x in np.array(image).mean(axis=(0, 1)))
    if _in_range(avg, *ENBRIDGE_RANGE):
        return "enbridge"
    if _in_range(avg, *LOGAN_RANGE):
        return "logan"
    return None
