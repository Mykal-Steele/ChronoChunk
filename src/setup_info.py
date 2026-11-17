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
# Every Google Cloud VM can ask this address about itself, with this header. Like Azure's, it
# answers only from inside the VM. The answer carries the same header, which is how a router
# or anything else on that address is told apart from Google.
GOOGLE_METADATA_URL = "http://metadata.google.internal/computeMetadata/v1/instance/?recursive=true"
# The newest entry is what the bot knows about its own latest update (moves, resets, fixes)
CHANGELOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CHANGELOG.md")
CHANGELOG_LIMIT = 2400
CHANGELOG_RECENT_DAYS = 14


def _today():
    return datetime.now(timezone.utc).date()

PROVIDER_NAMES = {
    "vertex": "gemini on google vertex ai",
    "azure": "azure openai",
    "openai": "an openai-compatible api",
}


def latest_changes() -> str:
    """
    What changed lately, from CHANGELOG.md: every entry from the last two weeks, newest
    first, or just the newest one when nothing is that recent. Each entry is its heading,
    "3.22.0 (2026-11-17)", then its bullets, cut to CHANGELOG_LIMIT characters in all.
    """
    try:
        with open(CHANGELOG_PATH, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    entries = []
    for section in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        title, _, body = section.partition("\n")
        found = re.search(r"\((\d{4}-\d{2}-\d{2})\)", title)
        if not found:
            continue
        bullets = [line.strip()[2:] for line in body.splitlines() if line.strip().startswith("- ")]
        entries.append((datetime.strptime(found.group(1), "%Y-%m-%d").date(), title.strip(), bullets))
    if not entries:
        return ""
    recent = [e for e in entries if (_today() - e[0]).days <= CHANGELOG_RECENT_DAYS] or entries[:1]
    lines, size = [], 0
    for _, title, bullets in recent:
        if size + len(title) > CHANGELOG_LIMIT:
            break
        lines.append(title)
        size += len(title)
        for bullet in bullets:
            if size + len(bullet) > CHANGELOG_LIMIT:
                break
            lines.append(f"  - {bullet}")
            size += len(bullet)
    return "\n".join(lines)


_started = time.monotonic()
_started_at = datetime.now(timezone.utc)
# Set by the bot once it is running. Gives aggregate server and member counts, never names or ids.
_population: Optional[Callable[[], Tuple[Optional[int], Optional[int]]]] = None
# Filled in once at startup by find_host. None means no cloud answered, or it has not asked yet.
_host: Optional[str] = None
# The Azure fields the bot may repeat. Deliberately not the name, vm id, subscription, resource
# group, resource id, tags, addresses or anything from the request: those aid reconnaissance.
_azure: dict = {}
AZURE_SAFE_FIELDS = ("location", "vmSize", "zone", "osType", "publisher", "offer", "sku")
# The Google fields the bot may repeat: the last part of each, never the project, name,
# hostname, id, service account or addresses.
_google: dict = {}


async def find_host() -> None:
    """Ask Azure, then Google, which VM this is, so /setup can name it. Anywhere else both just fail."""
    await _find_azure()
    if _host is None:
        await _find_google()


async def _find_google() -> None:
    global _host, _google
    try:
        timeout = aiohttp.ClientTimeout(total=AZURE_METADATA_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(GOOGLE_METADATA_URL, headers={"Metadata-Flavor": "Google"}) as response:
                if response.status != 200 or response.headers.get("Metadata-Flavor") != "Google":
                    return
                vm = await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError):
        return
    if not isinstance(vm, dict) or not vm.get("machineType"):
        return
    _google = {key: str(vm[key]).rsplit("/", 1)[-1] for key in ("machineType", "zone", "image") if vm.get(key)}
    _host = "Google Cloud VM, " + ", ".join(_google[key] for key in ("machineType", "zone") if key in _google)
    logger.info(f"Running on {_host}")


async def _find_azure() -> None:
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
    """Where the VM is, in public terms."""
    if _google:
        return "google cloud vm, " + ", ".join(_google[key] for key in ("machineType", "zone", "image") if key in _google)
    if not _azure:
        return "a machine u cant identify (no cloud answered when u asked at startup)"
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
    heavy = Config.AI_HEAVY_MODEL
    heavy_note = f"{heavy} writes study sets, detailed answers, rewrites and documents. " if heavy else ""
    provider = PROVIDER_NAMES.get((os.environ.get("AI_PROVIDER") or "azure").strip().lower(), "an unknown provider")
    changes = latest_changes()
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
        f"ur models come from {provider}. ur chat model is {chat_model} (reasoning {Config.AI_CHAT_REASONING_EFFORT}). "
        f"{heavy_note}{background_note}, and {Config.EMBEDDING_DEPLOYMENT} embeds the course material",
        *([f"ur recent updates, newest first (this is what happened to u lately: moves, outages, resets. "
           f"use it when someone asks where u were, what changed or why u forgot them):\n{changes}"] if changes else []),
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
        ("host", _host or "unknown, no cloud metadata answered"),
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
