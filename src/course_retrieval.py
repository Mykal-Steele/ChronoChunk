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
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple

from src.course_store import _STOP_WORDS, RESULTS, CourseStore, Hit

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


# Asked like this, they want what has been, not only what is coming
_PAST_WORDS = re.compile(r"\b(so far|past|previous|earlier|already|had|did|were|was|last|all|every|list)\b", re.IGNORECASE)
# Words of a question about dates that are not a topic to search the passages for
_NOT_A_TOPIC = _STOP_WORDS | frozenset(
    "due deadline deadlines assignment assignments homework hw submit hand week next upcoming today tonight tomorrow "
    "whats any anything got there list all every everything work now still left coming soon yet get so far past "
    "previous earlier already had did were was last class classes".split())


def has_topic(words: str) -> bool:
    """Whether a question about dates also names something to look for, like "lab 7"."""
    return any(word not in _NOT_A_TOPIC for word in re.findall(r"[^\W_]+", words.lower()))


def window_of(message: str, today: date) -> Optional[Tuple[date, date]]:
    """The days a message asks about, first and last, when it names them: today, tomorrow, this week, next week."""
    low = message.lower()
    monday = today - timedelta(days=today.weekday())
    if "next week" in low:
        return monday + timedelta(days=7), monday + timedelta(days=13)
    if "this week" in low:
        return monday, monday + timedelta(days=6)
    if "tomorrow" in low:
        return today + timedelta(days=1), today + timedelta(days=1)
    if re.search(r"\b(today|tonight)\b", low):
        return today, today
    return None


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


def _row(item: dict, today: date) -> str:
    line = f'{item["course"]} "{item["title"]}", {_due(item["date"], today)}'
    time_due = re.search(r"^Due \d{4}-\d{2}-\d{2} (\d{2}:\d{2})", item["text"], re.MULTILINE)
    points = re.search(r"^([\d.]+ points)$", item["text"], re.MULTILINE)
    if item["date"] and time_due:
        line += f" at {time_due.group(1)}"
    if points:
        line += f", {points.group(1)}"
    return line


def assignment_rows(items: List[dict], today: date, due: Optional[str] = None,
                    window: Optional[Tuple[date, date]] = None, everything: bool = False) -> Tuple[List[str], int]:
    """
    Assignments as one line each, and how many of them. Only the ones the question is
    about: the days of window when it names days, "upcoming" or "past" for due, all of
    them for everything. Otherwise only what is still to come, with a count of the past
    ones. Still to come is first, the soonest on top, then what has passed, the latest on top.
    """
    day = today.isoformat()
    upcoming = [item for item in items if item["date"] >= day]
    undated = [item for item in items if not item["date"]]
    past = [item for item in reversed(items) if item["date"] and item["date"] < day]
    if window:
        first, last = window
        within = [item for item in items if first.isoformat() <= item["date"] <= last.isoformat()]
        days = f"from {first.day} {first:%b} to {last.day} {last:%b %Y}"
        lines = ([f"due {days}:"] + [f"- {_row(item, today)}" for item in within] if within
                 else [f"nothing in the teams assignments is due {days}"])
        later = [item for item in items if item["date"] > last.isoformat()]
        if later:
            lines.append(f"next after that: {_row(later[0], today)}")
        return lines, len(within)
    if due:
        chosen, left_out = {"upcoming": upcoming + undated, "past": past}[due], 0
    elif everything:
        chosen, left_out = upcoming + undated + past, 0
    else:
        # A question about homework is about what is still to do
        chosen, left_out = upcoming + undated, len(past)
    chosen = chosen[:MAX_ROWS]
    lines = [f"- {_row(item, today)}" for item in chosen]
    if not chosen and left_out:
        lines.append("nothing in the teams assignments is due from now on")
    if left_out:
        lines.append(f"{left_out} past {'one is' if left_out == 1 else 'ones are'} not listed")
    return lines, len(chosen)


def format_hits(query: str, hits: List[Hit], names: dict, label: Optional[str] = None,
                rows: Sequence[str] = (), today: Optional[date] = None, searched: bool = True) -> str:
    """
    A lookup in a === block for the model: each passage under a line that says where it
    is from, then the dated list. searched is False when the list alone answers the
    question and no passages were looked for.
    """
    lines = [f'=== COURSE MATERIAL for "{query}" ===']
    if not hits and searched:
        lines.append("nothing in the course material matches. tell them u couldnt find it there" if not rows
                     else "no passage in the course material matches.")
    elif hits and (label or label_of(hits)) == "loose":
        lines.append("the passages are only loosely related to what was asked. use one only if it really answers it. if none "
                     "does, say u couldnt find exactly that, then give the closest thing here and where it is from")
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
                   due: Optional[str] = None, asked: Optional[str] = None) -> Found:
    """
    Look words up in the courses in codes, or in every course when codes is empty.
    kind puts results of that kind first without leaving the others out. dated adds
    the list of assignments, and due narrows that list to "upcoming" or "past". asked
    is the message as it was written, which says which days and which assignments
    the list is wanted for.
    """
    asked = asked or words
    dated = dated or bool(due)
    items: List[dict] = []
    if dated:
        items = [item for code in codes or [None] for item in await asyncio.to_thread(store.assignments, code)]
        items.sort(key=lambda item: (item["date"], item["title"]))
    # "whats due this week" names nothing to look for. The list answers it, and passages
    # found for those words would only be noise. With no list, the slides and lectures are all there is.
    searched = not (dated and items and not has_topic(words))

    hits: List[Hit] = []
    if searched:
        vector = await embed(words)

        def search() -> List[Hit]:
            found: List[Hit] = []
            for code in codes or [None]:
                if kind:
                    found += store.search(words, vector, code, kind, since)
                found += store.search(words, vector, code, None, since)
            return found

        # The search is a few milliseconds of SQLite, kept off the event loop all the same
        seen = set()
        for hit in await asyncio.to_thread(search):
            key = (hit.course, hit.kind, hit.title, hit.locator)
            if key not in seen:
                seen.add(key)
                hits.append(hit)
        hits = sorted(hits, key=lambda hit: (bool(kind) and hit.kind != kind, -hit.similarity))[:RESULTS]

    rows: List[str] = []
    count = 0
    if dated and items:
        rows, count = assignment_rows(items, today, due, window_of(asked, today), bool(_PAST_WORDS.search(asked)))
    elif dated:
        rows = [f"- none posted{' for ' + ', '.join(codes) if codes else ''}"]
    names = {course["code"]: course["name"] for course in store.courses()}
    label = label_of(hits)
    return Found(format_hits(words, hits, names, label, rows, today, searched), hits, count, label)
