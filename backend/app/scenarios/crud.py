"""Named scenario snapshots: assumptions + balances + scenario params + the
solve result, frozen at save time so reopening a scenario shows exactly what
was seen then (see case_builder.py / solve.py for why this doesn't need to
recompute to be useful -- it's about freezing a point in time, not avoiding
an expensive solve)."""

import json
import sqlite3
from datetime import datetime, timezone

from app.owl_integration.case_builder import ScenarioParams


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scenario_params_to_dict(scenario: ScenarioParams) -> dict:
    return {
        "separation_date": {k: (v.isoformat() if v else None) for k, v in scenario.separation_date.items()},
        "separation_age": scenario.separation_age,
        "ss_claiming_age": scenario.ss_claiming_age,
        "objective": scenario.objective,
        "bequest": scenario.bequest,
    }


def save_scenario(conn: sqlite3.Connection, name: str, description: str | None, scenario: ScenarioParams,
                   assumptions_fingerprint: dict, pulled_accounts: list[dict], solve_result: dict) -> int:
    now = _now()
    case_params = scenario_params_to_dict(scenario)

    cur = conn.execute(
        """
        INSERT INTO scenarios
            (name, description, separation_date_self, separation_date_spouse,
             assumptions_snapshot, balances_snapshot, case_params_snapshot, solve_result,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            description = excluded.description,
            separation_date_self = excluded.separation_date_self,
            separation_date_spouse = excluded.separation_date_spouse,
            assumptions_snapshot = excluded.assumptions_snapshot,
            balances_snapshot = excluded.balances_snapshot,
            case_params_snapshot = excluded.case_params_snapshot,
            solve_result = excluded.solve_result,
            updated_at = excluded.updated_at
        """,
        (
            name, description,
            case_params["separation_date"].get("self"), case_params["separation_date"].get("spouse"),
            json.dumps(assumptions_fingerprint), json.dumps(pulled_accounts),
            json.dumps(case_params), json.dumps(solve_result),
            now, now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_scenarios(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Summary fields only -- the heavy snapshot columns are left for get_scenario()."""
    return conn.execute(
        "SELECT id, name, description, separation_date_self, separation_date_spouse, created_at, updated_at "
        "FROM scenarios ORDER BY updated_at DESC"
    ).fetchall()


def get_scenario(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM scenarios WHERE name = ?", (name,)).fetchone()


def delete_scenario(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("DELETE FROM scenarios WHERE name = ?", (name,))
    conn.commit()
