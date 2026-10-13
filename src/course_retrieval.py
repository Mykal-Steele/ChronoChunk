"""
The one way course material reaches a reply. A lookup searches the passages (slides,
lectures, assignments) for the topic and, when the question is about dates, adds the
dated list of assignments. It says how good the match is, so the bot can tell a
right answer from the closest thing it found.
"""
import asyncio
import re
from dataclasses import dataclass
from datetime import date, timedelta, timezone
from typing import Awaitable, Callable, List, Optional, Sequence

from src.course_store import RESULTS, CourseStore, Hit

# The most characters of a result that reach the model: the chunk itself, each chunk
# beside it, and the wording it was translated from
MAX_CHUNK_CHARS = 2500
MAX_NEIGHBOUR_CHARS = 500
MAX_ORIGINAL_CHARS = 800
# The most assignments one list holds
MAX_ROWS = 40

# What each kind of source is called, for one of them and for a count of them
KIND_NAMES = {
    "slide": ("slides", "slide decks"),
    "transcript": ("lecture recording", "lecture recordings"),
    "post": ("class post", "class posts"),
    "assignment": ("assignment", "assignments"),
}

# The friend group studies in Bangkok, so dates and times are told in its time
BANGKOK = timezone(timedelta(hours=7))

# On real lectures the right passage scored 0.47 or more. Results under it passed the
# search's floor but are only near the topic. To be set again from the answer check.
STRONG = 0.47
# A message with one of these is about dates or work to hand in, so the dated list goes with it
_DATE_WORDS = re.compile(r"\b(due|deadlines?|assignments?|homework|hw|submit|hand in|this week|next week|upcoming)\b",
                         re.IGNORECASE)


@dataclass
class Found:
    """What a lookup found, written out for the model."""
    text: str
    hits: List[Hit]
    # How many assignments the dated list holds
    rows: int
    # strong, loose or nothing
    label: str


def about_dates(message: str) -> bool:
    return bool(_DATE_WORDS.search(message))


def label_of(hits: Sequence[Hit]) -> str:
    if not hits:
        return "nothing"
    return "strong" if max(hit.similarity for hit in hits) >= STRONG else "loose"


def _clip(text: str, limit: int, keep_end: bool = False) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:] if keep_end else text[:limit] + "..."


def _where(hit: Hit) -> str:
    """Where in its source a result sits: "page 12" for slides, "at 0:41:07" for a recording."""
    if not hit.locator:
        return ""
    return f"at {hit.locator}" if hit.kind == "transcript" else hit.locator


def _due(day: str, today: date) -> str:
    """How far off a due day is, like "due in 3 days (16 Oct 2026)"."""
    try:
        due = date.fromisoformat(day)
    except ValueError:
        return "no due date"
    days = (due - today).days
    if days > 1:
        left = f"due in {days} days"
    elif days < -1:
        left = f"was due {-days} days ago"
    else:
        left = {1: "due tomorrow", 0: "due today", -1: "was due yesterday"}[days]
    return f"{left} ({due.day} {due:%b %Y})"


def assignment_rows(items: List[dict], today: date, due: Optional[str] = None) -> List[str]:
    """
    Assignments as one line each: what is still to come first, the soonest on top, then
    what has passed, the latest on top. due narrows it to "upcoming" or "past".
    """
    day = today.isoformat()
    upcoming = [item for item in items if item["date"] >= day] + [item for item in items if not item["date"]]
    past = [item for item in reversed(items) if item["date"] and item["date"] < day]
    lines = []
    for item in {"upcoming": upcoming, "past": past}.get(due or "", upcoming + past)[:MAX_ROWS]:
        line = f'- {item["course"]} "{item["title"]}", {_due(item["date"], today)}'
        time_due = re.search(r"^Due \d{4}-\d{2}-\d{2} (\d{2}:\d{2})", item["text"], re.MULTILINE)
        points = re.search(r"^([\d.]+ points)$", item["text"], re.MULTILINE)
        if item["date"] and time_due:
            line += f" at {time_due.group(1)}"
        if points:
            line += f", {points.group(1)}"
        lines.append(line)
    return lines


def format_hits(query: str, hits: List[Hit], names: dict, label: Optional[str] = None,
                rows: Sequence[str] = (), today: Optional[date] = None) -> str:
    """A lookup in a === block for the model: each passage under a line that says where it is from, then the dated list."""
    lines = [f'=== COURSE MATERIAL for "{query}" ===']
    if not hits:
        lines.append("nothing in the course material matches. tell them u couldnt find it there" if not rows
                     else "no passage in the course material matches.")
    elif (label or label_of(hits)) == "loose":
        lines.append("these are only loosely related to what was asked. if they dont answer it, say u couldnt find "
                     "exactly that, then give the closest thing here and where it is from")
    for number, hit in enumerate(hits, 1):
        source = [f"{hit.course} {names.get(hit.course, '')}".strip(),
                  f'{KIND_NAMES.get(hit.kind, (hit.kind,))[0]} "{hit.title}"']
        source += [part for part in (hit.section, _where(hit), hit.date) if part]
        lines.append(f"[{number}] " + ", ".join(source))
        if hit.url:
            lines.append(f"link: {hit.url}")
        if hit.before:
            lines.append(f"right before it: {_clip(hit.before, MAX_NEIGHBOUR_CHARS, keep_end=True)}")
        lines.append(_clip(hit.text, MAX_CHUNK_CHARS))
        if hit.after:
            lines.append(f"right after it: {_clip(hit.after, MAX_NEIGHBOUR_CHARS)}")
        if hit.original:
            lines.append(f"as written, before translation: {_clip(hit.original, MAX_ORIGINAL_CHARS)}")
        lines.append("")
    if rows:
        if lines[-1] == "":
            lines.pop()
        when = f", today is {today:%A} {today.day} {today:%b %Y} in Bangkok" if today else ""
        lines.append(f"ASSIGNMENTS posted in teams{when}:")
        lines.extend(rows)
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    lines.append("=== END ===")
    return "\n".join(lines)


async def retrieve(store: CourseStore, embed: Callable[[str], Awaitable[List[float]]], words: str, codes: Sequence[str],
                   today: date, kind: Optional[str] = None, since: Optional[str] = None, dated: bool = False,
                   due: Optional[str] = None) -> Found:
    """
    Look words up in the courses in codes, or in every course when codes is empty.
    kind puts results of that kind first without leaving the others out. dated adds
    the list of assignments, and due narrows that list to "upcoming" or "past".
    """
    vector = await embed(words)

    def search() -> List[Hit]:
        found: List[Hit] = []
        for code in codes or [None]:
            if kind:
                found += store.search(words, vector, code, kind, since)
            found += store.search(words, vector, code, None, since)
        return found

    # The search is a few milliseconds of SQLite, kept off the event loop all the same
    seen, hits = set(), []
    for hit in await asyncio.to_thread(search):
        key = (hit.course, hit.kind, hit.title, hit.locator)
        if key not in seen:
            seen.add(key)
            hits.append(hit)
    hits = sorted(hits, key=lambda hit: (bool(kind) and hit.kind != kind, -hit.similarity))[:RESULTS]

    rows: List[str] = []
    if dated or due:
        items = [item for code in codes or [None] for item in await asyncio.to_thread(store.assignments, code)]
        rows = assignment_rows(sorted(items, key=lambda item: (item["date"], item["title"])), today, due)
    names = {course["code"]: course["name"] for course in store.courses()}
    label = label_of(hits)
    return Found(format_hits(words, hits, names, label, rows, today), hits, len(rows), label)
