from app.db import get_connection, init_db
from app.tiller.mapping import parse_balance, sync_accounts


def make_conn(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return conn


FAKE_ACCOUNTS = [
    {"Account Id": "acct1", "Account": "Checking", "Last Balance": "$1,234.56",
     "Institution": "Bank A", "Type": "Checking"},
    {"Account Id": "acct2", "Account": "Savings", "Last Balance": "$0.00",
     "Institution": "Bank A", "Type": "Savings"},
]


def test_first_sync_flags_everything_new(tmp_path):
    conn = make_conn(tmp_path)
    result = sync_accounts(conn, FAKE_ACCOUNTS)
    assert result == {"new_accounts": ["acct1", "acct2"], "changed_accounts": [], "unchanged_count": 0}

    row = conn.execute("SELECT * FROM account_mappings WHERE account_id = 'acct1'").fetchone()
    assert row["owl_bucket"] == "excluded"
    assert row["needs_review"] == 1


def test_identical_second_sync_is_a_noop(tmp_path):
    conn = make_conn(tmp_path)
    sync_accounts(conn, FAKE_ACCOUNTS)
    result = sync_accounts(conn, FAKE_ACCOUNTS)
    assert result == {"new_accounts": [], "changed_accounts": [], "unchanged_count": 2}


def test_drifted_account_is_reflagged(tmp_path):
    conn = make_conn(tmp_path)
    sync_accounts(conn, FAKE_ACCOUNTS)

    # simulate the user having already reviewed/mapped acct1 before the drift
    conn.execute(
        "UPDATE account_mappings SET owl_bucket = 'taxable', needs_review = 0 WHERE account_id = 'acct1'"
    )
    conn.commit()

    mutated = [dict(FAKE_ACCOUNTS[0], Type="Brokerage"), FAKE_ACCOUNTS[1]]
    result = sync_accounts(conn, mutated)

    assert result["changed_accounts"] == ["acct1"]
    assert result["unchanged_count"] == 1

    row = conn.execute("SELECT * FROM account_mappings WHERE account_id = 'acct1'").fetchone()
    assert row["needs_review"] == 1  # re-flagged despite prior manual mapping
    assert row["owl_bucket"] == "taxable"  # previous bucket choice preserved, not reset
    assert row["last_seen_type"] == "Brokerage"


def test_blank_account_id_row_is_skipped(tmp_path):
    conn = make_conn(tmp_path)
    rows = FAKE_ACCOUNTS + [{"Account Id": "", "Account": "", "Last Balance": "", "Institution": "", "Type": ""}]
    result = sync_accounts(conn, rows)
    assert result["new_accounts"] == ["acct1", "acct2"]


def test_parse_balance_formats():
    assert parse_balance("$81,539.52") == 81539.52
    assert parse_balance("$0.00") == 0.0
    assert parse_balance("($123.45)") == -123.45
    assert parse_balance("") == 0.0
