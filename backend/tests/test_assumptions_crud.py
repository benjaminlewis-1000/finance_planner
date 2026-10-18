from datetime import date

import pytest

from app.assumptions import crud, household
from app.assumptions.pension import (
    delete_pension_profile,
    get_current_pension_params,
    get_pension_profile,
    set_pension_params,
    set_pension_profile,
)
from app.db import get_connection, init_db


def make_conn(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return conn


def test_salary_steps_crud(tmp_path):
    conn = make_conn(tmp_path)
    step_id = crud.add_salary_step(conn, owner="self", effective_date="2026-01-01",
                                    annual_amount=140_000, note="raise")
    rows = crud.list_salary_steps(conn, owner="self")
    assert len(rows) == 1
    assert rows[0]["annual_amount"] == 140_000

    crud.delete_salary_step(conn, step_id)
    assert crud.list_salary_steps(conn, owner="self") == []


def test_expense_changes_and_events_crud(tmp_path):
    conn = make_conn(tmp_path)
    crud.add_expense_change(conn, effective_date="2027-01-01", category="childcare",
                             annual_amount_delta=-12_000, note="kid starts school")
    rows = crud.list_expense_changes(conn)
    assert len(rows) == 1
    assert rows[0]["annual_amount_delta"] == -12_000

    event_id = crud.add_one_off_event(conn, event_date="2030-06-01", description="home purchase",
                                       amount=-80_000, category="real_estate", owner="joint")
    events = crud.list_one_off_events(conn)
    assert len(events) == 1
    assert events[0]["id"] == event_id

    crud.delete_one_off_event(conn, event_id)
    assert crud.list_one_off_events(conn) == []


def test_pension_params_default_and_versioning(tmp_path):
    conn = make_conn(tmp_path)
    params = get_current_pension_params(conn)
    assert params["multiplier_standard"] == 0.01
    assert params["multiplier_age62_20yos"] == 0.011

    set_pension_params(conn, effective_date="2030-01-01", multiplier_standard=0.012,
                        multiplier_age62_20yos=0.013,
                        mra_plus_10_reduction_per_year_under_62=0.04,
                        note="hypothetical law change")
    latest = get_current_pension_params(conn)
    assert latest["multiplier_standard"] == 0.012
    assert latest["note"] == "hypothetical law change"


def test_pension_profile_upsert(tmp_path):
    conn = make_conn(tmp_path)
    set_pension_profile(conn, owner="self", service_start_date="2016-12-11",
                         high_3_salary=145_000, mra_age=57, survivor_benefit_fraction=0.5)
    profile = get_pension_profile(conn, "self")
    assert profile["service_start_date"] == "2016-12-11"
    assert profile["high_3_salary"] == 145_000

    # upsert should update, not duplicate
    set_pension_profile(conn, owner="self", service_start_date="2016-12-11",
                         high_3_salary=150_000, mra_age=57, survivor_benefit_fraction=0.5)
    profile = get_pension_profile(conn, "self")
    assert profile["high_3_salary"] == 150_000


def test_pension_profile_delete_is_the_no_pension_path(tmp_path):
    conn = make_conn(tmp_path)
    set_pension_profile(conn, owner="spouse", service_start_date="2016-12-11", high_3_salary=100_000)
    assert get_pension_profile(conn, "spouse") is not None

    delete_pension_profile(conn, "spouse")
    assert get_pension_profile(conn, "spouse") is None


def test_age_on_matches_pension_py_day_count_convention(tmp_path):
    # Same (as_of - dob).days / 365.25 convention compute_fers_pension uses
    # for years_of_service, so a computed separation_age lines up with it.
    assert household.age_on("1988-06-01", date(2050, 6, 1)) == pytest.approx(62.0, abs=0.01)


def test_default_separation_date_is_dob_plus_62(tmp_path):
    assert household.default_separation_date("1988-06-01") == date(2050, 6, 1)
    # Feb 29 birthday with a non-leap target year falls back to Feb 28.
    assert household.default_separation_date("2000-02-29", retirement_age=23) == date(2023, 2, 28)
