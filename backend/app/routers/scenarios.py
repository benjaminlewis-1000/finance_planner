import json
from sqlite3 import Connection

from fastapi import APIRouter, Depends, HTTPException

from app.db import get_db
from app.owl_integration.fingerprint import assumptions_fingerprint
from app.owl_integration.solve import solve_scenario
from app.routers.common import SaveScenarioRequest
from app.scenarios import crud
from app.tiller.sheets_client import TillerAuthError, TillerSchemaError, fetch_accounts

router = APIRouter(prefix="/scenarios", tags=["scenarios"])

SNAPSHOT_FIELDS = ("assumptions_snapshot", "balances_snapshot", "case_params_snapshot", "solve_result")


@router.post("")
def create_scenario(body: SaveScenarioRequest, conn: Connection = Depends(get_db)):
    """Runs a real solve and saves it as a named, reloadable snapshot."""
    try:
        pulled_accounts = fetch_accounts(body.spreadsheet_id)
    except TillerAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except TillerSchemaError as e:
        raise HTTPException(status_code=400, detail=str(e))

    scenario = body.to_scenario_params()
    fingerprint = assumptions_fingerprint(conn)
    result = solve_scenario(conn, pulled_accounts, scenario, fingerprint, force_recalculate=body.force_recalculate)

    crud.save_scenario(conn, body.name, body.description, scenario, fingerprint, pulled_accounts, result)
    return {"name": body.name, "result": result}


@router.get("")
def list_scenarios(conn: Connection = Depends(get_db)):
    return [dict(row) for row in crud.list_scenarios(conn)]


@router.get("/{name}")
def get_scenario(name: str, conn: Connection = Depends(get_db)):
    row = crud.get_scenario(conn, name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No scenario named '{name}'")

    data = dict(row)
    for field in SNAPSHOT_FIELDS:
        if data.get(field):
            data[field] = json.loads(data[field])
    return data


@router.delete("/{name}")
def delete_scenario(name: str, conn: Connection = Depends(get_db)):
    crud.delete_scenario(conn, name)
    return {"deleted": name}
