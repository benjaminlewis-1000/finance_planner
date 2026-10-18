"""
FERS pension modeling.

owlplanner's setPension() only takes a pre-computed monthly $ benefit and a
commencement age (confirmed in the spike) -- it has no FERS-aware logic of
its own. This module computes that benefit so case_builder.py (step 5) can
hand Owl a flat number.

Scope / simplifications (deliberate, not oversights):
- No FERS special-category-employee rules (law enforcement, firefighters,
  air traffic control have different multipliers/eligibility) -- standard
  FERS only.
- No FERS annuity supplement (the bridge payment some early retirees get
  until age 62) -- modeled as a possible future addition, not here.
- MRA (Minimum Retirement Age, 55-57 depending on birth year) is a direct
  user-entered field on the pension profile rather than derived from a
  birth-year table, since we don't otherwise track birth dates.
- `separation_age` is treated as the pension commencement age (matches how
  it's fed straight into Owl's setPension(ages=...)) -- deferred-vs-immediate
  claiming nuances beyond the MRA+10 reduction aren't modeled separately.

FERS formula:
- Standard multiplier: 1%/year of service x high-3 average salary.
- Enhanced multiplier: 1.1%/year IF commencing at age 62+ with >= 20 years
  of service (5 U.S.C. 8415(g)).
- Unreduced immediate eligibility: MRA + 30 YOS, OR age 60 + 20 YOS, OR age
  62 + 5 YOS.
- MRA+10 (10-29 YOS, commencing at/after MRA but before one of the unreduced
  paths above): reduced 5%/year (default, editable) for each year under 62.
"""

import sqlite3
from datetime import date, datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- pension_params (versioned FERS formula knobs) --------------------------

def get_current_pension_params(conn: sqlite3.Connection) -> sqlite3.Row:
    """Most recently effective params row, inserting the statutory defaults
    on first use so callers never have to special-case "no params yet"."""
    row = conn.execute(
        "SELECT * FROM pension_params ORDER BY effective_date DESC, id DESC LIMIT 1"
    ).fetchone()
    if row is not None:
        return row

    today = date.today().isoformat()
    conn.execute(
        """
        INSERT INTO pension_params
            (effective_date, multiplier_standard, multiplier_age62_20yos,
             mra_plus_10_reduction_per_year_under_62, note, created_at)
        VALUES (?, 0.01, 0.011, 0.05, 'Statutory FERS defaults', ?)
        """,
        (today, _now()),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM pension_params ORDER BY effective_date DESC, id DESC LIMIT 1"
    ).fetchone()


def set_pension_params(conn: sqlite3.Connection, effective_date: str, multiplier_standard: float,
                        multiplier_age62_20yos: float, mra_plus_10_reduction_per_year_under_62: float,
                        note: str | None = None) -> int:
    """Insert a new version rather than updating in place, so a scenario
    snapshot saved under the old params keeps meaning what it meant then."""
    cur = conn.execute(
        """
        INSERT INTO pension_params
            (effective_date, multiplier_standard, multiplier_age62_20yos,
             mra_plus_10_reduction_per_year_under_62, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (effective_date, multiplier_standard, multiplier_age62_20yos,
         mra_plus_10_reduction_per_year_under_62, note, _now()),
    )
    conn.commit()
    return cur.lastrowid


# --- pension_profiles (per person) -------------------------------------------

def set_pension_profile(conn: sqlite3.Connection, owner: str, service_start_date: str,
                         high_3_salary: float | None = None, mra_age: float = 57,
                         survivor_benefit_fraction: float = 0) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO pension_profiles
            (owner, service_start_date, high_3_salary, mra_age, survivor_benefit_fraction,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner) DO UPDATE SET
            service_start_date = excluded.service_start_date,
            high_3_salary = excluded.high_3_salary,
            mra_age = excluded.mra_age,
            survivor_benefit_fraction = excluded.survivor_benefit_fraction,
            updated_at = excluded.updated_at
        """,
        (owner, service_start_date, high_3_salary, mra_age, survivor_benefit_fraction, now, now),
    )
    conn.commit()


def get_pension_profile(conn: sqlite3.Connection, owner: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM pension_profiles WHERE owner = ?", (owner,)).fetchone()


def delete_pension_profile(conn: sqlite3.Connection, owner: str) -> None:
    """The explicit 'this person has no pension' path -- removing the profile
    row, not just leaving fields blank, so the pension-preview UI can show
    'No pension' instead of a zeroed-out form."""
    conn.execute("DELETE FROM pension_profiles WHERE owner = ?", (owner,))
    conn.commit()


# --- the calculation itself --------------------------------------------------

def compute_fers_pension(service_start_date: date, separation_date: date, separation_age: float,
                          high_3_salary: float, mra_age: float, params: sqlite3.Row) -> dict:
    years_of_service = (separation_date - service_start_date).days / 365.25

    enhanced = separation_age >= 62 and years_of_service >= 20
    multiplier = params["multiplier_age62_20yos"] if enhanced else params["multiplier_standard"]
    base_annual = multiplier * high_3_salary * years_of_service

    unreduced = (
        (years_of_service >= 30 and separation_age >= mra_age)
        or (separation_age >= 60 and years_of_service >= 20)
        or (separation_age >= 62 and years_of_service >= 5)
    )
    mra_plus_10_eligible = (not unreduced) and separation_age >= mra_age and years_of_service >= 10

    reduction_fraction = 0.0
    if mra_plus_10_eligible and separation_age < 62:
        reduction_fraction = min(
            params["mra_plus_10_reduction_per_year_under_62"] * (62 - separation_age), 1.0
        )

    annual_pension = base_annual * (1 - reduction_fraction)

    eligible_immediate = unreduced or mra_plus_10_eligible
    if unreduced:
        eligibility_note = "Unreduced immediate annuity"
    elif mra_plus_10_eligible:
        eligibility_note = "MRA+10 reduced immediate annuity"
    else:
        eligibility_note = "Not eligible for an immediate annuity at this age/service -- deferred case, not modeled"

    return {
        "years_of_service": years_of_service,
        "multiplier_used": multiplier,
        "enhanced_multiplier": enhanced,
        "base_annual_pension": base_annual,
        "reduction_fraction": reduction_fraction,
        "annual_pension": annual_pension,
        "monthly_pension": annual_pension / 12,
        "eligible_immediate": eligible_immediate,
        "eligibility_note": eligibility_note,
    }
