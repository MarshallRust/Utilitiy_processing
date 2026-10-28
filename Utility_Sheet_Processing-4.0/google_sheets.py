"""
Talks to the shared Google Sheet: signing in, reading tabs, writing cells.

Sign-in uses Google's normal "Sign in with Google" browser page. The first
time someone runs the app on a computer, a browser tab opens; after that
the sign-in is remembered (in %APPDATA%\\Catalyst Utility Processor) and
quietly refreshed. Edits show up in the sheet's version history under the
person who ran it.

The OAuth client (client_secret.json) belongs to Catalyst's Google
Workspace and is set to "Internal", which is what keeps the sign-in from
expiring every 7 days. See README for the one-time setup.
"""

import json
import re
import time

import requests
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app_paths import override, user_dir

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
API = "https://sheets.googleapis.com/v4/spreadsheets"
TOKEN_PATH = user_dir() / "google_token.json"
RETRIES = 4  # the office wifi drops out - retry before giving up


class SheetError(Exception):
    """Something went wrong talking to Google, with a message meant for
    whoever is sitting at the computer."""


# --- sign-in ------------------------------------------------------------

def _client_secret_path():
    path = override("client_secret.json")
    if not path.exists():
        raise SheetError(
            "This copy of the app is missing its Google sign-in file (client_secret.json). "
            "It should be built into the .exe - see the README's setup section."
        )
    return path


def load_saved_credentials():
    """The remembered sign-in, refreshed if needed, or None if there isn't a
    usable one (never signed in, or Google revoked it)."""
    if not TOKEN_PATH.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except (ValueError, json.JSONDecodeError):
        TOKEN_PATH.unlink(missing_ok=True)
        return None
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            # Revoked, password changed, or unused for 6 months - sign in again.
            TOKEN_PATH.unlink(missing_ok=True)
            return None
        except TransportError as e:
            raise SheetError("Couldn't reach Google to refresh the sign-in - check the internet connection.") from e
        _save(creds)
        return creds
    return None


def sign_in():
    """Open the browser sign-in page and wait for it to finish."""
    flow = InstalledAppFlow.from_client_secrets_file(str(_client_secret_path()), SCOPES)
    creds = flow.run_local_server(
        port=0,
        open_browser=True,
        authorization_prompt_message="",
        success_message="Signed in. You can close this tab and go back to the Utility Processor.",
    )
    _save(creds)
    return creds


def sign_out():
    TOKEN_PATH.unlink(missing_ok=True)


def _save(creds):
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")


def signed_in_email(creds):
    try:
        resp = AuthorizedSession(creds).get("https://openidconnect.googleapis.com/v1/userinfo", timeout=20)
        return resp.json().get("email") if resp.ok else None
    except requests.RequestException:
        return None


# --- the sheet ----------------------------------------------------------

def sheet_id_from_link(link):
    """Accepts a full Google Sheets link or a bare ID."""
    link = (link or "").strip()
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", link)
    if match:
        return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", link):
        return link
    raise SheetError("That doesn't look like a Google Sheets link. Copy it from the browser's address bar.")


class Sheet:
    def __init__(self, creds, sheet_id):
        self.session = AuthorizedSession(creds)
        self.sheet_id = sheet_id

    def _request(self, method, url, **kwargs):
        last_error = None
        for attempt in range(RETRIES):
            try:
                resp = self.session.request(method, url, timeout=60, **kwargs)
            except (requests.RequestException, TransportError) as e:
                last_error = e
            else:
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = SheetError(f"Google returned {resp.status_code}")
                elif resp.status_code == 403:
                    raise SheetError(
                        "Your Google account doesn't have edit access to this sheet. "
                        "Ask whoever owns it to share it with you as an Editor."
                    )
                elif resp.status_code == 404:
                    raise SheetError("Couldn't find that Google Sheet - check the link.")
                elif not resp.ok:
                    raise SheetError(f"Google returned an error ({resp.status_code}): {resp.text[:300]}")
                else:
                    return resp.json()
            time.sleep(2 ** attempt)
        raise SheetError(
            "Couldn't reach Google Sheets after several tries - check the internet connection and try again."
        ) from last_error

    def title_and_tabs(self):
        data = self._request(
            "GET", f"{API}/{self.sheet_id}",
            params={"fields": "properties.title,sheets.properties.title"},
        )
        return data["properties"]["title"], [s["properties"]["title"] for s in data.get("sheets", [])]

    def read_tabs(self, tabs, last_cell="Z80"):
        """{tab: grid} for each tab, as the values people see in the sheet."""
        tabs = list(dict.fromkeys(tabs))
        if not tabs:
            return {}
        ranges = [f"'{t.replace(chr(39), chr(39) * 2)}'!A1:{last_cell}" for t in tabs]
        data = self._request(
            "GET", f"{API}/{self.sheet_id}/values:batchGet",
            params={"ranges": ranges, "valueRenderOption": "FORMATTED_VALUE", "majorDimension": "ROWS"},
        )
        return {tab: vr.get("values", []) for tab, vr in zip(tabs, data.get("valueRanges", []))}

    def write_cells(self, cells):
        """cells: list of (a1_range, value). One request for the whole run.
        USER_ENTERED makes dates real dates and the amount a number, the
        same as typing them in."""
        if not cells:
            return
        self._request(
            "POST", f"{API}/{self.sheet_id}/values:batchUpdate",
            json={
                "valueInputOption": "USER_ENTERED",
                "data": [{"range": rng, "values": [[value]]} for rng, value in cells],
            },
        )
