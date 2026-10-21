"""Ongoing retirement contributions (TSP, agency match, IRA, taxable) during
working years, feeding case_builder.py's "...ctrb" HFP columns -- without
this, the solve assumed zero ongoing contributions (confirmed directly
against owlplanner's hfp_io.py, which defaults any absent column to zero).

FERS agency TSP match (5 U.S.C. 8432(c)): 1% automatic contribution
(regardless of employee election) + 100% match on the employee's first 3%
+ 50% match on the next 2% -- caps out at 5% total once the employee
contributes >=5%. By law, match money always lands in traditional TSP, even
if the employee elected 100% Roth.

IRS contribution caps are versioned/editable (contribution_caps), same
pattern as pension_params, since they change by law most years. A cap not
yet reached by the latest stored row is projected forward by compounding
annual_growth_pct and flooring to the nearest increment -- real IRS caps
move in lumpy steps, not smooth annual increases, so a projected cap can sit
flat for a few years before the next jump.

Catch-up contributions (50+ and the 60-63 SECURE 2.0 "super" catch-up) are
scenario-controlled (ScenarioParams.include_catchup_contributions), not
always-on -- a scenario can compare "assume I keep maxing out with catch-up"
against "assume I don't bother."
"""

import math
import sqlite3
from datetime import date, datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- contribution_profiles (per person) --------------------------------------

def set_contribution_profile(conn: sqlite3.Connection, owner: str, tsp_traditional_pct: float = 0,
                              tsp_roth_pct: float = 0, ira_traditional_annual: float = 0,
                              ira_roth_annual: float = 0, taxable_annual: float = 0,
                              tsp_max: bool = False, ira_max: bool = False,
                              tsp_roth_share: float = 0.5, ira_roth_share: float = 1.0) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO contribution_profiles
            (owner, tsp_traditional_pct, tsp_roth_pct, tsp_max, tsp_roth_share, ira_traditional_annual,
             ira_roth_annual, ira_max, ira_roth_share, taxable_annual, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner) DO UPDATE SET
            tsp_traditional_pct = excluded.tsp_traditional_pct,
            tsp_roth_pct = excluded.tsp_roth_pct,
            tsp_max = excluded.tsp_max,
            tsp_roth_share = excluded.tsp_roth_share,
            ira_traditional_annual = excluded.ira_traditional_annual,
            ira_roth_annual = excluded.ira_roth_annual,
            ira_max = excluded.ira_max,
            ira_roth_share = excluded.ira_roth_share,
            taxable_annual = excluded.taxable_annual,
            updated_at = excluded.updated_at
        """,
        (owner, tsp_traditional_pct, tsp_roth_pct, int(tsp_max), tsp_roth_share, ira_traditional_annual,
         ira_roth_annual, int(ira_max), ira_roth_share, taxable_annual, now, now),
    )
    conn.commit()


def get_contribution_profile(conn: sqlite3.Connection, owner: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM contribution_profiles WHERE owner = ?", (owner,)).fetchone()


def delete_contribution_profile(conn: sqlite3.Connection, owner: str) -> None:
    conn.execute("DELETE FROM contribution_profiles WHERE owner = ?", (owner,))
    conn.commit()


# --- contribution_caps (versioned IRS limits) --------------------------------

def get_current_contribution_caps(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM contribution_caps ORDER BY effective_date DESC, id DESC LIMIT 1"
    ).fetchone()
    if row is not None:
        return row

    today = date.today().isoformat()
    conn.execute(
        """
        INSERT INTO contribution_caps (effective_date, note, created_at)
        VALUES (?, 'Statutory defaults (table column defaults)', ?)
        """,
        (today, _now()),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM contribution_caps ORDER BY effective_date DESC, id DESC LIMIT 1"
    ).fetchone()


def set_contribution_caps(conn: sqlite3.Connection, effective_date: str, elective_deferral_limit: float,
                           elective_deferral_catchup_50: float, elective_deferral_catchup_60_63: float,
                           ira_limit: float, ira_catchup_50: float, annual_growth_pct: float = 0.02,
                           increment: float = 500, note: str | None = None) -> int:
    cur = conn.execute(
        """
        INSERT INTO contribution_caps
            (effective_date, elective_deferral_limit, elective_deferral_catchup_50,
             elective_deferral_catchup_60_63, ira_limit, ira_catchup_50,
             annual_growth_pct, increment, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (effective_date, elective_deferral_limit, elective_deferral_catchup_50,
         elective_deferral_catchup_60_63, ira_limit, ira_catchup_50,
         annual_growth_pct, increment, note, _now()),
    )
    conn.commit()
    return cur.lastrowid


# --- cap projection -----------------------------------------------------------

def project_cap(base_cap: float, base_year: int, target_year: int,
                 annual_growth_pct: float, increment: float) -> float:
    """Compounds from base_year to target_year, then floors to the nearest
    increment -- a real IRS cap can sit flat for a few years before jumping
    to the next $increment step, not rise smoothly."""
    if target_year <= base_year or increment <= 0:
        return base_cap
    grown = base_cap * (1 + annual_growth_pct) ** (target_year - base_year)
    return math.floor(grown / increment) * increment


