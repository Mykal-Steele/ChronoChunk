"""What /setup shows: where the bot runs and the settings it runs with. Nothing secret goes in here."""
import asyncio
import logging
import os
import platform
import re
import sqlite3
import time
from datetime import date
from typing import List, Optional, Tuple

import aiohttp
import discord

from config.config import Config
from src.database import get_database
from src.usage_guard import get_usage_guard
from src.version import __version__

logger = logging.getLogger(__name__)

REPO_URL = "https://github.com/Mykal-Steele/ChronoChunk"

# Every Azure VM can ask this address about itself. It answers only from inside the VM and needs no key.
AZURE_METADATA_URL = "http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01"
AZURE_METADATA_TIMEOUT_SECONDS = 3

_started = time.monotonic()
# Filled in once at startup by find_host. None means the bot is not on an Azure VM, or has not asked yet.
_host: Optional[str] = None


async def find_host() -> None:
    """Ask Azure which VM this is, so /setup can name it. Anywhere else the question just fails."""
    global _host
    try:
        timeout = aiohttp.ClientTimeout(total=AZURE_METADATA_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(AZURE_METADATA_URL, headers={"Metadata": "true"}) as response:
                if response.status != 200:
                    return
                vm = await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError):
        return
    if isinstance(vm, dict) and vm.get("vmSize"):
        _host = f"Azure VM {vm.get('name', '?')}, {vm['vmSize']}, {vm.get('location', '?')}"
        logger.info(f"Running on {_host}")


def _uptime() -> str:
    minutes = int((time.monotonic() - _started) // 60)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _memory() -> str:
    """Memory in use and the container's cap, such as "96 MB of 640 MB". Empty where Linux does not say."""
    used = re.search(r"VmRSS:\s+(\d+) kB", _read("/proc/self/status"))
    if not used:
        return ""
    text = f"{int(used.group(1)) // 1024} MB"
    cap = _read("/sys/fs/cgroup/memory.max").strip()
    if cap.isdigit():
        text += f" of {int(cap) // (1024 * 1024)} MB"
    return text


def _per(seconds: int) -> str:
    if seconds >= 86400:
        return "a day"
    if seconds >= 3600:
        return "an hour"
    return f"per {seconds // 60} min"


def _count(number: int) -> str:
    """A token count in short form: 950, 12.3K, 4.1M."""
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    return str(number)


def _usage_rows() -> List[Tuple[str, str]]:
    """Everything the bot has used since the spending ledger began: model calls, tokens, cost and people."""
    try:
        database = get_database()
        total = database.query(
            "SELECT MIN(day) AS first_day, COALESCE(SUM(requests), 0) AS calls, COALESCE(SUM(input_tokens), 0) AS tokens_in, "
            "COALESCE(SUM(output_tokens), 0) AS tokens_out, COALESCE(SUM(cost), 0) AS cost FROM usage_daily")[0]
        people = database.query("SELECT COUNT(*) AS people FROM users")[0]["people"]
    except sqlite3.Error as e:
        logger.error(f"Could not read the usage totals for /setup: {e}")
        return [("used so far", "could not read the totals")]
    if not total["first_day"]:
        return [("used so far", "nothing yet")]
    first = date.fromisoformat(total["first_day"])
    month = get_usage_guard().summary()
    return [
        ("used so far", f"since {first:%b} {first.day}, {first.year}"),
        ("ai calls", f"{total['calls']:,}"),
        ("tokens", f"{_count(total['tokens_in'])} in, {_count(total['tokens_out'])} out"),
        ("ai cost", f"${total['cost']:.2f} in all, ${month['cycle_cost']:.2f} of ${month['monthly_budget']:g} this month"),
        ("people met", f"{people:,}"),
    ]


def setup_summary(chat_model: str) -> str:
    """The bot's setup as a code block: host, runtime, models, budget, limits and what it has used so far."""
    background = Config.AI_BACKGROUND_DEPLOYMENT or chat_model
    limits = Config.RATE_LIMITS
    runtime = f"Python {platform.python_version()}, discord.py {discord.__version__}"
    # Docker puts this file in every container
    if os.path.exists("/.dockerenv"):
        runtime = "Docker, " + runtime
    memory = _memory()
    rows = [
        ("version", __version__),
        ("host", _host or "not an Azure VM (a dev machine?)"),
        ("runs in", runtime),
        ("up for", _uptime() + (f", using {memory}" if memory else "")),
        ("", ""),
        ("chat model", f"{chat_model}, reasoning {Config.AI_CHAT_REASONING_EFFORT}"),
        ("background", f"{background}, for picking facts out of messages"),
        ("pdf pages", f"{Config.PDF_PAGE_NOTES}, kept {Config.PDF_NOTES_KEEP_DAYS} days"),
        ("web search", "on" if Config.TAVILY_API_KEY else "off, no search key"),
        ("", ""),
        ("bot budget", f"${Config.AI_MONTHLY_BUDGET_USD:g} a month, ${Config.AI_DAILY_BUDGET_USD:g} a day, "
                       f"new month on day {Config.AI_BUDGET_RESET_DAY}"),
        ("per person", f"${Config.AI_USER_DAILY_BUDGET_USD:.2f} a day, ${Config.AI_USER_WEEKLY_BUDGET_USD:.2f} over 7 days"),
        ("limits", ", ".join(f"{name} {count} {_per(window)}" for name, (count, window) in limits.items()
                             if name in ("chat", "file", "pdf", "web", "tldr"))),
        ("", ""),
        *_usage_rows(),
    ]
    lines = [f"{label:<11} {value}".rstrip() for label, value in rows]
    return ("**how im set up**\n```\n" + "\n".join(lines) + "\n```\n"
            f"code and the full setup guide: <{REPO_URL}#where-it-runs>")
