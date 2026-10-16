"""Cheap fingerprint of everything case_builder.py reads besides the scenario
params and live balances -- so solve_cache invalidates when an assumption
changes, not just when balances or separation/claiming ages do."""

import sqlite3


def assumptions_fingerprint(conn: sqlite3.Connection) -> dict:
    def rows(sql: str) -> list[tuple]:
        return [tuple(row) for row in conn.execute(sql).fetchall()]

    return {
        "salary_steps": rows("SELECT owner, effective_date, annual_amount, updated_at FROM salary_steps ORDER BY id"),
        "one_off_events": rows("SELECT owner, event_date, amount, updated_at FROM one_off_events ORDER BY id"),
        "household_members": rows(
            "SELECT owner, display_name, date_of_birth, life_expectancy, updated_at FROM household_members ORDER BY owner"
        ),
        "pension_profiles": rows(
            "SELECT owner, service_start_date, high_3_salary, mra_age, survivor_benefit_fraction, updated_at "
            "FROM pension_profiles ORDER BY owner"
        ),
        "current_pension_params": rows(
            "SELECT id, effective_date, multiplier_standard, multiplier_age62_20yos, "
            "mra_plus_10_reduction_per_year_under_62 FROM pension_params ORDER BY effective_date DESC, id DESC LIMIT 1"
        ),
        "social_security_profiles": rows(
            "SELECT owner, estimated_pia_monthly, full_retirement_age, updated_at FROM social_security_profiles ORDER BY owner"
        ),
    }
