"""
One-time copy of the old JSON files into the database: one file per user, the AI
spending ledger and the channel switches. The files are left where they are.
"""
import glob
import json
import logging
import os
from datetime import datetime
from typing import Optional

from config.config import Config
from src.database import Database

logger = logging.getLogger(__name__)

IMPORT_FLAG = "legacy_json_imported_at"


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        logger.error(f"Could not read {path} for the import, skipping it: {e}")
        return None


def import_legacy_data(database: Database, user_data_dir: Optional[str] = None, state_dir: Optional[str] = None) -> dict:
    """
    Copy the JSON files into the database, once. Later calls do nothing. Returns what
    was copied: {"users": n, "ledger": bool, "channels": n}, or {} when already done.
    """
    if database.get_meta(IMPORT_FLAG):
        return {}
    from src.user_store import UserStore

    user_data_dir = user_data_dir or Config.USER_DATA_DIR
    state_dir = state_dir or Config.STATE_DIR
    store = UserStore(database)

    users = 0
    for path in sorted(glob.glob(os.path.join(user_data_dir, "*.json"))):
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        # A few old files have no user_id inside, only in the filename
        data.setdefault("user_id", os.path.splitext(os.path.basename(path))[0])
        users += store.import_user(data)

    ledger = _read_json(os.path.join(state_dir, "ai_usage.json"))
    has_ledger = False
    if isinstance(ledger, dict) and ledger.get("cycle_start"):
        _import_ledger(database, ledger)
        has_ledger = True

    channels = 0
    settings = _read_json(os.path.join(state_dir, "channel_settings.json"))
    if isinstance(settings, dict):
        for channel_id, value in settings.items():
            if isinstance(value, dict) and "read_recent_posts" in value:
                database.execute(
                    "INSERT OR IGNORE INTO channel_settings(channel_id, read_recent_posts) VALUES (?, ?)",
                    (str(channel_id), int(bool(value["read_recent_posts"]))))
                channels += 1

    database.set_meta(IMPORT_FLAG, datetime.now().isoformat())
    if users or has_ledger or channels:
        logger.info(f"Imported the old JSON files: {users} users, the spending ledger: {has_ledger}, {channels} channel settings")
    return {"users": users, "ledger": has_ledger, "channels": channels}


def _import_ledger(database: Database, ledger: dict) -> None:
    """
    The old ledger held two running totals. They become two rows: what was spent
    today, and the rest of the billing cycle dated at its first day. The sums the
    spending cap looks at come out the same.
    """
    cycle_start, day = str(ledger.get("cycle_start")), str(ledger.get("day") or ledger.get("cycle_start"))
    cycle_cost, day_cost = float(ledger.get("cycle_cost") or 0), float(ledger.get("day_cost") or 0)
    day_cost = min(day_cost, cycle_cost)
    rows = [(day, day_cost, 0, 0, 0)]
    earlier = (cycle_start, cycle_cost - day_cost, int(ledger.get("requests") or 0),
               int(ledger.get("input_tokens") or 0), int(ledger.get("output_tokens") or 0))
    rows.append(earlier)
    for row_day, cost, requests, tokens_in, tokens_out in rows:
        database.execute(
            "INSERT INTO usage_daily(day, user_id, feature, model, requests, input_tokens, output_tokens, cost) "
            "VALUES (?, '', 'imported', '', ?, ?, ?, ?) "
            "ON CONFLICT(day, user_id, feature, model) DO UPDATE SET requests = requests + excluded.requests, "
            "input_tokens = input_tokens + excluded.input_tokens, output_tokens = output_tokens + excluded.output_tokens, "
            "cost = cost + excluded.cost",
            (row_day, requests, tokens_in, tokens_out, cost))
