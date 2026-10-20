from datetime import date
from sqlite3 import Connection
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.assumptions import crud, household
from app.assumptions.contributions import (
    compute_contributions_for_year,
    delete_contribution_profile,
    get_contribution_profile,
    get_current_contribution_caps,
    set_contribution_caps,
    set_contribution_profile,
)
from app.assumptions.pension import (
    compute_fers_pension,
    delete_pension_profile,
    get_current_pension_params,
    get_pension_profile,
    set_pension_params,
    set_pension_profile,
)
from app.assumptions.salary import compute_high_3, get_salary_profile, project_salary, set_salary_profile
from app.assumptions.social_security import get_social_security_profile, set_social_security_profile
from app.db import get_db

router = APIRouter(prefix="/assumptions", tags=["assumptions"])

Owner = Literal["self", "spouse"]
EventOwner = Literal["self", "spouse", "joint"]


# --- salary steps -----------------------------------------------------------

class SalaryStepIn(BaseModel):
    owner: Owner
    effective_date: str
    annual_amount: float
    note: str | None = None


@router.post("/salary-steps")
def add_salary_step(body: SalaryStepIn, conn: Connection = Depends(get_db)):
    step_id = crud.add_salary_step(conn, body.owner, body.effective_date, body.annual_amount, body.note)
    return {"id": step_id}


@router.get("/salary-steps")
def list_salary_steps(owner: Owner | None = None, conn: Connection = Depends(get_db)):
    return [dict(row) for row in crud.list_salary_steps(conn, owner)]


@router.delete("/salary-steps/{step_id}")
def delete_salary_step(step_id: int, conn: Connection = Depends(get_db)):
    crud.delete_salary_step(conn, step_id)
    return {"deleted": step_id}


# --- expense changes ---------------------------------------------------------

class ExpenseChangeIn(BaseModel):
    effective_date: str
    category: str
    annual_amount_delta: float
    note: str | None = None


@router.post("/expense-changes")
def add_expense_change(body: ExpenseChangeIn, conn: Connection = Depends(get_db)):
    change_id = crud.add_expense_change(conn, body.effective_date, body.category, body.annual_amount_delta, body.note)
    return {"id": change_id}


@router.get("/expense-changes")
def list_expense_changes(conn: Connection = Depends(get_db)):
    return [dict(row) for row in crud.list_expense_changes(conn)]


@router.delete("/expense-changes/{change_id}")
def delete_expense_change(change_id: int, conn: Connection = Depends(get_db)):
    crud.delete_expense_change(conn, change_id)
    return {"deleted": change_id}


# --- one-off events -----------------------------------------------------------

class OneOffEventIn(BaseModel):
    event_date: str
    description: str
    amount: float
    category: str | None = None
    owner: EventOwner = "joint"


@router.post("/one-off-events")
def add_one_off_event(body: OneOffEventIn, conn: Connection = Depends(get_db)):
    event_id = crud.add_one_off_event(conn, body.event_date, body.description, body.amount, body.category, body.owner)
    return {"id": event_id}


@router.get("/one-off-events")
def list_one_off_events(conn: Connection = Depends(get_db)):
    return [dict(row) for row in crud.list_one_off_events(conn)]


@router.delete("/one-off-events/{event_id}")
def delete_one_off_event(event_id: int, conn: Connection = Depends(get_db)):
    crud.delete_one_off_event(conn, event_id)
    return {"deleted": event_id}


# --- salary growth profile (per person) --------------------------------------

class SalaryProfileIn(BaseModel):
    current_salary: float
    annual_increase_pct: float = 0


@router.get("/salary-profile/{owner}")
def get_salary_profile_route(owner: Owner, conn: Connection = Depends(get_db)):
    profile = get_salary_profile(conn, owner)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"No salary profile for '{owner}'")
    return dict(profile)


@router.put("/salary-profile/{owner}")
def put_salary_profile(owner: Owner, body: SalaryProfileIn, conn: Connection = Depends(get_db)):
    set_salary_profile(conn, owner, body.current_salary, body.annual_increase_pct)
    return dict(get_salary_profile(conn, owner))


# --- contribution caps (versioned IRS limits) --------------------------------

