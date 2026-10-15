#!/bin/sh
# Host cron entry point -- see crontab (added via `crontab -e`, not tracked
# here). Own lock, separate from other backup jobs on this host.
flock -n /tmp/backup_finance_planner.lock -c \
    "/usr/bin/python3 /home/benjamin/git_repos/finance_projections/backend/scripts/backup.py >> /mnt/fast_storage/backups/finance_planner/backup.log 2>&1"
