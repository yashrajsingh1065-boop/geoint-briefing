"""
Database backup utility for geoint-briefing.

Creates timestamped SQLite backups using the online backup API (safe even while DB is in use).
Retains the last N backups and removes older ones.

Usage:
    python backup_db.py                  # backup to data/backups/
    python backup_db.py --max-backups 7  # keep last 7 backups
    python backup_db.py --dest /tmp      # custom destination

Automate via cron/launchd:
    0 5 * * * cd /path/to/geoint-briefing && .venv/bin/python backup_db.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))
from config import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

DEFAULT_BACKUP_DIR = DB_PATH.parent / "backups"
DEFAULT_MAX_BACKUPS = 14  # 2 weeks of daily backups


def backup_database(dest_dir: Path, max_backups: int) -> Optional[Path]:
    """
    Create a timestamped backup of the SQLite database.
    Uses SQLite's online backup API for safe hot backups (WAL-safe).
    Returns the backup file path, or None on failure.
    """
    if not DB_PATH.exists():
        logger.error("Database not found at %s", DB_PATH)
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = dest_dir / f"briefings_{timestamp}.db"

    try:
        # Use SQLite online backup API (safe with WAL mode)
        source = sqlite3.connect(str(DB_PATH))
        dest = sqlite3.connect(str(backup_path))

        source.backup(dest)

        dest.close()
        source.close()

        # Set restrictive permissions on backup
        os.chmod(backup_path, 0o600)

        size_mb = backup_path.stat().st_size / (1024 * 1024)
        logger.info("Backup created: %s (%.2f MB)", backup_path.name, size_mb)

    except Exception as exc:
        logger.error("Backup failed: %s", type(exc).__name__)
        if backup_path.exists():
            backup_path.unlink()
        return None

    # Prune old backups
    _prune_old_backups(dest_dir, max_backups)

    return backup_path


def _prune_old_backups(dest_dir: Path, max_backups: int) -> None:
    """Remove oldest backups to keep only max_backups."""
    backups = sorted(dest_dir.glob("briefings_*.db"), key=lambda p: p.stat().st_mtime)

    while len(backups) > max_backups:
        oldest = backups.pop(0)
        oldest.unlink()
        logger.info("Pruned old backup: %s", oldest.name)


def verify_backup(backup_path: Path) -> bool:
    """Quick integrity check on a backup file."""
    try:
        conn = sqlite3.connect(str(backup_path))
        result = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        ok = result[0] == "ok"
        if ok:
            logger.info("Backup integrity check: PASSED")
        else:
            logger.warning("Backup integrity check: FAILED — %s", result[0])
        return ok
    except Exception as exc:
        logger.error("Backup verification failed: %s", type(exc).__name__)
        return False


def main():
    parser = argparse.ArgumentParser(description="Backup the geoint-briefing database")
    parser.add_argument("--dest", type=Path, default=DEFAULT_BACKUP_DIR, help="Backup destination directory")
    parser.add_argument("--max-backups", type=int, default=DEFAULT_MAX_BACKUPS, help="Max backups to retain")
    parser.add_argument("--verify", action="store_true", help="Verify backup integrity after creation")
    args = parser.parse_args()

    backup_path = backup_database(args.dest, args.max_backups)

    if backup_path and args.verify:
        if not verify_backup(backup_path):
            sys.exit(1)

    if not backup_path:
        sys.exit(1)


if __name__ == "__main__":
    main()
