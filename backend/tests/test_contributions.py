"""contributions.py: TSP/IRA/taxable contribution modeling, the FERS agency
match formula, and cap projection/catch-up."""

from datetime import date

import pytest

from app.assumptions.contributions import (
    compute_contributions_for_year,
    elective_deferral_cap,
    fers_match_pct,
    get_contribution_profile,
    get_current_contribution_caps,
    ira_cap,
    project_cap,
    set_contribution_profile,
)
from app.db import get_connection, init_db


def make_conn(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return conn


# --- FERS match ---------------------------------------------------------------

def test_match_is_1pct_automatic_even_at_zero_employee_pct():
    assert fers_match_pct(0.0) == pytest.approx(0.01)


def test_match_dollar_for_dollar_up_to_3pct():
    assert fers_match_pct(0.02) == pytest.approx(0.01 + 0.02)
    assert fers_match_pct(0.03) == pytest.approx(0.04)


def test_match_half_rate_from_3_to_5pct():
    assert fers_match_pct(0.04) == pytest.approx(0.01 + 0.03 + 0.005)
    assert fers_match_pct(0.05) == pytest.approx(0.05)


def test_match_caps_at_5pct_beyond_5pct_employee_contribution():
    assert fers_match_pct(0.10) == pytest.approx(0.05)


# --- cap projection -------------------------------------------------------------

def test_project_cap_flat_until_growth_crosses_an_increment():
    # 23500 * 1.02 = 23970 -> floors to 23500 (no jump yet)
    assert project_cap(23500, 2026, 2027, 0.02, 500) == 23500
    # Several years later growth has crossed into the next $500 step.
    assert project_cap(23500, 2026, 2030, 0.02, 500) == pytest.approx(
        __import__("math").floor(23500 * 1.02 ** 4 / 500) * 500
    )


def test_project_cap_same_year_is_a_noop():
    assert project_cap(23500, 2026, 2026, 0.02, 500) == 23500


def test_elective_deferral_cap_adds_super_catchup_at_60_63(tmp_path):
    conn = make_conn(tmp_path)
    caps = get_current_contribution_caps(conn)
    base_year = date.fromisoformat(caps["effective_date"]).year

    no_catchup = elective_deferral_cap(caps, base_year, base_year, "1960-01-01", include_catchup=False)
    with_super = elective_deferral_cap(caps, base_year, base_year, "1965-01-01", include_catchup=True)
    # age 60 in base_year (born 1965, base_year likely ~2026) gets the super catch-up
    assert with_super > no_catchup


def test_elective_deferral_cap_no_catchup_under_50(tmp_path):
    conn = make_conn(tmp_path)
    caps = get_current_contribution_caps(conn)
    base_year = date.fromisoformat(caps["effective_date"]).year
    dob_age_30 = f"{base_year - 30}-01-01"
    assert elective_deferral_cap(caps, base_year, base_year, dob_age_30, include_catchup=True) == caps[
        "elective_deferral_limit"
    ]


def test_ira_cap_adds_catchup_at_50(tmp_path):
    conn = make_conn(tmp_path)
    caps = get_current_contribution_caps(conn)
    base_year = date.fromisoformat(caps["effective_date"]).year
    dob_age_55 = f"{base_year - 55}-01-01"
    dob_age_30 = f"{base_year - 30}-01-01"

    with_catchup = ira_cap(caps, base_year, base_year, dob_age_55, include_catchup=True)
    without = ira_cap(caps, base_year, base_year, dob_age_30, include_catchup=True)
    assert with_catchup == without + caps["ira_catchup_50"]


# --- compute_contributions_for_year ---------------------------------------------

def test_contributions_split_traditional_and_roth(tmp_path):
    conn = make_conn(tmp_path)
    set_contribution_profile(conn, "self", tsp_traditional_pct=0.05, tsp_roth_pct=0.05)
    profile = get_contribution_profile(conn, "self")
    caps = get_current_contribution_caps(conn)
    base_year = date.fromisoformat(caps["effective_date"]).year

    result = compute_contributions_for_year(
        profile, caps, salary=100_000, date_of_birth=f"{base_year - 30}-01-01",
        year=base_year, cap_base_year=base_year, include_catchup=False,
    )
    assert result["tsp_traditional"] == pytest.approx(5_000)
    assert result["tsp_roth"] == pytest.approx(5_000)
    assert result["agency_match"] == pytest.approx(100_000 * 0.05)  # 10% employee -> full 5% match


def test_contributions_scaled_down_when_over_the_cap(tmp_path):
    conn = make_conn(tmp_path)
    set_contribution_profile(conn, "self", tsp_traditional_pct=0.5, tsp_roth_pct=0.5)
    profile = get_contribution_profile(conn, "self")
    caps = get_current_contribution_caps(conn)
    base_year = date.fromisoformat(caps["effective_date"]).year

    # 50%+50% of a $300k salary blows way past the elective deferral cap.
    result = compute_contributions_for_year(
        profile, caps, salary=300_000, date_of_birth=f"{base_year - 30}-01-01",
        year=base_year, cap_base_year=base_year, include_catchup=False,
    )
    assert result["tsp_traditional"] + result["tsp_roth"] == pytest.approx(result["tsp_cap_used"])
    assert result["tsp_traditional"] == pytest.approx(result["tsp_roth"])  # scaled proportionally
    # Match is on the elected percentage, not the (capped) actual deposit.
    assert result["agency_match"] == pytest.approx(300_000 * 0.05)


def test_ira_contributions_scaled_down_when_over_the_cap(tmp_path):
    conn = make_conn(tmp_path)
    set_contribution_profile(conn, "self", ira_traditional_annual=10_000, ira_roth_annual=10_000)
    profile = get_contribution_profile(conn, "self")
    caps = get_current_contribution_caps(conn)
    base_year = date.fromisoformat(caps["effective_date"]).year

    result = compute_contributions_for_year(
        profile, caps, salary=100_000, date_of_birth=f"{base_year - 30}-01-01",
        year=base_year, cap_base_year=base_year, include_catchup=False,
    )
    assert result["ira_traditional"] + result["ira_roth"] == pytest.approx(result["ira_cap_used"])


def test_tsp_max_mode_splits_by_explicit_roth_share(tmp_path):
    conn = make_conn(tmp_path)
    set_contribution_profile(conn, "self", tsp_max=True, tsp_roth_share=0.5)
    profile = get_contribution_profile(conn, "self")
    caps = get_current_contribution_caps(conn)
    base_year = date.fromisoformat(caps["effective_date"]).year

    result = compute_contributions_for_year(
        profile, caps, salary=50_000, date_of_birth=f"{base_year - 30}-01-01",
        year=base_year, cap_base_year=base_year, include_catchup=False,
    )
    assert result["tsp_traditional"] == pytest.approx(result["tsp_roth"])
    assert result["tsp_traditional"] + result["tsp_roth"] == pytest.approx(caps["elective_deferral_limit"])


def test_tsp_max_mode_all_roth_ignores_the_now_irrelevant_pct_fields(tmp_path):
    # Regression: maxing out used to infer the split from tsp_traditional_pct/
    # tsp_roth_pct, so leaving both at 0 (as someone with only a Roth TSP
    # naturally would) silently fell back to a 50/50 split. roth_share=1.0
    # must give 100% Roth regardless of what's in those two fields.
    conn = make_conn(tmp_path)
    set_contribution_profile(conn, "self", tsp_traditional_pct=0, tsp_roth_pct=0,
                              tsp_max=True, tsp_roth_share=1.0)
    profile = get_contribution_profile(conn, "self")
    caps = get_current_contribution_caps(conn)
    base_year = date.fromisoformat(caps["effective_date"]).year

    result = compute_contributions_for_year(
        profile, caps, salary=50_000, date_of_birth=f"{base_year - 30}-01-01",
        year=base_year, cap_base_year=base_year, include_catchup=False,
    )
    assert result["tsp_traditional"] == 0
    assert result["tsp_roth"] == pytest.approx(caps["elective_deferral_limit"])


def test_tsp_max_mode_is_zero_with_no_salary(tmp_path):
    # TSP is a payroll deduction -- maxing out means nothing without wages.
    conn = make_conn(tmp_path)
    set_contribution_profile(conn, "self", tsp_max=True, tsp_roth_share=1.0)
    profile = get_contribution_profile(conn, "self")
    caps = get_current_contribution_caps(conn)
    base_year = date.fromisoformat(caps["effective_date"]).year

    result = compute_contributions_for_year(
        profile, caps, salary=0, date_of_birth=f"{base_year - 30}-01-01",
        year=base_year, cap_base_year=base_year, include_catchup=False,
    )
    assert result["tsp_traditional"] == 0
    assert result["tsp_roth"] == 0
    assert result["agency_match"] == 0


def test_ira_max_mode_all_roth_works_with_zero_salary(tmp_path):
    # The motivating case: a non-earning spouse's Roth IRA (funded by the
    # other spouse's income) -- IRA contributions aren't tied to this
    # person's own wages at all, unlike TSP.
    conn = make_conn(tmp_path)
    set_contribution_profile(conn, "self", ira_max=True, ira_roth_share=1.0)
    profile = get_contribution_profile(conn, "self")
    caps = get_current_contribution_caps(conn)
    base_year = date.fromisoformat(caps["effective_date"]).year

    result = compute_contributions_for_year(
        profile, caps, salary=0, date_of_birth=f"{base_year - 30}-01-01",
        year=base_year, cap_base_year=base_year, include_catchup=False,
    )
    assert result["ira_traditional"] == 0
    assert result["ira_roth"] == pytest.approx(caps["ira_limit"])


def test_ira_max_mode_splits_by_explicit_roth_share(tmp_path):
    conn = make_conn(tmp_path)
    set_contribution_profile(conn, "self", ira_max=True, ira_roth_share=0.25)
    profile = get_contribution_profile(conn, "self")
    caps = get_current_contribution_caps(conn)
    base_year = date.fromisoformat(caps["effective_date"]).year

    result = compute_contributions_for_year(
        profile, caps, salary=50_000, date_of_birth=f"{base_year - 30}-01-01",
        year=base_year, cap_base_year=base_year, include_catchup=False,
    )
    assert result["ira_roth"] == pytest.approx(caps["ira_limit"] * 0.25)
    assert result["ira_traditional"] == pytest.approx(caps["ira_limit"] * 0.75)


def test_tsp_max_mode_tracks_cap_growth_across_years(tmp_path):
    conn = make_conn(tmp_path)
    set_contribution_profile(conn, "self", tsp_max=True)
    profile = get_contribution_profile(conn, "self")
    caps = get_current_contribution_caps(conn)
    base_year = date.fromisoformat(caps["effective_date"]).year

    this_year_total = sum(compute_contributions_for_year(
        profile, caps, salary=50_000, date_of_birth=f"{base_year - 30}-01-01",
        year=base_year, cap_base_year=base_year, include_catchup=False,
    )[k] for k in ("tsp_traditional", "tsp_roth"))
    later_total = sum(compute_contributions_for_year(
        profile, caps, salary=50_000, date_of_birth=f"{base_year - 30}-01-01",
        year=base_year + 10, cap_base_year=base_year, include_catchup=False,
    )[k] for k in ("tsp_traditional", "tsp_roth"))
    assert later_total > this_year_total  # cap grew, so the maxed-out amount grew with it


def test_taxable_contribution_has_no_cap(tmp_path):
    conn = make_conn(tmp_path)
    set_contribution_profile(conn, "self", taxable_annual=50_000)
    profile = get_contribution_profile(conn, "self")
    caps = get_current_contribution_caps(conn)
    base_year = date.fromisoformat(caps["effective_date"]).year

    result = compute_contributions_for_year(
        profile, caps, salary=100_000, date_of_birth=f"{base_year - 30}-01-01",
        year=base_year, cap_base_year=base_year, include_catchup=False,
    )
    assert result["taxable"] == 50_000
