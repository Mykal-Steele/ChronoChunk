"""What /setup shows: where the bot runs and the settings it runs with. Nothing secret goes in here."""
import asyncio
import logging
import os
import platform
import re
import time
from typing import Optional

import aiohttp
import discord

from config.config import Config
from src.stats import usage_rows
from src.tools.courses import course_store
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


def _courses() -> str:
    """How much course material is loaded, in a few words."""
    store = course_store()
    if not store:
        return "off, no course file the bot can search"
    return f"{len(store.courses())} courses, {sum(store.chunk_counts().values()):,} passages, embedded with {Config.EMBEDDING_DEPLOYMENT}"


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
        ("courses", _courses()),
        ("test bot", "answered in one test channel" if Config.TEST_BOT_ID and Config.TEST_CHANNEL_ID else "off"),
        ("joins in", f"may answer uncalled messages for {Config.JOIN_IN_SECONDS}s after a call"
                     if Config.JOIN_IN_SECONDS > 0 else "off"),
        ("", ""),
        ("bot budget", f"${Config.AI_MONTHLY_BUDGET_USD:g} a month, ${Config.AI_DAILY_BUDGET_USD:.2f} a day, "
                       f"new month on day {Config.AI_BUDGET_RESET_DAY}"),
        ("per person", f"${Config.AI_USER_DAILY_BUDGET_USD:.2f} a day, ${Config.AI_USER_WEEKLY_BUDGET_USD:.2f} over 7 days"),
        ("limits", ", ".join(f"{name} {count} {_per(window)}" for name, (count, window) in limits.items()
                             if name in ("chat", "file", "pdf", "web", "course", "tldr"))),
        ("", ""),
        *usage_rows(),
    ]
    lines = [f"{label:<11} {value}".rstrip() for label, value in rows]
    return ("**how im set up**\n```\n" + "\n".join(lines) + "\n```\n"
            f"`/stats` has the full count. code and the full setup guide: <{REPO_URL}#where-it-runs>")
