"""
Google Sheets read-only client for pulling account balances from the Tiller
"Accounts" tab.

OAuth design (per the build plan):
- Read-only scope only: no write access to the sheet is ever requested.
- The interactive consent step (opening a browser) happens once, outside the
  container, via scripts/authorize_tiller.py -- the long-running backend
  (which has no browser) only ever loads and silently refreshes the cached
  token from oauth/token_cache/token.json.
"""

import os
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# parents[3] assumes the local dev layout (repo_root/backend/app/tiller/...),
# which doesn't hold inside the Docker image (WORKDIR /app, code copied to
# ./app -- one directory level shallower). OAUTH_DIR env var lets
# docker-compose override it to the actual mount point (/app/oauth), same
# pattern as db.py's DB_PATH.
OAUTH_DIR = Path(os.environ.get("OAUTH_DIR", Path(__file__).resolve().parents[3] / "oauth"))
CLIENT_SECRET_FILE = OAUTH_DIR / "client_secret.json"
TOKEN_FILE = OAUTH_DIR / "token_cache" / "token.json"

ACCOUNTS_TAB = "accounts"

# Columns the mapping/ingestion logic depends on. If any of these disappear
# or get renamed in the sheet, fail loudly rather than silently reading
# garbage -- the account-mapping "warn on drift" requirement from the plan.
REQUIRED_COLUMNS = ["Account Id", "Account", "Last Balance", "Last Update", "Institution", "Type"]


class TillerAuthError(RuntimeError):
    """Raised when there is no valid cached OAuth token for the Sheets pull."""


class TillerSchemaError(RuntimeError):
    """Raised when the Accounts tab is missing an expected column."""


def load_credentials() -> Credentials:
    """
    Load and, if needed, silently refresh the cached OAuth token.

    Never opens a browser -- that only happens in scripts/authorize_tiller.py,
    run once interactively outside the container. If no valid cached token
    exists, raises TillerAuthError with instructions rather than trying to
    launch a consent flow with no browser available.
    """
    if not TOKEN_FILE.exists():
        raise TillerAuthError(
            f"No cached Google OAuth token at {TOKEN_FILE}. Run "
            "`python backend/scripts/authorize_tiller.py` once, interactively, "
            "to grant access."
        )

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise TillerAuthError(
                "Cached Google OAuth token could not be refreshed -- it may have "
                "been revoked. Re-run `python backend/scripts/authorize_tiller.py`."
            ) from e
        TOKEN_FILE.write_text(creds.to_json())
        return creds

    raise TillerAuthError(
        "Cached Google OAuth token is invalid and has no refresh token. "
        "Re-run `python backend/scripts/authorize_tiller.py`."
    )


def _validate_header(header_row: list[str]) -> None:
    missing = [col for col in REQUIRED_COLUMNS if col not in header_row]
    if missing:
        raise TillerSchemaError(
            f"Tiller '{ACCOUNTS_TAB}' tab is missing expected column(s): {missing}. "
            f"Found columns: {header_row}. The sheet layout may have changed -- "
            "check it before trusting any pulled balances."
        )


def fetch_accounts(spreadsheet_id: str) -> list[dict]:
    """
    Pull all rows from the Tiller Accounts tab as a list of dicts keyed by
    header name (not fixed column letters, since Tiller can reorder/add
    columns). Raises TillerSchemaError if an expected column is missing.
    """
    creds = load_credentials()
    service = build("sheets", "v4", credentials=creds)

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=ACCOUNTS_TAB)
        .execute()
    )
    rows = result.get("values", [])
    if not rows:
        raise TillerSchemaError(f"Tiller '{ACCOUNTS_TAB}' tab returned no rows at all.")

    header, *data_rows = rows
    _validate_header(header)

    accounts = []
    for row in data_rows:
        # Sheets omits trailing empty cells from a row -- pad so zip() doesn't
        # silently drop trailing columns.
        padded = row + [""] * (len(header) - len(row))
        accounts.append(dict(zip(header, padded)))
    return accounts
