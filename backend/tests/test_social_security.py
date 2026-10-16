import pytest

from app.assumptions.social_security import (
    compute_social_security_benefit,
    get_social_security_profile,
    set_social_security_profile,
)
from app.db import get_connection, init_db


def make_conn(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return conn


def test_claiming_at_full_retirement_age_is_unadjusted():
    result = compute_social_security_benefit(pia_monthly=2000, full_retirement_age=67, claiming_age=67)
    assert result["adjustment_fraction"] == 0
    assert result["monthly_benefit"] == pytest.approx(2000)


def test_claiming_early_at_62_reduces_benefit():
    # 5 years early = 60 months: first 36 at 5/9%, remaining 24 at 5/12%.
    result = compute_social_security_benefit(pia_monthly=2000, full_retirement_age=67, claiming_age=62)
    expected_reduction = 36 * (5 / 9 / 100) + 24 * (5 / 12 / 100)
    assert result["adjustment_fraction"] == pytest.approx(-expected_reduction)
    assert result["monthly_benefit"] == pytest.approx(2000 * (1 - expected_reduction))


def test_claiming_one_year_early_only_uses_first_tier():
    result = compute_social_security_benefit(pia_monthly=2000, full_retirement_age=67, claiming_age=66)
    expected_reduction = 12 * (5 / 9 / 100)
    assert result["adjustment_fraction"] == pytest.approx(-expected_reduction)


def test_delaying_past_fra_increases_benefit():
    result = compute_social_security_benefit(pia_monthly=2000, full_retirement_age=67, claiming_age=70)
    expected_credit = 36 * (2 / 3 / 100)
    assert result["adjustment_fraction"] == pytest.approx(expected_credit)
    assert result["monthly_benefit"] == pytest.approx(2000 * (1 + expected_credit))


def test_claiming_age_is_capped_at_70():
    result = compute_social_security_benefit(pia_monthly=2000, full_retirement_age=67, claiming_age=75)
    capped = compute_social_security_benefit(pia_monthly=2000, full_retirement_age=67, claiming_age=70)
    assert result["monthly_benefit"] == pytest.approx(capped["monthly_benefit"])


def test_social_security_profile_upsert(tmp_path):
    conn = make_conn(tmp_path)
    set_social_security_profile(conn, owner="self", estimated_pia_monthly=2200, full_retirement_age=67)
    profile = get_social_security_profile(conn, "self")
    assert profile["estimated_pia_monthly"] == 2200

    set_social_security_profile(conn, owner="self", estimated_pia_monthly=2300, full_retirement_age=67)
    profile = get_social_security_profile(conn, "self")
    assert profile["estimated_pia_monthly"] == 2300