class ContributionCapsIn(BaseModel):
    effective_date: str
    elective_deferral_limit: float
    elective_deferral_catchup_50: float
    elective_deferral_catchup_60_63: float
    ira_limit: float
    ira_catchup_50: float
    annual_growth_pct: float = 0.02
    increment: float = 500
    note: str | None = None


@router.get("/contribution-caps")
def get_contribution_caps_route(conn: Connection = Depends(get_db)):
    return dict(get_current_contribution_caps(conn))


@router.put("/contribution-caps")
def put_contribution_caps(body: ContributionCapsIn, conn: Connection = Depends(get_db)):
    caps_id = set_contribution_caps(
        conn, body.effective_date, body.elective_deferral_limit, body.elective_deferral_catchup_50,
        body.elective_deferral_catchup_60_63, body.ira_limit, body.ira_catchup_50,
        body.annual_growth_pct, body.increment, body.note,
    )
    return {"id": caps_id}


# --- contribution profile (per person) ---------------------------------------

class ContributionProfileIn(BaseModel):
    tsp_traditional_pct: float = 0
    tsp_roth_pct: float = 0
    tsp_max: bool = False
    ira_traditional_annual: float = 0
    ira_roth_annual: float = 0
    ira_max: bool = False
    taxable_annual: float = 0


@router.get("/contribution-profile/{owner}")
def get_contribution_profile_route(owner: Owner, conn: Connection = Depends(get_db)):
    profile = get_contribution_profile(conn, owner)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"No contribution profile for '{owner}'")
    return dict(profile)


@router.put("/contribution-profile/{owner}")
def put_contribution_profile(owner: Owner, body: ContributionProfileIn, conn: Connection = Depends(get_db)):
    set_contribution_profile(conn, owner, body.tsp_traditional_pct, body.tsp_roth_pct,
                              body.ira_traditional_annual, body.ira_roth_annual, body.taxable_annual,
                              body.tsp_max, body.ira_max)
    return dict(get_contribution_profile(conn, owner))


@router.delete("/contribution-profile/{owner}")
def delete_contribution_profile_route(owner: Owner, conn: Connection = Depends(get_db)):
    delete_contribution_profile(conn, owner)
    return {"deleted": owner}


@router.get("/contribution-preview/{owner}")
def contribution_preview(
    owner: Owner,
    year: int | None = None,
    include_catchup: bool = False,
    tsp_traditional_pct: float | None = None,
    tsp_roth_pct: float | None = None,
    tsp_max: bool | None = None,
    ira_traditional_annual: float | None = None,
    ira_roth_annual: float | None = None,
    ira_max: bool | None = None,
    taxable_annual: float | None = None,
    conn: Connection = Depends(get_db),
):
    """Read-only: what this year's (or a given year's) contributions and
    agency match actually come out to -- lets you sanity-check the cap/match
    math without running a full solve.

    The profile fields are query overrides (same pattern as pension-preview)
    so the UI can preview whatever's currently in its input boxes, not only
    what's been saved -- each falls back to the saved contribution_profile
    when omitted entirely."""
    member = household.get_household_member(conn, owner)
    if member is None:
        raise HTTPException(status_code=404, detail=f"No household member '{owner}'")

    overrides = {
        "tsp_traditional_pct": tsp_traditional_pct, "tsp_roth_pct": tsp_roth_pct, "tsp_max": tsp_max,
        "ira_traditional_annual": ira_traditional_annual, "ira_roth_annual": ira_roth_annual, "ira_max": ira_max,
        "taxable_annual": taxable_annual,
    }
    profile = get_contribution_profile(conn, owner)
    if profile is None and all(v is None for v in overrides.values()):
        return {"has_contribution_profile": False}

    effective_profile = {
        field: (value if value is not None else (profile[field] if profile else (False if field.endswith("_max") else 0.0)))
        for field, value in overrides.items()
    }

    salary_profile = get_salary_profile(conn, owner)
    if salary_profile is None:
        raise HTTPException(status_code=400, detail=f"No salary profile set for '{owner}'")

    target_year = year or date.today().year
    salary = project_salary(salary_profile, target_year)

    caps = get_current_contribution_caps(conn)
    cap_base_year = date.fromisoformat(caps["effective_date"]).year
    result = compute_contributions_for_year(
        effective_profile, caps, salary, member["date_of_birth"], target_year, cap_base_year, include_catchup,
    )
    return {"has_contribution_profile": True, "year": target_year, "salary_used": salary, **result}


