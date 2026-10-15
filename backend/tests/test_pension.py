"""Hand-computed FERS scenarios, per the build plan's verification section."""

from datetime import date, timedelta

import pytest

from app.assumptions.pension import compute_fers_pension

DEFAULT_PARAMS = {
    "multiplier_standard": 0.01,
    "multiplier_age62_20yos": 0.011,
    "mra_plus_10_reduction_per_year_under_62": 0.05,
}


def _years_ago(years: float, from_date: date) -> date:
    return from_date - timedelta(days=round(years * 365.25))


def test_mra_plus_10_reduced_case():
    # Separate at MRA (57) with 25 YOS: not 30+YOS, not 60+20, not 62+5 --
    # MRA+10 applies, reduced 5%/year for 5 years under 62.
    separation_date = date(2040, 1, 1)
    service_start_date = _years_ago(25, separation_date)
    high_3_salary = 120_000

    result = compute_fers_pension(
        service_start_date=service_start_date,
        separation_date=separation_date,
        separation_age=57,
        high_3_salary=high_3_salary,
        mra_age=57,
        params=DEFAULT_PARAMS,
    )

    assert result["years_of_service"] == pytest.approx(25, abs=0.01)
    assert result["enhanced_multiplier"] is False
    assert result["multiplier_used"] == pytest.approx(0.01)
    assert result["base_annual_pension"] == pytest.approx(0.01 * high_3_salary * 25, rel=1e-3)
    assert result["reduction_fraction"] == pytest.approx(0.25)
    assert result["annual_pension"] == pytest.approx(22_500, rel=1e-3)
    assert result["eligible_immediate"] is True
    assert "MRA+10" in result["eligibility_note"]


def test_age_62_with_20_years_unreduced_enhanced():
    # Separate at 62 with 20 YOS: satisfies 62+5 (unreduced) AND the
    # enhanced 1.1% multiplier (age 62+, 20+ YOS).
    separation_date = date(2045, 6, 1)
    service_start_date = _years_ago(20, separation_date)
    high_3_salary = 120_000

    result = compute_fers_pension(
        service_start_date=service_start_date,
        separation_date=separation_date,
        separation_age=62,
        high_3_salary=high_3_salary,
        mra_age=57,
        params=DEFAULT_PARAMS,
    )

    assert result["years_of_service"] == pytest.approx(20, abs=0.01)
    assert result["enhanced_multiplier"] is True
    assert result["multiplier_used"] == pytest.approx(0.011)
    assert result["reduction_fraction"] == 0
    assert result["annual_pension"] == pytest.approx(26_400, rel=1e-3)
    assert result["eligible_immediate"] is True
    assert "Unreduced" in result["eligibility_note"]


def test_age_60_with_20_years_unreduced_standard_multiplier():
    # Age 60 + 20 YOS is an unreduced path, but the enhanced multiplier
    # requires age 62+ -- so this stays at the standard 1% multiplier with
    # no reduction, distinguishing "unreduced" from "enhanced".
    separation_date = date(2042, 3, 1)
    service_start_date = _years_ago(20, separation_date)
    high_3_salary = 100_000

    result = compute_fers_pension(
        service_start_date=service_start_date,
        separation_date=separation_date,
        separation_age=60,
        high_3_salary=high_3_salary,
        mra_age=57,
        params=DEFAULT_PARAMS,
    )

    assert result["enhanced_multiplier"] is False
    assert result["multiplier_used"] == pytest.approx(0.01)
    assert result["reduction_fraction"] == 0
    assert result["annual_pension"] == pytest.approx(0.01 * high_3_salary * 20, rel=1e-3)
    assert result["eligible_immediate"] is True
    assert "Unreduced" in result["eligibility_note"]


def test_not_yet_eligible_for_immediate_annuity():
    # MRA (57) but only 8 YOS: below the 10-YOS floor for MRA+10, and none
    # of the unreduced paths apply either -- not modeled as immediate.
    separation_date = date(2038, 1, 1)
    service_start_date = _years_ago(8, separation_date)

    result = compute_fers_pension(
        service_start_date=service_start_date,
        separation_date=separation_date,
        separation_age=57,
        high_3_salary=110_000,
        mra_age=57,
        params=DEFAULT_PARAMS,
    )

    assert result["eligible_immediate"] is False
    assert "deferred" in result["eligibility_note"].lower()


def test_editable_params_change_the_result():
    # If the law changes, a different reduction rate should flow straight
    # through -- this is the whole point of storing params instead of
    # hardcoding them.
    separation_date = date(2040, 1, 1)
    service_start_date = _years_ago(25, separation_date)
    high_3_salary = 120_000

    custom_params = {**DEFAULT_PARAMS, "mra_plus_10_reduction_per_year_under_62": 0.03}
    result = compute_fers_pension(
        service_start_date=service_start_date,
        separation_date=separation_date,
        separation_age=57,
        high_3_salary=high_3_salary,
        mra_age=57,
        params=custom_params,
    )

    assert result["reduction_fraction"] == pytest.approx(0.15)
    assert result["annual_pension"] == pytest.approx(30_000 * 0.85, rel=1e-3)
