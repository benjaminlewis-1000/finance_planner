"""
Verifies the viewer's Authelia session directly against auth.exploretheworld.tech,
rather than trusting that the Cloudflare Access policy in front of this app is
correctly configured. This matters because Cloudflare Access policies are
managed in an external dashboard, not in this repo -- a misconfiguration (or
one that silently detaches on an edit) would otherwise serve the dashboard to
anyone, with nothing in our own code catching it. Same pattern already
running in production for /home/benjamin/docker_via_tunnels/location_track
(see serve_flask_interface.py's authelia_required decorator): Authelia's
session cookie is scoped to the apex domain (exploretheworld.tech), so it's
already present in the browser for any subdomain once the user has logged in
once, anywhere.

Fails closed: no cookie, an invalid/expired cookie, or Authelia being
unreachable all result in access being denied, never silently allowed.
"""

import os

import requests
import streamlit as st

AUTHELIA_URL = os.environ.get("AUTHELIA_URL", "https://auth.exploretheworld.tech")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://financeforecast.exploretheworld.tech")
REQUIRE_AUTH = os.environ.get("REQUIRE_AUTH", "true").lower() != "false"

COOKIE_NAME = "authelia_session"


def check_authelia_cookie(cookie: str | None) -> tuple[bool, str]:
    """Pure decision function, deliberately separated from the Streamlit UI
    calls below -- this is the actual security-critical logic, and keeping
    it free of st.* calls means it can be unit-tested directly instead of
    needing a real browser/WebSocket session (which is what Streamlit
    requires to execute anything st.context-dependent)."""
    if not cookie:
        return False, "You need to log in first."

    try:
        response = requests.get(
            f"{AUTHELIA_URL}/api/verify",
            params={"rd": PUBLIC_URL},
            cookies={COOKIE_NAME: cookie},
            timeout=5,
            allow_redirects=False,
        )
    except requests.exceptions.RequestException:
        return False, "Could not reach Authelia to verify your session -- access denied."

    if response.status_code != 200:
        return False, "Your session has expired. Please log in again."

    return True, ""


def require_authelia_session() -> None:
    """Call once at the top of the app, before rendering anything else.
    Streamlit re-runs the whole script on every interaction, so this
    re-verifies on every single rerun -- not just once at page load."""
    if not REQUIRE_AUTH:
        return  # explicit local-dev opt-out only, never the default

    cookie = st.context.cookies.get(COOKIE_NAME)
    authorized, message = check_authelia_cookie(cookie)
    if not authorized:
        st.error(message)
        st.link_button("Log in via Authelia", f"{AUTHELIA_URL}/?rd={PUBLIC_URL}")
        st.stop()
