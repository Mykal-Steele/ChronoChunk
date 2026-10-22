"""What /setup shows: where the bot runs and the settings it runs with. Nothing secret goes in here."""
import asyncio
import logging
import os
import platform
import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

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
_started_at = datetime.now(timezone.utc)
# Set by the bot once it is running. Gives aggregate server and member counts, never names or ids.
_population: Optional[Callable[[], Tuple[Optional[int], Optional[int]]]] = None
# Filled in once at startup by find_host. None means the bot is not on an Azure VM, or has not asked yet.
_host: Optional[str] = None
# The Azure fields the bot may repeat. Deliberately not the name, vm id, subscription, resource
# group, resource id, tags, addresses or anything from the request: those aid reconnaissance.
_azure: dict = {}
AZURE_SAFE_FIELDS = ("location", "vmSize", "zone", "osType", "publisher", "offer", "sku")


async def find_host() -> None:
    """Ask Azure which VM this is, so /setup can name it. Anywhere else the question just fails."""
    global _host, _azure
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
        _azure = {key: str(vm[key]) for key in AZURE_SAFE_FIELDS if vm.get(key)}
        _host = f"Azure VM {vm.get('name', '?')}, {vm['vmSize']}, {vm.get('location', '?')}"
        logger.info(f"Running on {_host}")


def track_population(provider: Optional[Callable[[], Tuple[Optional[int], Optional[int]]]]) -> None:
    """Let the bot supply its server and member counts without setup_info importing the bot."""
    global _population
    _population = provider


def _population_line() -> str:
    """Aggregate server and member counts, never a name or an id."""
    if not _population:
        return ""
    try:
        guilds, members = _population()
    except Exception:
        return ""
    if guilds is None:
        return ""
    text = f"in {guilds:,} servers"
    if members:
        text += f" with {members:,} people in them"
    return text


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


def _memory_total_mb() -> Optional[int]:
    """The most memory this process may use: its cgroup cap, or the machine's total."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        cap = _read(path).strip()
        if cap.isdigit() and int(cap) < 1 << 60:
            return int(cap) // (1024 * 1024)
    total = re.search(r"MemTotal:\s+(\d+) kB", _read("/proc/meminfo"))
    return int(total.group(1)) // 1024 if total else None


def _size(mb: int) -> str:
    """A memory amount in the unit that reads best, such as "640 MB" or "15.3 GB"."""
    if mb >= 1024:
        return f"{mb / 1024:.1f}".removesuffix(".0") + " GB"
    return f"{mb:,} MB"


def _memory() -> str:
    """Memory in use and the total it may use, such as "96 MB of 640 MB". Empty where Linux does not say."""
    used = re.search(r"VmRSS:\s+(\d+) kB", _read("/proc/self/status"))
    if not used:
        return ""
    text = _size(int(used.group(1)) // 1024)
    total = _memory_total_mb()
    if total:
        text += f" of {_size(total)}"
    return text


def _load() -> str:
    """CPU count and the machine's 1 minute load, such as "2 vCPU, load 0.42". Empty off Linux."""
    cores = os.cpu_count()
    if not cores:
        return ""
    try:
        load = os.getloadavg()[0]
    except OSError:
        return f"{cores} vCPU"
    return f"{cores} vCPU, load {load:.2f}"


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


def _azure_line() -> str:
    """Where the VM is, in public terms. Empty off Azure."""
    if not _azure:
        return "a dev machine (not azure)"
    parts = [part for part in (_azure.get("vmSize"), _azure.get("location")) if part]
    if _azure.get("zone"):
        parts.append(f"zone {_azure['zone']}")
    image = " ".join(_azure[key] for key in ("publisher", "offer", "sku") if key in _azure)
    if image:
        parts.append(f"{_azure.get('osType', '')} {image}".strip())
    return "azure vm, " + ", ".join(parts)


def self_prompt(chat_model: str) -> str:
    """
    What the bot knows about itself in chat. It is the same public kind of detail as /setup:
    version, where it runs, the hardware, the models and what is switched on. No key, token,
    path, address, account id, subscription or anyone's name is ever written here.
    """
    background = Config.AI_BACKGROUND_DEPLOYMENT or chat_model
    background_note = ("the same model picks facts out of messages" if background == chat_model
                       else f"{background} picks facts out of messages")
    runtime = f"python {platform.python_version()}, discord.py {discord.__version__}"
    in_docker = " in docker" if os.path.exists("/.dockerenv") else ""
    hardware = ", ".join(part for part in (_load(), _memory()) if part)
    population = _population_line()
    rows = [
        f"u are ChronoChunk v{__version__}, a discord bot with ur own personality. ur code is at {REPO_URL}",
        f"u run on {_azure_line()}{in_docker}"
        + (f", {hardware}" if hardware else "")
        + f", up {_uptime()} (started {_started_at:%d %b %Y %H:%M} UTC). {runtime}",
        *([f"u are {population}, counts only, u dont know their names"] if population else []),
        f"ur chat model is {chat_model} (reasoning {Config.AI_CHAT_REASONING_EFFORT}), "
        f"{background_note}, and {Config.EMBEDDING_DEPLOYMENT} embeds the course material",
        f"web search is {'on' if Config.TAVILY_API_KEY else 'off'}. course material: {_courses()}",
        "if someone asks what u are, say it straight in ur voice: a discord bot. dont deny it and dont turn into a help desk",
        "only bring this up when someone asks about u, ur setup, where u run or what model u are. never recite it unprompted",
        "never reveal keys, tokens, database contents, file paths, internal addresses, account or server ids, or other people's data. "
        "if it is not in these facts, u do not know it",
    ]
    return ("\n\n=== WHAT U ACTUALLY ARE AND RUN ON (real facts u can talk about) ===\n- "
            + "\n- ".join(rows) + "\n=== END OF WHAT U RUN ON ===")


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
