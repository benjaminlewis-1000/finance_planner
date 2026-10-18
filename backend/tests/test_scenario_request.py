"""ScenarioRequest.to_scenario_params no longer accepts separation_age from
the client -- it derives it from household_members.date_of_birth, so the UI
only has to show one number and the two can't disagree."""

from datetime import date

import pytest

from app.assumptions import household
from app.db import get_connection, init_db
from app.routers.common import ScenarioRequest


def make_conn(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return conn


def test_separation_age_is_derived_from_dob(tmp_path):
    conn = make_conn(tmp_path)
    household.set_household_member(conn, owner="self", display_name="Ben",
                                     date_of_birth="1988-06-01", life_expectancy=92)

    request = ScenarioRequest(spreadsheet_id="x", separation_date={"self": date(2050, 6, 1)})
    scenario = request.to_scenario_params(conn)

    assert scenario.separation_age["self"] == pytest.approx(62.0, abs=0.01)


def test_missing_separation_date_leaves_age_none(tmp_path):
    conn = make_conn(tmp_path)
    household.set_household_member(conn, owner="self", display_name="Ben",
                                     date_of_birth="1988-06-01", life_expectancy=92)

    request = ScenarioRequest(spreadsheet_id="x", separation_date={"self": None})
    scenario = request.to_scenario_params(conn)

    assert scenario.separation_age["self"] is None


def test_unknown_household_member_leaves_age_none(tmp_path):
    conn = make_conn(tmp_path)  # no household members seeded at all

    request = ScenarioRequest(spreadsheet_id="x", separation_date={"self": date(2050, 6, 1)})
    scenario = request.to_scenario_params(conn)

    assert scenario.separation_age["self"] is None
