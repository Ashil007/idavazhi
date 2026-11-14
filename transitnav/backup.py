"""Automated live backup for Vazhi SQLite databases.

Performs hot, lock-free backups using SQLite's native online backup API
(conn.backup), so copies are 100% consistent even while riders are actively
reading or writing data.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")

# Databases to monitor and back up
DB_NAMES = ["timings.db", "places.db", "stops.db", "feedback.db"]
RETENTION_DAYS = 14
BACKUP_INTERVAL_S = 24 * 3600  # Once every 24 hours


def backup_database(db_filename: str, log=None) -> str | None:
    """Safely back up a single SQLite database without locking out active users."""
    src_path = os.path.join(DATA_DIR, db_filename)
    if not os.path.exists(src_path):
        return None

    os.makedirs(BACKUP_DIR, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    base_name = os.path.splitext(db_filename)[0]
    dest_path = os.path.join(BACKUP_DIR, f"{base_name}_{today}.db")

    try:
        src = sqlite3.connect(src_path, timeout=10.0)
        dst = sqlite3.connect(dest_path)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        if log:
            log(f"Database backup created: {os.path.basename(dest_path)}")
        return dest_path
    except Exception as exc:
        if log:
            log(f"Backup failed for {db_filename}: {exc}")
        return None


def prune_old_backups(retention_days: int = RETENTION_DAYS, log=None) -> int:
    """Delete backups older than retention_days to preserve disk space."""
    if not os.path.isdir(BACKUP_DIR):
        return 0

    cutoff = time.time() - (retention_days * 86400)
    pruned = 0
    for fname in os.listdir(BACKUP_DIR):
        if fname.endswith(".db"):
            fpath = os.path.join(BACKUP_DIR, fname)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    pruned += 1
                    if log:
                        log(f"Pruned old backup: {fname}")
            except OSError:
                pass
    return pruned


def backup_all(log=None) -> list[str]:
    """Back up all active databases and prune expired archives."""
    created = []
    for db_name in DB_NAMES:
        dest = backup_database(db_name, log=log)
        if dest:
            created.append(dest)
    prune_old_backups(RETENTION_DAYS, log=log)
    return created


def start_daily_backup(log=None) -> threading.Thread:
    """Run an automated background daemon thread that backs up databases daily."""
    def worker():
        # First backup on server start
        try:
            backup_all(log=log)
        except Exception as e:
            if log:
                log(f"Initial backup error: {e}")

        # Sleep and repeat once every 24 hours
        while True:
            time.sleep(BACKUP_INTERVAL_S)
            try:
                backup_all(log=log)
            except Exception as e:
                if log:
                    log(f"Scheduled backup error: {e}")

    thread = threading.Thread(target=worker, name="daily-sqlite-backup", daemon=True)
    thread.start()
    return thread
