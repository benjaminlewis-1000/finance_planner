"""
Social Security claiming-age adjustment.

Mirrors pension.py's shape: a per-person profile (PIA at full retirement
age, in today's dollars) stored as a setting, and a scenario-supplied
claiming age fed through the standard SSA early/delayed adjustment to
produce the flat monthly benefit owlplanner's setSocialSecurity() expects.

SSA adjustment rules (unchanged federal law, not expected to shift the way
FERS multipliers might -- so unlike pension_params this isn't stored as an
editable settings row, just the two well-known constants below):
- Early claiming (62 up to FRA): -5/9 of 1% per month for the first 36
  months early, then -5/12 of 1% per month beyond that.
- Delayed claiming (FRA up to 70): +2/3 of 1% per month (8%/year).
"""

import sqlite3
from datetime import datetime, timezone

EARLY_REDUCTION_PER_MONTH_FIRST_36 = 5 / 9 / 100
EARLY_REDUCTION_PER_MONTH_BEYOND_36 = 5 / 12 / 100
DELAYED_CREDIT_PER_MONTH = 2 / 3 / 100
MAX_CLAIMING_AGE = 70


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_social_security_profile(conn: sqlite3.Connection, owner: str, estimated_pia_monthly: float,
                                 full_retirement_age: float = 67) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO social_security_profiles
            (owner, estimated_pia_monthly, full_retirement_age, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(owner) DO UPDATE SET
            estimated_pia_monthly = excluded.estimated_pia_monthly,
            full_retirement_age = excluded.full_retirement_age,
            updated_at = excluded.updated_at
        """,
        (owner, estimated_pia_monthly, full_retirement_age, now, now),
    )
    conn.commit()


def get_social_security_profile(conn: sqlite3.Connection, owner: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM social_security_profiles WHERE owner = ?", (owner,)
    ).fetchone()


def compute_social_security_benefit(pia_monthly: float, full_retirement_age: float,
                                     claiming_age: float) -> dict:
    """Adjusts a full-retirement-age PIA for an earlier or later claiming age."""
    claiming_age = min(claiming_age, MAX_CLAIMING_AGE)
    months_diff = round((claiming_age - full_retirement_age) * 12)

    if months_diff < 0:
        months_early = -months_diff
        reduction = (
            min(months_early, 36) * EARLY_REDUCTION_PER_MONTH_FIRST_36
            + max(months_early - 36, 0) * EARLY_REDUCTION_PER_MONTH_BEYOND_36
        )
        adjustment_fraction = -reduction
    elif months_diff > 0:
        adjustment_fraction = months_diff * DELAYED_CREDIT_PER_MONTH
    else:
        adjustment_fraction = 0.0

    monthly_benefit = pia_monthly * (1 + adjustment_fraction)

    return {
        "claiming_age": claiming_age,
        "adjustment_fraction": adjustment_fraction,
        "monthly_benefit": monthly_benefit,
        "annual_benefit": monthly_benefit * 12,
    }
