"""
Where things live, whether this is running as plain Python on a Mac or as
the packaged Windows .exe. Nothing else in the app should build a path by
hand - ask this module instead.

- resource(): files that ship inside the app (fields.yaml, the stamp, the
  bundled Tesseract). In the .exe these are unpacked to a temp folder.
- override(): lets someone drop an edited fields.yaml next to the .exe to
  fix a crop box without rebuilding the app.
- user_dir(): per-user settings, the Google sign-in token and run logs.
  %APPDATA%\\Catalyst Utility Processor on Windows,
  ~/Library/Application Support/Catalyst Utility Processor on a Mac.
"""

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "Catalyst Utility Processor"

FROZEN = getattr(sys, "frozen", False)  # True inside the PyInstaller .exe
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent


def resource(*parts) -> Path:
    """A file that ships with the app."""
    return BUNDLE_DIR.joinpath(*parts)


def override(name) -> Path:
    """A file next to the .exe takes priority over the bundled copy."""
    local = EXE_DIR / name
    return local if local.exists() else resource(name)


def user_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_tesseract():
    """Point pytesseract at a working tesseract. Inside the .exe that's the
    copy bundled under tesseract/. Running from source, it's whatever is on
    the PATH (brew install tesseract on a Mac). Returns the path used, or
    raises RuntimeError with a plain-English message if there isn't one."""
    from pytesseract import pytesseract

    bundled = resource("tesseract", "tesseract.exe" if sys.platform == "win32" else "tesseract")
    if bundled.exists():
        pytesseract.tesseract_cmd = str(bundled)
        os.environ["TESSDATA_PREFIX"] = str(resource("tesseract", "tessdata"))
        return str(bundled)

    on_path = shutil.which("tesseract")
    if on_path:
        pytesseract.tesseract_cmd = on_path
        return on_path

    raise RuntimeError(
        "Tesseract (the OCR engine) wasn't found. The packaged app includes it; "
        "if you're running from source, install it first (Mac: brew install tesseract)."
    )
