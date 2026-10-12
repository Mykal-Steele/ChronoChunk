"""What /stats shows: everything the bot has used since its spending record began."""
import logging
import sqlite3
from datetime import date, datetime, timezone
from typing import List, Tuple

from src import backups
from src.database import get_database
from src.usage_guard import get_usage_guard

logger = logging.getLogger(__name__)

# How the ledger's feature names read in /stats. Calls from before features were recorded have none.
JOBS = {
    "chat": "chat",
    "facts": "memory",
    "document": "documents",
    "pdf_notes": "pdf pages",
    "emoji_notes": "emoji",
    "join_in": "joining in",
    "music": "music search",
    "": "earlier",
}

USAGE_TOTALS = (
    "COALESCE(SUM(requests), 0) AS calls, COALESCE(SUM(input_tokens), 0) AS tokens_in, "
    "COALESCE(SUM(output_tokens), 0) AS tokens_out, COALESCE(SUM(cost), 0) AS cost"
)


def short_count(number: int) -> str:
    """A token count in short form: 950, 12.3K, 4.1M."""
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    return str(number)


def _day(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"


def _block(title: str, rows: List[Tuple[str, str]]) -> str:
    return f"**{title}**\n```\n" + "\n".join(f"{label:<14} {value}".rstrip() for label, value in rows) + "\n```"


def usage_rows() -> List[Tuple[str, str]]:
    """The short version, for /setup: model calls, tokens, cost and people since the ledger began."""
    try:
        database = get_database()
        total = database.query(f"SELECT MIN(day) AS first_day, {USAGE_TOTALS} FROM usage_daily")[0]
        people = database.query("SELECT COUNT(*) AS people FROM users")[0]["people"]
    except sqlite3.Error as e:
        logger.error(f"Could not read the usage totals: {e}")
        return [("used so far", "could not read the totals")]
    if not total["first_day"]:
        return [("used so far", "nothing yet")]
    month = get_usage_guard().summary()
    return [
        ("used so far", f"since {_day(date.fromisoformat(total['first_day']))}"),
        ("ai calls", f"{total['calls']:,}"),
        ("tokens", f"{short_count(total['tokens_in'])} in, {short_count(total['tokens_out'])} out"),
        ("ai cost", f"${total['cost']:.2f} in all, ${month['cycle_cost']:.2f} of ${month['monthly_budget']:g} this month"),
        ("people met", f"{people:,}"),
    ]


def stats_summary() -> str:
    """Everything the bot has used so far, as one Discord message. No names and no user ids."""
    try:
        database = get_database()
        today = datetime.now(timezone.utc).date()
        week_start = date.fromordinal(today.toordinal() - 6).isoformat()
        total = database.query(f"SELECT MIN(day) AS first_day, COUNT(DISTINCT day) AS days_used, {USAGE_TOTALS} FROM usage_daily")[0]
        if not total["first_day"]:
            return "nothing to count yet, i havent made a single ai call"
        week = database.query(f"SELECT {USAGE_TOTALS} FROM usage_daily WHERE day >= ?", (week_start,))[0]
        this_day = database.query(f"SELECT {USAGE_TOTALS} FROM usage_daily WHERE day = ?", (today.isoformat(),))[0]
        busiest = database.query(
            f"SELECT day, {USAGE_TOTALS} FROM usage_daily GROUP BY day ORDER BY calls DESC, day DESC LIMIT 1")[0]
        by_job = database.query(f"SELECT feature AS name, {USAGE_TOTALS} FROM usage_daily GROUP BY feature ORDER BY cost DESC")
        by_model = database.query(f"SELECT model AS name, {USAGE_TOTALS} FROM usage_daily GROUP BY model ORDER BY cost DESC")
        people = database.query("SELECT COUNT(*) AS n FROM users")[0]["n"]
        facts = database.query("SELECT COUNT(*) AS n FROM facts")[0]["n"]
        decks = database.query("SELECT COUNT(*) AS n FROM page_notes")[0]["n"]
    except sqlite3.Error as e:
        logger.error(f"Could not read the usage totals for /stats: {e}")
        return "couldnt read my numbers rn, try again in a bit"

    first = date.fromisoformat(total["first_day"])
    month = get_usage_guard().summary()
    days = today.toordinal() - first.toordinal() + 1
    rows = [
        ("counting", f"since {_day(first)}, {days:,} days, used on {total['days_used']:,} of them"),
        ("ai calls", f"{total['calls']:,}"),
        ("tokens", f"{short_count(total['tokens_in'])} in, {short_count(total['tokens_out'])} out"),
        ("ai cost", f"${total['cost']:.2f}" + (f", ${total['cost'] / total['calls']:.4f} a call" if total["calls"] else "")),
        ("", ""),
        ("today", f"{this_day['calls']:,} calls, ${this_day['cost']:.2f}"),
        ("last 7 days", f"{week['calls']:,} calls, ${week['cost']:.2f}"),
        ("this month", f"${month['cycle_cost']:.2f} of ${month['monthly_budget']:g}"),
        ("busiest day", f"{_day(date.fromisoformat(busiest['day']))}: {busiest['calls']:,} calls, ${busiest['cost']:.2f}"),
        ("", ""),
        ("by job", ""),
        *((f"  {JOBS.get(row['name'], row['name'])}", f"{row['calls']:,} calls, ${row['cost']:.2f}") for row in by_job),
        ("", ""),
        ("by model", ""),
        *((f"  {row['name'] or 'unknown'}", f"{row['calls']:,} calls, ${row['cost']:.2f}") for row in by_model),
        ("", ""),
        ("people", f"{people:,} remembered, {facts:,} facts about them"),
        ("pdfs", f"{decks:,} described and kept"),
    ]
    saved = backups.last_backup_day()
    rows.append(("last copy", _day(saved) if saved else "none yet"))
    return _block("my numbers so far", rows)