def _age_in_year(date_of_birth: str, year: int) -> int:
    """Calendar-year age (the IRS's own convention for catch-up eligibility:
    whether you turn the age by year-end), not a precise day count."""
    return year - date.fromisoformat(date_of_birth).year


def elective_deferral_cap(caps: sqlite3.Row, base_year: int, target_year: int,
                           date_of_birth: str, include_catchup: bool) -> float:
    base = project_cap(caps["elective_deferral_limit"], base_year, target_year,
                        caps["annual_growth_pct"], caps["increment"])
    if not include_catchup:
        return base

    age = _age_in_year(date_of_birth, target_year)
    if 60 <= age <= 63:
        catchup = project_cap(caps["elective_deferral_catchup_60_63"], base_year, target_year,
                               caps["annual_growth_pct"], caps["increment"])
    elif age >= 50:
        catchup = project_cap(caps["elective_deferral_catchup_50"], base_year, target_year,
                               caps["annual_growth_pct"], caps["increment"])
    else:
        catchup = 0
    return base + catchup


def ira_cap(caps: sqlite3.Row, base_year: int, target_year: int,
            date_of_birth: str, include_catchup: bool) -> float:
    base = project_cap(caps["ira_limit"], base_year, target_year,
                        caps["annual_growth_pct"], caps["increment"])
    if not include_catchup or _age_in_year(date_of_birth, target_year) < 50:
        return base
    catchup = project_cap(caps["ira_catchup_50"], base_year, target_year,
                           caps["annual_growth_pct"], caps["increment"])
    return base + catchup


# --- FERS agency TSP match ----------------------------------------------------

def fers_match_pct(employee_pct: float) -> float:
    """1% automatic + 100% match on the first 3% employee contributes + 50%
    match on the next 2% -- tops out at 5% agency once the employee is at
    or above 5%."""
    match = 0.01  # automatic 1%, regardless of employee election
    match += min(employee_pct, 0.03) * 1.0
    match += max(0.0, min(employee_pct, 0.05) - 0.03) * 0.5
    return match


# --- putting it together for one person/year ---------------------------------

def compute_contributions_for_year(profile, caps: sqlite3.Row, salary: float,
                                    date_of_birth: str, year: int, cap_base_year: int,
                                    include_catchup: bool) -> dict:
    """Desired $ amounts from the profile's percentages/flat amounts, scaled
    down proportionally if they'd exceed that year's projected cap -- or, in
    'max out' mode, the full cap itself, split traditional:Roth by the
    profile's explicit *_roth_share (not inferred from the %/$ fields, which
    a maxed-out person may reasonably leave at 0)."""
    tsp_cap = elective_deferral_cap(caps, cap_base_year, year, date_of_birth, include_catchup)
    if profile["tsp_max"]:
        # TSP is a payroll deduction -- maxing out means nothing without wages.
        tsp_total = tsp_cap if salary > 0 else 0.0
        tsp_roth = tsp_total * profile["tsp_roth_share"]
        tsp_traditional = tsp_total - tsp_roth
    else:
        desired_tsp_traditional = salary * profile["tsp_traditional_pct"]
        desired_tsp_roth = salary * profile["tsp_roth_pct"]
        desired_tsp_total = desired_tsp_traditional + desired_tsp_roth
        if desired_tsp_total > tsp_cap and desired_tsp_total > 0:
            scale = tsp_cap / desired_tsp_total
            tsp_traditional, tsp_roth = desired_tsp_traditional * scale, desired_tsp_roth * scale
        else:
            tsp_traditional, tsp_roth = desired_tsp_traditional, desired_tsp_roth

    # Agency match is on the elected percentage, not the (possibly capped or
    # maxed-out) actual deposit -- in 'max out' mode, infer the equivalent
    # election from what just got contributed.
    employee_pct = (
        (tsp_traditional + tsp_roth) / salary if profile["tsp_max"] and salary > 0
        else profile["tsp_traditional_pct"] + profile["tsp_roth_pct"]
    )
    agency_match = salary * fers_match_pct(employee_pct)

    this_ira_cap = ira_cap(caps, cap_base_year, year, date_of_birth, include_catchup)
    if profile["ira_max"]:
        # IRA contributions aren't tied to this person's own wages (e.g. a
        # spousal Roth IRA funded by the other spouse's earned income).
        ira_roth = this_ira_cap * profile["ira_roth_share"]
        ira_traditional = this_ira_cap - ira_roth
    else:
        desired_ira_total = profile["ira_traditional_annual"] + profile["ira_roth_annual"]
        if desired_ira_total > this_ira_cap and desired_ira_total > 0:
            scale = this_ira_cap / desired_ira_total
            ira_traditional = profile["ira_traditional_annual"] * scale
            ira_roth = profile["ira_roth_annual"] * scale
        else:
            ira_traditional, ira_roth = profile["ira_traditional_annual"], profile["ira_roth_annual"]

    return {
        "tsp_traditional": tsp_traditional,
        "tsp_roth": tsp_roth,
        "agency_match": agency_match,
        "ira_traditional": ira_traditional,
        "ira_roth": ira_roth,
        "taxable": profile["taxable_annual"],
        "tsp_cap_used": tsp_cap,
        "ira_cap_used": this_ira_cap,
    }
