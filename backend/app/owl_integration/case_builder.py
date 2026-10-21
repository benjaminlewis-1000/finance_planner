"""
Assembles a live owlplanner.Plan from stored assumptions + a fresh Tiller
balance pull, using the exact API confirmed in spike/owl_api_spike.py.

Unit conventions (do not change without re-checking the spike's findings):
- setAccountBalances: $k
- readHFP wage/event columns, setPension amounts, setSocialSecurity pias: whole $

Known gap (not guessed at, per the project's "don't guess at the Owl API"
rule): expense_changes isn't wired into the Plan yet. Owl's spending model is
a household-level "spending profile" (e.g. "flat"), not itemized per-category
deltas, and case_builder.py doesn't yet know how Owl wants a future spending
change expressed. Left as a follow-up once that's confirmed rather than
guessing at an unverified API shape.
"""

from dataclasses import dataclass, field
from datetime import date

import owlplanner as owl
import pandas as pd

from app.assumptions import crud, household
from app.assumptions.contributions import (
    compute_contributions_for_year,
    get_contribution_profile,
    get_current_contribution_caps,
)
from app.assumptions.pension import compute_fers_pension, get_current_pension_params, get_pension_profile
from app.assumptions.salary import compute_high_3, get_salary_profile, project_salary
from app.assumptions.social_security import compute_social_security_benefit, get_social_security_profile
from app.tiller.mapping import parse_balance

# Placeholder allocation/spending/rate assumptions until a dedicated settings
# feature exists for these (matches the spike's defaults, not derived from
# anything stored yet).
DEFAULT_ALLOCATION = [70, 30, 0, 0]
DEFAULT_RATES_METHOD = "historical average"
DEFAULT_RATES_START_YEAR = 1990
DEFAULT_RATES_END_YEAR = 2023


@dataclass
class ScenarioParams:
    """Per-scenario knobs that vary even though the underlying profiles don't
    -- this is the whole reason separation age/date and SS claiming age live
    here rather than on pension_profiles/social_security_profiles."""

    separation_date: dict[str, date | None] = field(default_factory=dict)
    separation_age: dict[str, float | None] = field(default_factory=dict)
    ss_claiming_age: dict[str, float | None] = field(default_factory=dict)
    include_catchup_contributions: dict[str, bool] = field(default_factory=dict)
    objective: str = "maxSpending"
    bequest: float = 0


def _bucket_balances(conn, pulled_accounts: list[dict], owners: list[str]) -> dict[str, dict[str, float]]:
    """Sums mapped, reviewed account balances into taxable/tax_deferred/roth
    per owner, in $k. Accounts needing review or marked 'excluded' never
    count. A 'joint' account is split 50/50 between self and spouse (no finer
    ownership signal exists in Tiller) -- if there's no spouse in the
    household, joint balances go entirely to 'self'."""
    balances_by_account = {
        row["Account Id"]: parse_balance(row.get("Last Balance", ""))
        for row in pulled_accounts
        if row.get("Account Id")
    }

    totals = {owner: {"taxable": 0.0, "tax_deferred": 0.0, "roth": 0.0} for owner in owners}

    mappings = conn.execute(
        "SELECT * FROM account_mappings WHERE needs_review = 0 AND owl_bucket != 'excluded'"
    ).fetchall()

    for mapping in mappings:
        balance = balances_by_account.get(mapping["account_id"])
        if balance is None:
            continue  # mapped account wasn't in this pull -- skip rather than guess

        bucket = mapping["owl_bucket"]
        owner = mapping["owner"]

        # 'mixed' (e.g. a TSP with both a traditional and a Roth balance)
        # splits into real buckets by mixed_roth_fraction before the
        # owner-split below -- the rest of this loop doesn't need to know
        # 'mixed' exists.
        if bucket == "mixed":
            roth_fraction = mapping["mixed_roth_fraction"] or 0.0
            contributions = [("roth", balance * roth_fraction), ("tax_deferred", balance * (1 - roth_fraction))]
        else:
            contributions = [(bucket, balance)]

        for sub_bucket, amount in contributions:
            if owner == "joint":
                if "spouse" in owners:
                    totals["self"][sub_bucket] += amount / 2
                    totals["spouse"][sub_bucket] += amount / 2
                else:
                    totals["self"][sub_bucket] += amount
            elif owner in totals:
                totals[owner][sub_bucket] += amount

    return {
        owner: {bucket: amount / 1000 for bucket, amount in buckets.items()}
        for owner, buckets in totals.items()
    }


