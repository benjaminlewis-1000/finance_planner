"""CRUD for the three forward-looking assumption types: salary steps,
expense changes, and one-off events. Plain sqlite3, matching tiller/mapping.py's
style -- no ORM needed for tables this simple."""

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- salary steps ---------------------------------------------------------

def add_salary_step(conn: sqlite3.Connection, owner: str, effective_date: str,
                     annual_amount: float, note: str | None = None) -> int:
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO salary_steps (owner, effective_date, annual_amount, note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (owner, effective_date, annual_amount, note, now, now),
    )
    conn.commit()
    return cur.lastrowid


def list_salary_steps(conn: sqlite3.Connection, owner: str | None = None) -> list[sqlite3.Row]:
    if owner:
        return conn.execute(
            "SELECT * FROM salary_steps WHERE owner = ? ORDER BY effective_date", (owner,)
        ).fetchall()
    return conn.execute("SELECT * FROM salary_steps ORDER BY owner, effective_date").fetchall()


def delete_salary_step(conn: sqlite3.Connection, step_id: int) -> None:
    conn.execute("DELETE FROM salary_steps WHERE id = ?", (step_id,))
    conn.commit()


# --- expense changes -------------------------------------------------------

def add_expense_change(conn: sqlite3.Connection, effective_date: str, category: str,
                        annual_amount_delta: float, note: str | None = None) -> int:
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO expense_changes
            (effective_date, category, annual_amount_delta, note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (effective_date, category, annual_amount_delta, note, now, now),
    )
    conn.commit()
    return cur.lastrowid


def list_expense_changes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM expense_changes ORDER BY effective_date").fetchall()


def delete_expense_change(conn: sqlite3.Connection, change_id: int) -> None:
    conn.execute("DELETE FROM expense_changes WHERE id = ?", (change_id,))
    conn.commit()


# --- one-off events ---------------------------------------------------------

def add_one_off_event(conn: sqlite3.Connection, event_date: str, description: str,
                       amount: float, category: str | None = None, owner: str = "joint") -> int:
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO one_off_events
            (event_date, description, amount, category, owner, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (event_date, description, amount, category, owner, now, now),
    )
    conn.commit()
    return cur.lastrowid


def list_one_off_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM one_off_events ORDER BY event_date").fetchall()


def delete_one_off_event(conn: sqlite3.Connection, event_id: int) -> None:
    conn.execute("DELETE FROM one_off_events WHERE id = ?", (event_id,))
    conn.commit()