@router.get("/contribution-projection/{owner}")
def contribution_projection(owner: Owner, num_years: int = 15, include_catchup: bool = False,
                             conn: Connection = Depends(get_db)):
    """Year-by-year salary, cap, and contribution figures for the
    Assumptions page's chart/table -- flags years where either cap actually
    changed from the prior year, since real IRS caps move in lumpy,
    infrequent steps, not every year. Still returns salary/cap rows (with
    zeroed contributions) when there's no saved contribution_profile, so the
    cap-growth chart works on its own."""
    member = household.get_household_member(conn, owner)
    if member is None:
        raise HTTPException(status_code=404, detail=f"No household member '{owner}'")

    salary_profile = get_salary_profile(conn, owner)
    if salary_profile is None:
        raise HTTPException(status_code=400, detail=f"No salary profile set for '{owner}'")

    profile = get_contribution_profile(conn, owner)
    effective_profile = profile if profile is not None else {
        "tsp_traditional_pct": 0.0, "tsp_roth_pct": 0.0, "tsp_max": False,
        "ira_traditional_annual": 0.0, "ira_roth_annual": 0.0, "ira_max": False,
        "taxable_annual": 0.0,
    }

    caps = get_current_contribution_caps(conn)
    cap_base_year = date.fromisoformat(caps["effective_date"]).year

    this_year = date.today().year
    rows = []
    prev_tsp_cap = prev_ira_cap = None
    for year in range(this_year, this_year + num_years):
        salary = project_salary(salary_profile, year)
        result = compute_contributions_for_year(
            effective_profile, caps, salary, member["date_of_birth"], year, cap_base_year, include_catchup,
        )
        rows.append({
            "year": year,
            "salary": salary,
            "tsp_cap": result["tsp_cap_used"],
            "tsp_cap_changed": prev_tsp_cap is not None and result["tsp_cap_used"] != prev_tsp_cap,
            "ira_cap": result["ira_cap_used"],
            "ira_cap_changed": prev_ira_cap is not None and result["ira_cap_used"] != prev_ira_cap,
            "tsp_traditional": result["tsp_traditional"],
            "tsp_roth": result["tsp_roth"],
            "agency_match": result["agency_match"],
            "ira_traditional": result["ira_traditional"],
            "ira_roth": result["ira_roth"],
            "taxable": result["taxable"],
        })
        prev_tsp_cap, prev_ira_cap = result["tsp_cap_used"], result["ira_cap_used"]

    return {"has_contribution_profile": profile is not None, "rows": rows}


# --- pension params (versioned FERS formula knobs) ---------------------------

class PensionParamsIn(BaseModel):
    effective_date: str
    multiplier_standard: float
    multiplier_age62_20yos: float
    mra_plus_10_reduction_per_year_under_62: float
    note: str | None = None


@router.get("/pension-params")
def get_pension_params(conn: Connection = Depends(get_db)):
    return dict(get_current_pension_params(conn))


@router.put("/pension-params")
def put_pension_params(body: PensionParamsIn, conn: Connection = Depends(get_db)):
    params_id = set_pension_params(
        conn, body.effective_date, body.multiplier_standard, body.multiplier_age62_20yos,
        body.mra_plus_10_reduction_per_year_under_62, body.note,
    )
    return {"id": params_id}


# --- pension profile (per person) --------------------------------------------

class PensionProfileIn(BaseModel):
    service_start_date: str
    high_3_salary: float | None = None
    mra_age: float = 57
    survivor_benefit_fraction: float = 0


@router.get("/pension-profile/{owner}")
def get_pension_profile_route(owner: Owner, conn: Connection = Depends(get_db)):
    profile = get_pension_profile(conn, owner)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"No pension profile for '{owner}'")
    return dict(profile)


@router.put("/pension-profile/{owner}")
def put_pension_profile(owner: Owner, body: PensionProfileIn, conn: Connection = Depends(get_db)):
    set_pension_profile(conn, owner, body.service_start_date, body.high_3_salary,
                         body.mra_age, body.survivor_benefit_fraction)
    return dict(get_pension_profile(conn, owner))