def _hfp_dataframe(conn, owner: str, owners: list[str], years: list[int], date_of_birth: str,
                    separation_year: int | None, include_catchup: bool) -> pd.DataFrame:
    """A 'joint' one-off event is split 50/50 across both people's HFP frames
    when there's a spouse, same convention as joint account balances; with no
    spouse in the household it falls entirely on 'self'."""
    has_spouse = "spouse" in owners
    event_rows = [
        row for row in crud.list_one_off_events(conn)
        if row["owner"] == owner or row["owner"] == "joint"
    ]

    # A salary_profile (current salary + annual % growth) is the primary way
    # to project future wages; salary_steps (manual step entries) are only a
    # fallback for an owner with no profile set.
    salary_profile = get_salary_profile(conn, owner)
    if salary_profile is not None:
        wages_by_year = {year: project_salary(salary_profile, year) for year in years}
    else:
        wages_by_year = {}
        for step in crud.list_salary_steps(conn, owner=owner):
            step_year = date.fromisoformat(step["effective_date"]).year
            for year in years:
                if year >= step_year:
                    wages_by_year[year] = step["annual_amount"]

    # Wages stop at separation -- you can't earn a federal salary after
    # leaving. (Contributions, below, get their own year range: IRA/taxable
    # don't depend on this person having any wages at all -- e.g. a spousal
    # Roth IRA funded by the other spouse's income -- so they must not be
    # silently limited to years wages_by_year happens to have an entry for.)
    if separation_year is not None:
        wages_by_year = {year: amount for year, amount in wages_by_year.items() if year < separation_year}
    contribution_years = [y for y in years if separation_year is None or y < separation_year]

    events_by_year: dict[int, float] = {}
    for event in event_rows:
        event_year = date.fromisoformat(event["event_date"]).year
        amount = event["amount"]
        if event["owner"] == "joint" and has_spouse:
            amount = amount / 2
        events_by_year[event_year] = events_by_year.get(event_year, 0) + amount

    tsp_traditional_by_year, tsp_roth_by_year = {}, {}
    ira_traditional_by_year, ira_roth_by_year = {}, {}
    taxable_ctrb_by_year = {}
    contribution_profile = get_contribution_profile(conn, owner)
    if contribution_profile is not None:
        caps = get_current_contribution_caps(conn)
        cap_base_year = date.fromisoformat(caps["effective_date"]).year
        for year in contribution_years:
            salary = wages_by_year.get(year, 0)
            c = compute_contributions_for_year(
                contribution_profile, caps, salary, date_of_birth, year, cap_base_year, include_catchup,
            )
            # Agency match always lands in traditional TSP, same column Owl
            # sums with "IRA ctrb" into the tax-deferred bucket.
            tsp_traditional_by_year[year] = c["tsp_traditional"] + c["agency_match"]
            tsp_roth_by_year[year] = c["tsp_roth"]
            ira_traditional_by_year[year] = c["ira_traditional"]
            ira_roth_by_year[year] = c["ira_roth"]
            taxable_ctrb_by_year[year] = c["taxable"]

    return pd.DataFrame({
        "year": years,
        "anticipated wages": [wages_by_year.get(year, 0) for year in years],
        "big-ticket items": [events_by_year.get(year, 0) for year in years],
        "401k ctrb": [tsp_traditional_by_year.get(year, 0) for year in years],
        "Roth 401k ctrb": [tsp_roth_by_year.get(year, 0) for year in years],
        "IRA ctrb": [ira_traditional_by_year.get(year, 0) for year in years],
        "Roth IRA ctrb": [ira_roth_by_year.get(year, 0) for year in years],
        "taxable ctrb": [taxable_ctrb_by_year.get(year, 0) for year in years],
    })


