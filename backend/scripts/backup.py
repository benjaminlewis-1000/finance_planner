"""
Nightly backup of this project's SQLite DB and OAuth secrets, with
daily/weekly retention. Meant to run from HOST cron (see
config_files/cronjob_backup.sh), not inside the backend container --
data/ and oauth/ are bind-mounted onto the host, so this never needs
`docker exec`.

Pattern mirrors /home/benjamin/docker_via_tunnels/location_track/backup_database.py:
sqlite3's backup API (safe to run against the live DB while the backend has
it open), zstd compression, daily dir pruned to DAILY_RETENTION, a Sunday
copy promoted into weekly/ pruned to WEEKLY_RETENTION.

OAuth secrets (client_secret.json, token_cache/token.json) are small text
files -- copied as-is into the same daily/weekly snapshot, no compression
needed. Losing these means re-running authorize_tiller.py, but backing them
up avoids that hassle after a disk failure.

To restore a given day's DB snapshot:
    zstd -d /mnt/fast_storage/backups/finance_planner/daily/owl_dashboard_YYYY-MM-DD.sqlite.zst \
        -o data/owl_dashboard.db
"""

import datetime
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "owl_dashboard.db"
OAUTH_DIR = REPO_ROOT / "oauth"

BACKUP_DIR = Path("/mnt/fast_storage/backups/finance_planner")
DAILY_RETENTION = 7
WEEKLY_RETENTION = 4
WEEKLY_PROMOTION_WEEKDAY = 6  # Sunday (datetime.weekday(): Mon=0 .. Sun=6)


def _snapshot_db_to_tmp(tmp_path: Path) -> None:
    source = sqlite3.connect(DB_PATH)
    dest = sqlite3.connect(tmp_path)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def _compress(tmp_path: Path, dest_path: Path) -> None:
    subprocess.run(["zstd", "-f", str(tmp_path), "-o", str(dest_path)], check=True)


def _prune(directory: Path, prefix: str, keep: int) -> None:
    files = sorted(f for f in os.listdir(directory) if f.startswith(prefix))
    for stale in files[:-keep] if len(files) > keep else []:
        os.remove(directory / stale)


def _backup_db(date_str: str, daily_dir: Path, weekly_dir: Path, is_weekly_day: bool) -> None:
    if not DB_PATH.exists():
        print(f"No DB at {DB_PATH} yet -- skipping DB backup.")
        return

    filename = f"owl_dashboard_{date_str}.sqlite.zst"
    daily_path = daily_dir / filename
    tmp_path = BACKUP_DIR / f".tmp_{filename}.sqlite"
    try:
        _snapshot_db_to_tmp(tmp_path)
        _compress(tmp_path, daily_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    if is_weekly_day:
        shutil.copy2(daily_path, weekly_dir / filename)

    _prune(daily_dir, "owl_dashboard_", DAILY_RETENTION)
    _prune(weekly_dir, "owl_dashboard_", WEEKLY_RETENTION)
    print(f"DB backup complete: {daily_path}")


def _backup_oauth(date_str: str, daily_dir: Path, weekly_dir: Path, is_weekly_day: bool) -> None:
    client_secret = OAUTH_DIR / "client_secret.json"
    token = OAUTH_DIR / "token_cache" / "token.json"
    sources = [p for p in (client_secret, token) if p.exists()]
    if not sources:
        print(f"No OAuth secrets found under {OAUTH_DIR} yet -- skipping secrets backup.")
        return

    dest_subdir = f"oauth_{date_str}"
    (daily_dir / dest_subdir).mkdir(exist_ok=True)
    for src in sources:
        shutil.copy2(src, daily_dir / dest_subdir / src.name)

    if is_weekly_day:
        shutil.copytree(daily_dir / dest_subdir, weekly_dir / dest_subdir, dirs_exist_ok=True)

    _prune(daily_dir, "oauth_", DAILY_RETENTION)
    _prune(weekly_dir, "oauth_", WEEKLY_RETENTION)
    print(f"Secrets backup complete: {daily_dir / dest_subdir}")


def main():
    daily_dir = BACKUP_DIR / "daily"
    weekly_dir = BACKUP_DIR / "weekly"
    daily_dir.mkdir(parents=True, exist_ok=True)
    weekly_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.datetime.now(datetime.timezone.utc)
    date_str = today.strftime("%Y-%m-%d")
    is_weekly_day = today.weekday() == WEEKLY_PROMOTION_WEEKDAY

    _backup_db(date_str, daily_dir, weekly_dir, is_weekly_day)
    _backup_oauth(date_str, daily_dir, weekly_dir, is_weekly_day)


if __name__ == "__main__":
    main()
