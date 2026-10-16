from sqlite3 import Connection
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.assumptions import crud, household
from app.assumptions.pension import (
    get_current_pension_params,
    get_pension_profile,
    set_pension_params,
    set_pension_profile,
)
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
