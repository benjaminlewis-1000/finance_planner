"""Per-person identity needed to construct an owlplanner Plan: name, date of
birth, life expectancy. Spouse is optional -- a household with only a 'self'
row builds a single-person Plan."""

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_household_member(conn: sqlite3.Connection, owner: str, display_name: str,
                          date_of_birth: str, life_expectancy: int = 92) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO household_members
            (owner, display_name, date_of_birth, life_expectancy, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner) DO UPDATE SET
            display_name = excluded.display_name,
            date_of_birth = excluded.date_of_birth,
            life_expectancy = excluded.life_expectancy,
            updated_at = excluded.updated_at
        """,
        (owner, display_name, date_of_birth, life_expectancy, now, now),
    )
    conn.commit()


def get_household_member(conn: sqlite3.Connection, owner: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM household_members WHERE owner = ?", (owner,)).fetchone()


def list_household_members(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Ordered 'self' first, then 'spouse', matching the order case_builder.py
    feeds Owl's per-individual arrays in."""
    return conn.execute(
        "SELECT * FROM household_members ORDER BY CASE owner WHEN 'self' THEN 0 ELSE 1 END"
    ).fetchall()
