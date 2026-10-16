from sqlite3 import Connection

from fastapi import APIRouter, Depends, HTTPException

from app.db import get_db
from app.owl_integration.fingerprint import assumptions_fingerprint
from app.owl_integration.solve import solve_scenario
from app.routers.common import ScenarioRequest
from app.tiller.sheets_client import TillerAuthError, TillerSchemaError, fetch_accounts

router = APIRouter(tags=["solve"])


@router.post("/solve")
def solve(body: ScenarioRequest, conn: Connection = Depends(get_db)):
    """Ad-hoc solve against current assumptions + a live Tiller pull -- doesn't
    persist anything. Use /scenarios to save a named snapshot instead."""
    try:
        pulled_accounts = fetch_accounts(body.spreadsheet_id)
    except TillerAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except TillerSchemaError as e:
        raise HTTPException(status_code=400, detail=str(e))

    scenario = body.to_scenario_params()
    fingerprint = assumptions_fingerprint(conn)
    return solve_scenario(conn, pulled_accounts, scenario, fingerprint, force_recalculate=body.force_recalculate)
