"""Salary growth projection: current_salary + annual_increase_pct, in place of
hand-entering every future salary_steps row. current_salary is this calendar
year's rate; the raise lands every Jan 1 after that (matches federal pay
raises), so project_salary(year) is simple compounding from the current year.

Feeds both case_builder.py's HFP wage schedule and the pension page's high-3
preview -- compute_high_3 assumes a monotonically increasing projection, so
the highest 3 consecutive years are just the 3 full years immediately before
separation."""

import sqlite3
from datetime import date, datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_salary_profile(conn: sqlite3.Connection, owner: str, current_salary: float,
                        annual_increase_pct: float = 0) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO salary_profiles (owner, current_salary, annual_increase_pct, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(owner) DO UPDATE SET
            current_salary = excluded.current_salary,
            annual_increase_pct = excluded.annual_increase_pct,
            updated_at = excluded.updated_at
        """,
        (owner, current_salary, annual_increase_pct, now, now),
    )
    conn.commit()


def get_salary_profile(conn: sqlite3.Connection, owner: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM salary_profiles WHERE owner = ?", (owner,)).fetchone()


def delete_salary_profile(conn: sqlite3.Connection, owner: str) -> None:
    conn.execute("DELETE FROM salary_profiles WHERE owner = ?", (owner,))
    conn.commit()


def project_salary(profile: sqlite3.Row, year: int) -> float:
    current_year = date.today().year
    return profile["current_salary"] * (1 + profile["annual_increase_pct"]) ** (year - current_year)


def compute_high_3(profile: sqlite3.Row, separation_year: int) -> float:
    """Average projected salary over the 3 full years immediately before
    separation_year (separation_year-3, -2, -1) -- not the separation year
    itself, since it's typically a partial year of service."""
    years = [separation_year - 3, separation_year - 2, separation_year - 1]
    return sum(project_salary(profile, year) for year in years) / len(years)
