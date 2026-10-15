import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "owl_dashboard.db"


def get_db_path() -> Path:
    return Path(os.environ.get("DB_PATH", DEFAULT_DB_PATH))


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS account_mappings (
            account_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            owl_bucket TEXT NOT NULL DEFAULT 'excluded'
                CHECK (owl_bucket IN ('taxable', 'tax_deferred', 'roth', 'excluded')),
            owner TEXT NOT NULL DEFAULT 'self'
                CHECK (owner IN ('self', 'spouse', 'joint')),
            last_seen_type TEXT,
            last_seen_institution TEXT,
            last_seen_at TEXT NOT NULL,
            needs_review INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
