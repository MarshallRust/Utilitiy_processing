"""
The few things the app remembers between runs, saved per user in
%APPDATA%\\Catalyst Utility Processor\\settings.json. Deleting that file
just puts everything back to the defaults below.
"""

import json

from app_paths import user_dir

# The shared billing sheet. Changeable from the window if it ever moves.
DEFAULT_SHEET_LINK = "https://docs.google.com/spreadsheets/d/150w2_R7j1SGkib58RT2v140pR2954MtPvMKULr1x1U8/edit"

SETTINGS_PATH = user_dir() / "settings.json"
DEFAULTS = {"sheet_link": DEFAULT_SHEET_LINK, "last_folder": ""}


def load():
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save(settings):
    try:
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError:
        pass
