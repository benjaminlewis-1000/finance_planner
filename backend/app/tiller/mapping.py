"""
Diffs a fresh Tiller Accounts pull against the stored account_mappings table.

New accounts default to owl_bucket='excluded' + needs_review=1 -- they never
silently start counting toward a solve. An account whose Type, Institution,
or display name changed since we last saw it is re-flagged needs_review
rather than having its existing owl_bucket/owner silently kept, since a
changed Type (e.g. a brokerage account Tiller re-categorizes) may mean the
previous taxable/tax_deferred/roth assignment is now wrong.
"""

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sync_accounts(conn: sqlite3.Connection, pulled_accounts: list[dict]) -> dict:
    now = _now()
    new_accounts = []
    changed_accounts = []
    unchanged_count = 0

    for row in pulled_accounts:
        account_id = row["Account Id"]
        if not account_id:
            continue  # a blank/header-artifact row, not a real account

        display_name = row.get("Account", "")
        seen_type = row.get("Type", "")
        seen_institution = row.get("Institution", "")

        existing = conn.execute(
            "SELECT * FROM account_mappings WHERE account_id = ?", (account_id,)
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO account_mappings
                    (account_id, display_name, owl_bucket, owner,
                     last_seen_type, last_seen_institution, last_seen_at,
                     needs_review, updated_at)
                VALUES (?, ?, 'excluded', 'self', ?, ?, ?, 1, ?)
                """,
                (account_id, display_name, seen_type, seen_institution, now, now),
            )
            new_accounts.append(account_id)
            continue

        drifted = (
            existing["display_name"] != display_name
            or existing["last_seen_type"] != seen_type
            or existing["last_seen_institution"] != seen_institution
        )
        if drifted:
            conn.execute(
                """
                UPDATE account_mappings
                SET display_name = ?, last_seen_type = ?, last_seen_institution = ?,
                    last_seen_at = ?, needs_review = 1, updated_at = ?
                WHERE account_id = ?
                """,
                (display_name, seen_type, seen_institution, now, now, account_id),
            )
            changed_accounts.append(account_id)
        else:
            conn.execute(
                "UPDATE account_mappings SET last_seen_at = ? WHERE account_id = ?",
                (now, account_id),
            )
            unchanged_count += 1

    conn.commit()
    return {
        "new_accounts": new_accounts,
        "changed_accounts": changed_accounts,
        "unchanged_count": unchanged_count,
    }


def update_account_mapping(conn: sqlite3.Connection, account_id: str, owl_bucket: str, owner: str,
                            mixed_roth_fraction: float | None = None) -> None:
    """The settings-UI 'review' action: the user has looked at this account
    and confirmed its bucket/owner, so needs_review clears. mixed_roth_fraction
    is only meaningful (and required by the router) when owl_bucket='mixed';
    stored as NULL otherwise so a stale fraction can't linger after someone
    switches an account away from 'mixed'."""
    conn.execute(
        "UPDATE account_mappings SET owl_bucket = ?, owner = ?, mixed_roth_fraction = ?, "
        "needs_review = 0, updated_at = ? WHERE account_id = ?",
        (owl_bucket, owner, mixed_roth_fraction if owl_bucket == "mixed" else None, _now(), account_id),
    )
    conn.commit()


def parse_balance(raw: str) -> float:
    """Tiller's 'Last Balance' is a formatted currency string, e.g. '$1,234.56'."""
    cleaned = raw.replace("$", "").replace(",", "").strip()
    if not cleaned:
        return 0.0
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    value = float(cleaned)
    return -value if negative else value
