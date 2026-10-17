"""Unit tests for the security-critical decision logic in auth.py. Deliberately
doesn't touch require_authelia_session() itself -- that's a thin st.error/
st.stop() wrapper around check_authelia_cookie(), which is what actually
decides access and is worth pinning down with tests."""

from unittest.mock import patch

import pytest
import requests

from auth import check_authelia_cookie


def test_no_cookie_denies():
    authorized, message = check_authelia_cookie(None)
    assert authorized is False
    assert "log in" in message.lower()


def test_valid_cookie_is_authorized():
    with patch("auth.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        authorized, message = check_authelia_cookie("real-session-token")
    assert authorized is True
    assert message == ""


def test_expired_or_invalid_cookie_denies():
    with patch("auth.requests.get") as mock_get:
        mock_get.return_value.status_code = 401
        authorized, message = check_authelia_cookie("stale-token")
    assert authorized is False
    assert "expired" in message.lower()


def test_authelia_unreachable_fails_closed():
    """The failure mode that matters most: if Authelia can't be reached,
    we must deny, never silently allow through."""
    with patch("auth.requests.get", side_effect=requests.exceptions.ConnectionError):
        authorized, message = check_authelia_cookie("some-token")
    assert authorized is False
    assert "denied" in message.lower()


def test_verify_call_does_not_follow_redirects():
    """Mirrors the known location_track bug this pattern avoids: following
    Authelia's own redirect server-side causes unbounded 'rd' nesting."""
    with patch("auth.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        check_authelia_cookie("token")
    assert mock_get.call_args.kwargs["allow_redirects"] is False


def test_verify_call_sends_the_cookie_under_the_expected_name():
    with patch("auth.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        check_authelia_cookie("the-actual-token")
    assert mock_get.call_args.kwargs["cookies"] == {"authelia_session": "the-actual-token"}