@router.delete("/pension-profile/{owner}")
def delete_pension_profile_route(owner: Owner, conn: Connection = Depends(get_db)):
    """The 'this person has no FERS pension' path -- e.g. a spouse who isn't
    a federal employee."""
    delete_pension_profile(conn, owner)
    return {"deleted": owner}


@router.get("/pension-preview/{owner}")
def pension_preview(
    owner: Owner,
    service_start_date: str | None = None,
    mra_age: float | None = None,
    high_3_salary: float | None = None,
    separation_date: str | None = None,
    conn: Connection = Depends(get_db),
):
    """Read-only preliminary math: years of service, which multiplier tier
    applies, the high-3 and final-year salary used, and the resulting
    estimated pension -- same formula /solve feeds Owl.

    service_start_date/mra_age/high_3_salary are query overrides so a caller
    (the Pension page, mid-edit before any Save) can preview against
    whatever's currently in its input fields rather than only what's been
    persisted -- each falls back to the saved pension_profile when omitted.
    Defaults separation_date to DOB + 62 when not given (the Solve page
    instead passes the scenario's actual chosen separation date)."""
    member = household.get_household_member(conn, owner)
    if member is None:
        raise HTTPException(status_code=404, detail=f"No household member '{owner}'")

    profile = get_pension_profile(conn, owner)
    if service_start_date is None:
        if profile is None:
            return {"has_pension": False}
        service_start_date = profile["service_start_date"]
    if mra_age is None:
        mra_age = profile["mra_age"] if profile else 57
    if high_3_salary is None and profile is not None:
        high_3_salary = profile["high_3_salary"]

    sep_date = (
        date.fromisoformat(separation_date) if separation_date
        else household.default_separation_date(member["date_of_birth"])
    )
    sep_age = household.age_on(member["date_of_birth"], sep_date)

    salary_profile = get_salary_profile(conn, owner)
    if high_3_salary is None:
        if salary_profile is None:
            raise HTTPException(
                status_code=400,
                detail=f"No high_3_salary override and no salary profile set for '{owner}'",
            )
        high_3_salary = compute_high_3(salary_profile, sep_date.year)
    projected_final_salary = project_salary(salary_profile, sep_date.year) if salary_profile else None

    params = get_current_pension_params(conn)
    result = compute_fers_pension(
        service_start_date=date.fromisoformat(service_start_date),
        separation_date=sep_date,
        separation_age=sep_age,
        high_3_salary=high_3_salary,
        mra_age=mra_age,
        params=params,
    )
    return {
        "has_pension": True,
        "separation_date": sep_date.isoformat(),
        "separation_age": sep_age,
        "high_3_salary_used": high_3_salary,
        "projected_final_salary": projected_final_salary,
        **result,
    }


# --- social security profile (per person) ------------------------------------

class SocialSecurityProfileIn(BaseModel):
    estimated_pia_monthly: float
    full_retirement_age: float = 67


@router.get("/social-security-profile/{owner}")
def get_social_security_profile_route(owner: Owner, conn: Connection = Depends(get_db)):
    profile = get_social_security_profile(conn, owner)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"No Social Security profile for '{owner}'")
    return dict(profile)


@router.put("/social-security-profile/{owner}")
def put_social_security_profile(owner: Owner, body: SocialSecurityProfileIn, conn: Connection = Depends(get_db)):
    set_social_security_profile(conn, owner, body.estimated_pia_monthly, body.full_retirement_age)
    return dict(get_social_security_profile(conn, owner))


# --- household members (name, DOB, life expectancy) --------------------------

class HouseholdMemberIn(BaseModel):
    display_name: str
    date_of_birth: str
    life_expectancy: int = 92


@router.get("/household-members")
def list_household_members(conn: Connection = Depends(get_db)):
    return [dict(row) for row in household.list_household_members(conn)]


@router.put("/household-member/{owner}")
def put_household_member(owner: Owner, body: HouseholdMemberIn, conn: Connection = Depends(get_db)):
    household.set_household_member(conn, owner, body.display_name, body.date_of_birth, body.life_expectancy)
    return dict(household.get_household_member(conn, owner))
