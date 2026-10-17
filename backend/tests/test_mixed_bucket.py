"""Covers the 'mixed' account bucket (e.g. a TSP with both a traditional and
a Roth balance): the pre-existing-db migration, the update path, and
case_builder.py splitting a mixed account's balance by mixed_roth_fraction."""

from app.db import get_connection, init_db
from app.tiller.mapping import sync_accounts, update_account_mapping

FAKE_ACCOUNTS = [
    {"Account Id": "tsp1", "Account": "TSP", "Last Balance": "$400,000.00",
     "Institution": "TSP", "Type": "401k"},
]


def make_conn(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return conn


def test_migration_is_idempotent_and_adds_the_column(tmp_path):
    conn = make_conn(tmp_path)
    init_db(conn)  # calling init_db again must not error or duplicate columns
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(account_mappings)").fetchall()]
    assert "mixed_roth_fraction" in columns


def test_update_to_mixed_stores_the_fraction(tmp_path):
    conn = make_conn(tmp_path)
    sync_accounts(conn, FAKE_ACCOUNTS)
    update_account_mapping(conn, "tsp1", owl_bucket="mixed", owner="self", mixed_roth_fraction=0.3)

    row = conn.execute("SELECT * FROM account_mappings WHERE account_id = 'tsp1'").fetchone()
    assert row["owl_bucket"] == "mixed"
    assert row["mixed_roth_fraction"] == 0.3
    assert row["needs_review"] == 0


def test_switching_away_from_mixed_clears_the_fraction(tmp_path):
    conn = make_conn(tmp_path)
    sync_accounts(conn, FAKE_ACCOUNTS)
    update_account_mapping(conn, "tsp1", owl_bucket="mixed", owner="self", mixed_roth_fraction=0.3)
    update_account_mapping(conn, "tsp1", owl_bucket="taxable", owner="self")

    row = conn.execute("SELECT * FROM account_mappings WHERE account_id = 'tsp1'").fetchone()
    assert row["owl_bucket"] == "taxable"
    assert row["mixed_roth_fraction"] is None


def test_mixed_account_splits_into_tax_deferred_and_roth_in_case_builder(tmp_path):
    from app.assumptions import household
    from app.owl_integration.case_builder import ScenarioParams, _bucket_balances

    conn = make_conn(tmp_path)
    household.set_household_member(conn, owner="self", display_name="Ben",
                                     date_of_birth="1988-06-01", life_expectancy=92)
    sync_accounts(conn, FAKE_ACCOUNTS)
    update_account_mapping(conn, "tsp1", owl_bucket="mixed", owner="self", mixed_roth_fraction=0.25)

    balances = _bucket_balances(conn, FAKE_ACCOUNTS, ["self"])

    # $400,000 at 25% Roth -> $100,000 Roth / $300,000 tax_deferred, in $k.
    assert balances["self"]["roth"] == 100.0
    assert balances["self"]["tax_deferred"] == 300.0
    assert balances["self"]["taxable"] == 0.0