def _pension_amount_and_age(conn, owner: str, scenario: ScenarioParams) -> tuple[float, float, float]:
    profile = get_pension_profile(conn, owner)
    separation_date = scenario.separation_date.get(owner)
    separation_age = scenario.separation_age.get(owner)

    if profile is None or separation_date is None or separation_age is None:
        return 0.0, 65.0, 0.0  # no pension for this person in this scenario

    high_3_salary = profile["high_3_salary"]
    if high_3_salary is None:
        salary_profile = get_salary_profile(conn, owner)
        if salary_profile is None:
            return 0.0, 65.0, 0.0  # no high-3 override and no salary profile to project it from
        high_3_salary = compute_high_3(salary_profile, separation_date.year)

    params = get_current_pension_params(conn)
    result = compute_fers_pension(
        service_start_date=date.fromisoformat(profile["service_start_date"]),
        separation_date=separation_date,
        separation_age=separation_age,
        high_3_salary=high_3_salary,
        mra_age=profile["mra_age"],
        params=params,
    )
    return result["monthly_pension"], separation_age, profile["survivor_benefit_fraction"]


def _ss_amount_and_age(conn, owner: str, scenario: ScenarioParams) -> tuple[float, float]:
    profile = get_social_security_profile(conn, owner)
    if profile is None:
        return 0.0, 67.0

    claiming_age = scenario.ss_claiming_age.get(owner, profile["full_retirement_age"])
    result = compute_social_security_benefit(
        pia_monthly=profile["estimated_pia_monthly"],
        full_retirement_age=profile["full_retirement_age"],
        claiming_age=claiming_age,
    )
    return result["monthly_benefit"], claiming_age


def build_plan(conn, pulled_accounts: list[dict], scenario: ScenarioParams) -> owl.Plan:
    members = household.list_household_members(conn)
    if not members:
        raise ValueError("No household_members rows -- at least a 'self' profile is required")

    owners = [member["owner"] for member in members]
    inames = [member["display_name"] for member in members]
    dobs = [member["date_of_birth"] for member in members]
    life_expectancies = [member["life_expectancy"] for member in members]

    plan = owl.Plan(inames, dobs, life_expectancies, "household")

    balances = _bucket_balances(conn, pulled_accounts, owners)
    plan.setAccountBalances(
        taxable=[balances[owner]["taxable"] for owner in owners],
        taxDeferred=[balances[owner]["tax_deferred"] for owner in owners],
        taxFree=[balances[owner]["roth"] for owner in owners],
        startDate="01-01",
    )

    this_year = date.today().year
    horizon_years = list(range(this_year, this_year + max(life_expectancies) + 1))
    hfp = {}
    for member in members:
        owner = member["owner"]
        sep_date = scenario.separation_date.get(owner)
        hfp[member["display_name"]] = _hfp_dataframe(
            conn, owner, owners, horizon_years,
            date_of_birth=member["date_of_birth"],
            separation_year=sep_date.year if sep_date else None,
            include_catchup=scenario.include_catchup_contributions.get(owner, False),
        )
    plan.readHFP(hfp)

    pension_amounts, pension_ages, survivor_fractions = [], [], []
    for owner in owners:
        amount, age, survivor_fraction = _pension_amount_and_age(conn, owner, scenario)
        pension_amounts.append(amount)
        pension_ages.append(age)
        survivor_fractions.append(survivor_fraction)
    plan.setPension(
        amounts=pension_amounts,
        ages=pension_ages,
        indexed=[False] * len(owners),
        survivor_fraction=survivor_fractions,
    )

    ss_amounts, ss_ages = [], []
    for owner in owners:
        amount, age = _ss_amount_and_age(conn, owner, scenario)
        ss_amounts.append(amount)
        ss_ages.append(age)
    plan.setSocialSecurity(ss_amounts, ss_ages)

    plan.setAllocationRatios(
        "individual",
        generic=[[[*DEFAULT_ALLOCATION], [*DEFAULT_ALLOCATION]] for _ in owners],
    )
    plan.setSpendingProfile("flat")
    plan.setRates(DEFAULT_RATES_METHOD, DEFAULT_RATES_START_YEAR, DEFAULT_RATES_END_YEAR)

    return plan
