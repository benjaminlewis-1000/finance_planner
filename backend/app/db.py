import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "owl_dashboard.db"


def get_db_path() -> Path:
    return Path(os.environ.get("DB_PATH", DEFAULT_DB_PATH))


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI's sync dependency functions run in a
    # threadpool worker, not the thread that created the connection. Safe here
    # -- this is a single-user app with no concurrent writers to race.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """FastAPI dependency -- a connection per request, closed afterward.
    Tests override this via app.dependency_overrides to point at a tmp db."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        -- 'mixed' is for accounts split across tax treatments (e.g. a TSP
        -- with both a traditional/tax_deferred balance and a Roth balance)
        -- -- mixed_roth_fraction (0-1) holds the Roth share; the rest is
        -- tax_deferred. Only meaningful when owl_bucket = 'mixed'.
        CREATE TABLE IF NOT EXISTS account_mappings (
            account_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            owl_bucket TEXT NOT NULL DEFAULT 'excluded'
                CHECK (owl_bucket IN ('taxable', 'tax_deferred', 'roth', 'mixed', 'excluded')),
            owner TEXT NOT NULL DEFAULT 'self'
                CHECK (owner IN ('self', 'spouse', 'joint')),
            mixed_roth_fraction REAL,
            last_seen_type TEXT,
            last_seen_institution TEXT,
            last_seen_at TEXT NOT NULL,
            needs_review INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS salary_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT NOT NULL CHECK (owner IN ('self', 'spouse')),
            effective_date TEXT NOT NULL,
            annual_amount REAL NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS expense_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            effective_date TEXT NOT NULL,
            category TEXT NOT NULL,
            annual_amount_delta REAL NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS one_off_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date TEXT NOT NULL,
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT,
            owner TEXT NOT NULL DEFAULT 'joint' CHECK (owner IN ('self', 'spouse', 'joint')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        -- Versioned FERS formula knobs, editable rather than hardcoded, since
        -- the law can change. effective_date lets a past scenario snapshot
        -- keep referring to the params that were true when it was made.
        CREATE TABLE IF NOT EXISTS pension_params (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            effective_date TEXT NOT NULL,
            multiplier_standard REAL NOT NULL DEFAULT 0.01,
            multiplier_age62_20yos REAL NOT NULL DEFAULT 0.011,
            mra_plus_10_reduction_per_year_under_62 REAL NOT NULL DEFAULT 0.05,
            note TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pension_profiles (
            owner TEXT PRIMARY KEY CHECK (owner IN ('self', 'spouse')),
            service_start_date TEXT NOT NULL,
            high_3_salary REAL,
            mra_age REAL NOT NULL DEFAULT 57,
            survivor_benefit_fraction REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS household_members (
            owner TEXT PRIMARY KEY CHECK (owner IN ('self', 'spouse')),
            display_name TEXT NOT NULL,
            date_of_birth TEXT NOT NULL,
            life_expectancy INTEGER NOT NULL DEFAULT 92,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS social_security_profiles (
            owner TEXT PRIMARY KEY CHECK (owner IN ('self', 'spouse')),
            estimated_pia_monthly REAL NOT NULL,
            full_retirement_age REAL NOT NULL DEFAULT 67,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scenarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            separation_date_self TEXT,
            separation_date_spouse TEXT,
            assumptions_snapshot TEXT NOT NULL,
            balances_snapshot TEXT NOT NULL,
            case_params_snapshot TEXT,
            toml_snapshot TEXT,
            solve_result TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS solve_cache (
            cache_key TEXT PRIMARY KEY,
            result TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    _migrate_account_mappings_for_mixed_bucket(conn)


def _migrate_account_mappings_for_mixed_bucket(conn: sqlite3.Connection) -> None:
    """A pre-existing account_mappings table (from before 'mixed' existed)
    has a CHECK constraint that doesn't allow it and no mixed_roth_fraction
    column -- SQLite can't ALTER a CHECK constraint in place, so this rebuilds
    the table under the new schema, copying every existing row forward.
    Idempotent: skipped once mixed_roth_fraction already exists."""
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(account_mappings)").fetchall()]
    if "mixed_roth_fraction" in columns:
        return

    conn.executescript(
        """
        ALTER TABLE account_mappings RENAME TO account_mappings_pre_mixed;

        CREATE TABLE account_mappings (
            account_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            owl_bucket TEXT NOT NULL DEFAULT 'excluded'
                CHECK (owl_bucket IN ('taxable', 'tax_deferred', 'roth', 'mixed', 'excluded')),
            owner TEXT NOT NULL DEFAULT 'self'
                CHECK (owner IN ('self', 'spouse', 'joint')),
            mixed_roth_fraction REAL,
            last_seen_type TEXT,
            last_seen_institution TEXT,
            last_seen_at TEXT NOT NULL,
            needs_review INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );

        INSERT INTO account_mappings
            (account_id, display_name, owl_bucket, owner, last_seen_type,
             last_seen_institution, last_seen_at, needs_review, updated_at)
        SELECT account_id, display_name, owl_bucket, owner, last_seen_type,
               last_seen_institution, last_seen_at, needs_review, updated_at
        FROM account_mappings_pre_mixed;

        DROP TABLE account_mappings_pre_mixed;
        """
    )
    conn.commit()
