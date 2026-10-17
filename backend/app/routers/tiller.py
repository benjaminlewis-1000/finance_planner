from sqlite3 import Connection
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_db
from app.tiller.mapping import sync_accounts, update_account_mapping
from app.tiller.sheets_client import TillerAuthError, TillerSchemaError, fetch_accounts, load_credentials

router = APIRouter(prefix="/tiller", tags=["tiller"])


@router.get("/auth/status")
def auth_status():
    """Backs the settings page's OAuth connect/status tab."""
    try:
        load_credentials()
        return {"authenticated": True}
    except TillerAuthError as e:
        return {"authenticated": False, "message": str(e)}


@router.post("/accounts/sync")
def sync(spreadsheet_id: str, conn: Connection = Depends(get_db)):
    """Pulls the live Tiller Accounts tab and diffs it against account_mappings --
    the settings page's 'review new/changed accounts' panel is built from this."""
    try:
        pulled_accounts = fetch_accounts(spreadsheet_id)
    except TillerAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except TillerSchemaError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return sync_accounts(conn, pulled_accounts)


@router.get("/accounts")
def list_accounts(conn: Connection = Depends(get_db)):
    rows = conn.execute("SELECT * FROM account_mappings ORDER BY display_name").fetchall()
    return [dict(row) for row in rows]


class AccountMappingUpdate(BaseModel):
    owl_bucket: Literal["taxable", "tax_deferred", "roth", "mixed", "excluded"]
    owner: Literal["self", "spouse", "joint"]
    mixed_roth_fraction: float | None = None  # required when owl_bucket == 'mixed'


@router.patch("/accounts/{account_id}")
def update_mapping(account_id: str, body: AccountMappingUpdate, conn: Connection = Depends(get_db)):
    existing = conn.execute("SELECT 1 FROM account_mappings WHERE account_id = ?", (account_id,)).fetchone()
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Unknown account_id '{account_id}'")

    if body.owl_bucket == "mixed":
        if body.mixed_roth_fraction is None:
            raise HTTPException(status_code=400, detail="mixed_roth_fraction is required when owl_bucket='mixed'")
        if not 0 <= body.mixed_roth_fraction <= 1:
            raise HTTPException(status_code=400, detail="mixed_roth_fraction must be between 0 and 1")

    update_account_mapping(conn, account_id, body.owl_bucket, body.owner, body.mixed_roth_fraction)
    row = conn.execute("SELECT * FROM account_mappings WHERE account_id = ?", (account_id,)).fetchone()
    return dict(row)
