"""Integration-style tests: real owlplanner solves (not mocked), confirming
case_builder.py + solve.py actually produce a working Plan end to end, per
the project's 'don't guess at the Owl API, verify it' rule."""

from datetime import date, timedelta

from app.assumptions import crud, household
from app.assumptions.contributions import set_contribution_profile
from app.assumptions.pension import set_pension_profile
from app.assumptions.salary import set_salary_profile
from app.assumptions.social_security import set_social_security_profile
from app.db import get_connection, init_db
from app.owl_integration.case_builder import ScenarioParams, _hfp_dataframe, build_plan
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


def test_hfp_dataframe_zeroes_wages_and_contributions_at_separation(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    this_year = date.today().year
    set_salary_profile(conn, owner="self", current_salary=100_000, annual_increase_pct=0.03)
    set_contribution_profile(conn, owner="self", tsp_traditional_pct=0.05, tsp_roth_pct=0.05)

    years = list(range(this_year, this_year + 10))
    separation_year = this_year + 5
    df = _hfp_dataframe(
        conn, owner="self", owners=["self"], years=years, date_of_birth="1988-06-01",
        separation_year=separation_year, include_catchup=False,
    )

    before = df[df["year"] < separation_year]
    after = df[df["year"] >= separation_year]
    assert (before["anticipated wages"] > 0).all()
    assert (before["401k ctrb"] > 0).all()
    assert (after["anticipated wages"] == 0).all()
    assert (after["401k ctrb"] == 0).all()
    assert (after["Roth 401k ctrb"] == 0).all()


def test_hfp_dataframe_ira_contributions_flow_with_zero_salary(tmp_path):
    # Regression: contributions used to be computed only for years present
    # in wages_by_year, so a non-earning spouse (no salary_profile/steps)
    # funding a spousal Roth IRA would never show up in the actual solve at
    # all, even with a saved contribution_profile.
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    this_year = date.today().year
    set_contribution_profile(conn, owner="self", ira_roth_annual=7_000)
    # Deliberately no salary_profile and no salary_steps for "self".

    df = _hfp_dataframe(
        conn, owner="self", owners=["self"], years=[this_year, this_year + 1],
        date_of_birth="1988-06-01", separation_year=None, include_catchup=False,
    )
    assert (df["anticipated wages"] == 0).all()
    assert (df["Roth IRA ctrb"] == 7_000).all()


def test_hfp_dataframe_no_contribution_profile_means_zero_contributions(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    this_year = date.today().year
    set_salary_profile(conn, owner="self", current_salary=100_000, annual_increase_pct=0.03)
    # No contribution_profile set.

    df = _hfp_dataframe(
        conn, owner="self", owners=["self"], years=[this_year], date_of_birth="1988-06-01",
        separation_year=None, include_catchup=False,
    )
    assert df["401k ctrb"].iloc[0] == 0
    assert df["anticipated wages"].iloc[0] > 0


def test_solve_scenario_includes_yearly_breakdown(tmp_path):
    conn = make_household(tmp_path)
    scenario = make_scenario()
    fingerprint = {"salary_steps": 1}

    result = solve_scenario(conn, FAKE_ACCOUNTS, scenario, fingerprint)
    yearly = result["yearly"]
    assert len(yearly) > 0

    years = [row["year"] for row in yearly]
    assert years == sorted(years)  # chronological, one row per solved year
    assert years[0] == date.today().year

    first = yearly[0]
    for key in ("taxable", "tax_deferred", "tax_free", "hsa", "total_assets",
                "net_worth", "liquid_net_worth", "net_spending", "total_tax",
                "wages", "pensions", "social_security"):
        assert key in first

    # Starting balances roughly match the Tiller pull ($80k taxable, $320k
    # tax-deferred, $135k Roth -- allow drift since Owl grows/consumes within
    # year one depending on timing assumptions).
    assert first["total_assets"] > 400_000


def test_allow_roth_conversions_false_disables_conversions_entirely(tmp_path):
    conn = make_household(tmp_path)
    scenario = make_scenario()
    scenario.allow_roth_conversions = False
    fingerprint = {"x": 1}

    result = solve_scenario(conn, FAKE_ACCOUNTS, scenario, fingerprint, force_recalculate=True)
    assert result["case_status"] == "solved"
    assert all(row["roth_conversions"] == 0 for row in result["yearly"])


def test_allow_roth_conversions_true_is_the_default_and_can_convert(tmp_path):
    conn = make_household(tmp_path)
    scenario = make_scenario()
    assert scenario.allow_roth_conversions is True
    fingerprint = {"x": 1}

    result = solve_scenario(conn, FAKE_ACCOUNTS, scenario, fingerprint, force_recalculate=True)
    assert sum(row["roth_conversions"] for row in result["yearly"]) > 0


def test_marginal_federal_rate_is_a_sane_bracket_fraction(tmp_path):
    conn = make_household(tmp_path)
    scenario = make_scenario()
    fingerprint = {"x": 1}

    result = solve_scenario(conn, FAKE_ACCOUNTS, scenario, fingerprint, force_recalculate=True)
    for row in result["yearly"]:
        assert 0.0 <= row["marginal_federal_rate"] <= 0.37


def test_effective_federal_rate_is_sane_and_never_exceeds_marginal(tmp_path):
    # Effective (average) rate on ordinary income can't exceed the marginal
    # (next-dollar) rate -- marginal is the top slice of a progressive
    # schedule, effective blends in everything taxed below it.
    conn = make_household(tmp_path)
    scenario = make_scenario()
    fingerprint = {"x": 1}

    result = solve_scenario(conn, FAKE_ACCOUNTS, scenario, fingerprint, force_recalculate=True)
    for row in result["yearly"]:
        assert 0.0 <= row["effective_federal_rate"] <= 0.37
        assert row["effective_federal_rate"] <= row["marginal_federal_rate"] + 1e-9


def test_solve_scenario_force_recalculate_bypasses_cache(tmp_path):
    conn = make_household(tmp_path)
    scenario = make_scenario()
    fingerprint = {"salary_steps": 1}

    first = solve_scenario(conn, FAKE_ACCOUNTS, scenario, fingerprint)
    assert first["from_cache"] is False

    forced = solve_scenario(conn, FAKE_ACCOUNTS, scenario, fingerprint, force_recalculate=True)
    assert forced["from_cache"] is False
