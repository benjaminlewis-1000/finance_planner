"""The tsp_max/ira_max columns were added after contribution_profiles first
shipped -- confirms a pre-existing table (no such columns) gets migrated
in place, idempotently, without losing existing rows."""

from app.db import (
    _migrate_contribution_profiles_add_max_columns,
    _migrate_contribution_profiles_add_roth_share_columns,
    get_connection,
    init_db,
)


def test_migration_adds_columns_to_a_pre_existing_table_and_keeps_data(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    conn.executescript(
        """
        CREATE TABLE contribution_profiles (
            owner TEXT PRIMARY KEY,
            tsp_traditional_pct REAL NOT NULL DEFAULT 0,
            tsp_roth_pct REAL NOT NULL DEFAULT 0,
            ira_traditional_annual REAL NOT NULL DEFAULT 0,
            ira_roth_annual REAL NOT NULL DEFAULT 0,
            taxable_annual REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO contribution_profiles
            (owner, tsp_traditional_pct, tsp_roth_pct, ira_traditional_annual,
             ira_roth_annual, taxable_annual, created_at, updated_at)
        VALUES ('self', 0.05, 0.05, 0, 0, 0, '2026-01-01', '2026-01-01');
        """
    )
    conn.commit()

    _migrate_contribution_profiles_add_max_columns(conn)
    _migrate_contribution_profiles_add_max_columns(conn)  # idempotent

    columns = [row["name"] for row in conn.execute("PRAGMA table_info(contribution_profiles)").fetchall()]
    assert "tsp_max" in columns
    assert "ira_max" in columns

    row = conn.execute("SELECT * FROM contribution_profiles WHERE owner = 'self'").fetchone()
    assert row["tsp_traditional_pct"] == 0.05  # existing data preserved
    assert row["tsp_max"] == 0  # new column defaults to "not maxed"


def test_init_db_runs_the_migration_on_a_fresh_db(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(contribution_profiles)").fetchall()]
    assert "tsp_max" in columns
    assert "ira_max" in columns
    assert "tsp_roth_share" in columns
    assert "ira_roth_share" in columns


def test_roth_share_migration_runs_even_when_max_columns_already_exist(tmp_path):
    # The exact real-world scenario: a DB already migrated for tsp_max/
    # ira_max (an earlier deploy) but not yet for *_roth_share -- the max
    # migration's own early-return must not skip this one.
    conn = get_connection(tmp_path / "test.db")
    conn.executescript(
        """
        CREATE TABLE contribution_profiles (
            owner TEXT PRIMARY KEY,
            tsp_traditional_pct REAL NOT NULL DEFAULT 0,
            tsp_roth_pct REAL NOT NULL DEFAULT 0,
            tsp_max INTEGER NOT NULL DEFAULT 0,
            ira_traditional_annual REAL NOT NULL DEFAULT 0,
            ira_roth_annual REAL NOT NULL DEFAULT 0,
            ira_max INTEGER NOT NULL DEFAULT 0,
            taxable_annual REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO contribution_profiles
            (owner, tsp_traditional_pct, tsp_roth_pct, tsp_max, ira_traditional_annual,
             ira_roth_annual, ira_max, taxable_annual, created_at, updated_at)
        VALUES ('self', 0, 0, 1, 0, 7000, 1, 0, '2026-01-01', '2026-01-01');
        """
    )
    conn.commit()

    # This call's early-return (tsp_max already present) must not prevent
    # init_db from still reaching the roth_share migration.
    _migrate_contribution_profiles_add_max_columns(conn)
    _migrate_contribution_profiles_add_roth_share_columns(conn)

    columns = [row["name"] for row in conn.execute("PRAGMA table_info(contribution_profiles)").fetchall()]
    assert "tsp_roth_share" in columns
    assert "ira_roth_share" in columns
    row = conn.execute("SELECT * FROM contribution_profiles WHERE owner = 'self'").fetchone()
    assert row["ira_roth_annual"] == 7000  # existing data preserved
