"""
Run this once, interactively, to grant read-only access to your Tiller
Google Sheet. Writes the resulting token to oauth/token_cache/token.json.
The containerized backend only ever reads and silently refreshes that
cached file -- it never runs this flow itself.

This host is headless/SSH-only, so the local redirect server's port must be
forwarded to wherever your browser actually runs:

    1. In a separate terminal, SSH in with a local port forward:
           ssh -L 8089:localhost:8089 <user>@<this-host>
       (keep that session open; no commands needed in it)
    2. Run this script (here, or in that forwarded session -- either works):
           python backend/scripts/authorize_tiller.py
    3. Open the printed URL in your own browser, log in, approve.
    4. Google redirects your browser to http://localhost:8089/..., which the
       SSH tunnel carries back to this script's listener here.

Re-run this if sheets_client.TillerAuthError says the token was revoked or
can't be refreshed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/, for `app.*`

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from app.tiller.sheets_client import CLIENT_SECRET_FILE, SCOPES, TOKEN_FILE  # noqa: E402


def main():
    if not CLIENT_SECRET_FILE.exists():
        raise SystemExit(
            f"Missing {CLIENT_SECRET_FILE}. Download it from the Google Cloud "
            "Console (APIs & Services -> Clients) and place it there first."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    creds = flow.run_local_server(port=8089, open_browser=False)

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    print(f"Saved token to {TOKEN_FILE}")


if __name__ == "__main__":
    main()
