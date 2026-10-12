"""A daily copy of the database, so a damaged file or a bad change does not take the bot's history with it."""
import asyncio
import glob
import logging
import os
from datetime import date, datetime, timezone
from typing import Optional

from config.config import Config
from src.database import Database, get_database

logger = logging.getLogger(__name__)

BACKUP_FOLDER = "backups"
# One file per day. Older ones are deleted, so something a person deletes is gone from the copies a week later.
KEEP_DAYS = 7
HOURS_BETWEEN = 24


def backup_folder() -> str:
    return os.path.join(Config.STATE_DIR, BACKUP_FOLDER)


def backup_now(database: Optional[Database] = None, folder: Optional[str] = None) -> str:
    """Write today's copy and delete the ones past KEEP_DAYS. Returns the path of the new file."""
    database = database or get_database()
    folder = folder or backup_folder()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"chronochunk-{datetime.now(timezone.utc):%Y-%m-%d}.db")
    # Written under another name first, so a copy that stops halfway never replaces a good one
    partial = path + ".partial"
    if os.path.exists(partial):
        os.remove(partial)
    database.backup(partial)
    os.replace(partial, path)
    for old in sorted(glob.glob(os.path.join(folder, "chronochunk-*.db")))[:-KEEP_DAYS]:
        os.remove(old)
    return path


def last_backup_day(folder: Optional[str] = None) -> Optional[date]:
    """The day of the newest copy, or None when there is none."""
    files = sorted(glob.glob(os.path.join(folder or backup_folder(), "chronochunk-*.db")))
    if not files:
        return None
    try:
        return date.fromisoformat(os.path.basename(files[-1])[len("chronochunk-"):-len(".db")])
    except ValueError:
        return None


async def run_daily() -> None:
    """Copy the database at startup and then once a day, for as long as the bot runs."""
    while True:
        try:
            path = await asyncio.to_thread(backup_now)
            logger.info(f"Database copied to {path}")
        except Exception as e:
            # A failed copy must not stop the bot. The next one is tried a day later.
            logger.error(f"Could not copy the database: {e!r}")
        await asyncio.sleep(HOURS_BETWEEN * 3600)
