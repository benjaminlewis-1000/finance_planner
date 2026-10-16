"""Integration-style tests: real owlplanner solves (not mocked), confirming
case_builder.py + solve.py actually produce a working Plan end to end, per
the project's 'don't guess at the Owl API, verify it' rule."""

from datetime import date, timedelta

from app.assumptions import crud, household
from app.assumptions.pension import set_pension_profile
from app.assumptions.social_security import set_social_security_profile
from app.db import get_connection, init_db
from app.owl_integration.case_builder import ScenarioParams, build_plan
from app.owl_integration.solve import solve_scenario

FAKE_ACCOUNTS = [
    {"Account Id": "acct1", "Account": "Brokerage", "Last Balance": "$80,000.00",
     "Institution": "Bank A", "Type": "Brokerage"},
    {"Account Id": "acct2", "Account": "401k", "Last Balance": "$320,000.00",
     "Institution": "Bank A", "Type": "401k"},
    {"Account Id": "acct3", "Account": "Roth IRA", "Last Balance": "$135,000.00",
     "Institution": "Bank A", "Type": "Roth IRA"},
]


def make_household(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)

    household.set_household_member(conn, owner="self", display_name="Ben",
                                     date_of_birth="1988-06-01", life_expectancy=92)

    from app.tiller.mapping import sync_accounts
    sync_accounts(conn, FAKE_ACCOUNTS)
    conn.execute("UPDATE account_mappings SET owl_bucket = 'taxable', needs_review = 0 WHERE account_id = 'acct1'")
    conn.execute("UPDATE account_mappings SET owl_bucket = 'tax_deferred', needs_review = 0 WHERE account_id = 'acct2'")
    conn.execute("UPDATE account_mappings SET owl_bucket = 'roth', needs_review = 0 WHERE account_id = 'acct3'")
    conn.commit()

    crud.add_salary_step(conn, owner="self", effective_date=date.today().isoformat(), annual_amount=140_000)

    set_pension_profile(conn, owner="self", service_start_date="2016-12-11",
                         high_3_salary=145_000, mra_age=57, survivor_benefit_fraction=0)
    set_social_security_profile(conn, owner="self", estimated_pia_monthly=2800, full_retirement_age=67)

    return conn


def make_scenario():
    separation_date = date.today() + timedelta(days=365 * 10)
    return ScenarioParams(
        separation_date={"self": separation_date},
        separation_age={"self": 57},
        ss_claiming_age={"self": 67},
    )


def test_build_plan_produces_a_solvable_owl_plan(tmp_path):
    conn = make_household(tmp_path)
    plan = build_plan(conn, FAKE_ACCOUNTS, make_scenario())

    plan.solve("maxSpending", options={"bequest": 0})  # returns None -- status lives on plan.caseStatus
    assert plan.caseStatus == "solved"
    assert plan.metricsDict()["total_spending_today"] > 0


def test_solve_scenario_caches_identical_inputs(tmp_path):
    conn = make_household(tmp_path)
    scenario = make_scenario()
    fingerprint = {"salary_steps": 1, "pension_profile_v": 1}  # stand-in fingerprint for this test

    first = solve_scenario(conn, FAKE_ACCOUNTS, scenario, fingerprint)
    assert first["from_cache"] is False
    assert "metrics" in first
    assert first["case_status"] == "solved"

    second = solve_scenario(conn, FAKE_ACCOUNTS, scenario, fingerprint)
    assert second["from_cache"] is True
    assert second["metrics"] == first["metrics"]


def test_solve_scenario_force_recalculate_bypasses_cache(tmp_path):
    conn = make_household(tmp_path)
    scenario = make_scenario()
    fingerprint = {"salary_steps": 1}

    first = solve_scenario(conn, FAKE_ACCOUNTS, scenario, fingerprint)
    assert first["from_cache"] is False

    forced = solve_scenario(conn, FAKE_ACCOUNTS, scenario, fingerprint, force_recalculate=True)
    assert forced["from_cache"] is False
