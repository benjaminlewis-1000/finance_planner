"""Exercises every router through FastAPI's TestClient, with a tmp-path
SQLite db swapped in via dependency_overrides (no live Tiller/Google calls --
fetch_accounts is monkeypatched per-test where a route needs it)."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db import get_connection, get_db, init_db
from app.main import app

FAKE_ACCOUNTS = [
    {"Account Id": "acct1", "Account": "Brokerage", "Last Balance": "$80,000.00",
     "Institution": "Bank A", "Type": "Brokerage"},
    {"Account Id": "acct2", "Account": "401k", "Last Balance": "$320,000.00",
     "Institution": "Bank A", "Type": "401k"},
    {"Account Id": "acct3", "Account": "Roth IRA", "Last Balance": "$135,000.00",
     "Institution": "Bank A", "Type": "Roth IRA"},
]


@pytest.fixture
def client(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)

    def override_get_db():
        yield conn

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), conn
    app.dependency_overrides.clear()
    conn.close()


def seed_household(conn):
    client_conn = conn
    client_conn.execute(
        "INSERT INTO household_members (owner, display_name, date_of_birth, life_expectancy, created_at, updated_at) "
        "VALUES ('self', 'Ben', '1988-06-01', 92, datetime('now'), datetime('now'))"
    )
    client_conn.commit()


# --- assumptions -------------------------------------------------------------

def test_salary_step_crud_via_api(client):
    api, conn = client
    resp = api.post("/assumptions/salary-steps", json={
        "owner": "self", "effective_date": "2026-01-01", "annual_amount": 140000, "note": "raise",
    })
    assert resp.status_code == 200
    step_id = resp.json()["id"]

    resp = api.get("/assumptions/salary-steps", params={"owner": "self"})
    assert len(resp.json()) == 1

    resp = api.delete(f"/assumptions/salary-steps/{step_id}")
    assert resp.status_code == 200
    assert api.get("/assumptions/salary-steps").json() == []


def test_pension_profile_round_trip(client):
    api, conn = client
    resp = api.put("/assumptions/pension-profile/self", json={
        "service_start_date": "2016-12-11", "high_3_salary": 145000, "mra_age": 57,
        "survivor_benefit_fraction": 0.5,
    })
    assert resp.status_code == 200
    assert resp.json()["high_3_salary"] == 145000

    resp = api.get("/assumptions/pension-profile/self")
    assert resp.json()["service_start_date"] == "2016-12-11"


def test_pension_profile_404_when_unset(client):
    api, conn = client
    resp = api.get("/assumptions/pension-profile/spouse")
    assert resp.status_code == 404


def test_pension_profile_delete_turns_pension_off(client):
    api, conn = client
    api.put("/assumptions/pension-profile/spouse", json={
        "service_start_date": "2016-12-11", "high_3_salary": 100000,
    })
    assert api.get("/assumptions/pension-profile/spouse").status_code == 200

    resp = api.delete("/assumptions/pension-profile/spouse")
    assert resp.status_code == 200
    assert api.get("/assumptions/pension-profile/spouse").status_code == 404


def test_salary_profile_round_trip(client):
    api, conn = client
    resp = api.put("/assumptions/salary-profile/self", json={
        "current_salary": 140000, "annual_increase_pct": 0.03,
    })
    assert resp.status_code == 200
    assert resp.json()["current_salary"] == 140000

    resp = api.get("/assumptions/salary-profile/self")
    assert resp.json()["annual_increase_pct"] == 0.03


def test_salary_profile_404_when_unset(client):
    api, conn = client
    resp = api.get("/assumptions/salary-profile/spouse")
    assert resp.status_code == 404


def test_pension_preview_uses_salary_profile_for_high_3(client):
    api, conn = client
    seed_household(conn)
    api.put("/assumptions/pension-profile/self", json={
        "service_start_date": "2016-12-11", "mra_age": 57,  # no high_3_salary override
    })
    api.put("/assumptions/salary-profile/self", json={
        "current_salary": 100000, "annual_increase_pct": 0.03,
    })

    resp = api.get("/assumptions/pension-preview/self", params={"separation_date": "2050-06-01"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_pension"] is True
    assert body["separation_age"] == pytest.approx(62.0, abs=0.01)
    assert body["high_3_salary_used"] > 100000  # projected forward from today, not today's raw rate


def test_pension_preview_no_pension(client):
    api, conn = client
    seed_household(conn)
    resp = api.get("/assumptions/pension-preview/self")
    assert resp.json() == {"has_pension": False}


def test_pension_preview_400_without_high_3_or_salary_profile(client):
    api, conn = client
    seed_household(conn)
    api.put("/assumptions/pension-profile/self", json={"service_start_date": "2016-12-11"})
    resp = api.get("/assumptions/pension-preview/self")
    assert resp.status_code == 400


def test_household_member_round_trip(client):
    api, conn = client
    resp = api.put("/assumptions/household-member/self", json={
        "display_name": "Ben", "date_of_birth": "1988-06-01", "life_expectancy": 92,
    })
    assert resp.status_code == 200

    resp = api.get("/assumptions/household-members")
    assert len(resp.json()) == 1
    assert resp.json()[0]["display_name"] == "Ben"


# --- tiller -------------------------------------------------------------------

def test_tiller_sync_and_review_flow(client):
    api, conn = client
    with patch("app.routers.tiller.fetch_accounts", return_value=FAKE_ACCOUNTS):
        resp = api.post("/tiller/accounts/sync", params={"spreadsheet_id": "fake-id"})
    assert resp.status_code == 200
    assert resp.json()["new_accounts"] == ["acct1", "acct2", "acct3"]

    resp = api.get("/tiller/accounts")
    assert len(resp.json()) == 3
    assert all(row["needs_review"] == 1 for row in resp.json())

    resp = api.patch("/tiller/accounts/acct1", json={"owl_bucket": "taxable", "owner": "self"})
    assert resp.status_code == 200
    assert resp.json()["needs_review"] == 0
    assert resp.json()["owl_bucket"] == "taxable"


def test_tiller_patch_unknown_account_404s(client):
    api, conn = client
    resp = api.patch("/tiller/accounts/does-not-exist", json={"owl_bucket": "taxable", "owner": "self"})
    assert resp.status_code == 404


# --- solve / scenarios (real owlplanner solve, Tiller pull mocked) ----------

def _prep_solvable_household(conn):
    seed_household(conn)
    from app.tiller.mapping import sync_accounts
    sync_accounts(conn, FAKE_ACCOUNTS)
    conn.execute("UPDATE account_mappings SET owl_bucket='taxable', needs_review=0 WHERE account_id='acct1'")
    conn.execute("UPDATE account_mappings SET owl_bucket='tax_deferred', needs_review=0 WHERE account_id='acct2'")
    conn.execute("UPDATE account_mappings SET owl_bucket='roth', needs_review=0 WHERE account_id='acct3'")
    conn.commit()
    conn.execute(
        "INSERT INTO salary_steps (owner, effective_date, annual_amount, created_at, updated_at) "
        "VALUES ('self', ?, 140000, datetime('now'), datetime('now'))",
        (date.today().isoformat(),),
    )
    conn.commit()


def test_solve_endpoint_runs_a_real_solve(client):
    api, conn = client
    _prep_solvable_household(conn)
    separation_date = (date.today() + timedelta(days=3650)).isoformat()

    with patch("app.routers.solve.fetch_accounts", return_value=FAKE_ACCOUNTS):
        resp = api.post("/solve", json={
            "spreadsheet_id": "fake-id",
            "separation_age": {"self": 57},
            "separation_date": {"self": separation_date},
            "ss_claiming_age": {"self": 67},
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["case_status"] == "solved"
    assert body["from_cache"] is False


def test_scenarios_save_list_get_delete(client):
    api, conn = client
    _prep_solvable_household(conn)
    separation_date = (date.today() + timedelta(days=3650)).isoformat()

    with patch("app.routers.scenarios.fetch_accounts", return_value=FAKE_ACCOUNTS):
        resp = api.post("/scenarios", json={
            "name": "retire-at-57",
            "description": "MRA retirement",
            "spreadsheet_id": "fake-id",
            "separation_age": {"self": 57},
            "separation_date": {"self": separation_date},
            "ss_claiming_age": {"self": 67},
        })
    assert resp.status_code == 200

    resp = api.get("/scenarios")
    assert len(resp.json()) == 1
    assert resp.json()[0]["name"] == "retire-at-57"

    resp = api.get("/scenarios/retire-at-57")
    assert resp.status_code == 200
    assert resp.json()["solve_result"]["case_status"] == "solved"

    resp = api.delete("/scenarios/retire-at-57")
    assert resp.status_code == 200
    assert api.get("/scenarios").json() == []
