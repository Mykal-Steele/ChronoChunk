"""
The one way course material reaches a reply. A lookup searches the passages (slides,
lectures, assignments) for the topic and, when the question is about dates, adds the
dated list of assignments. It says how good the match is, so the bot can tell a
right answer from the closest thing it found.
"""
import asyncio
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

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

# From this score up a passage is on the topic. Measured on 300 passages that a judge model read
# against their question (check run of 2026-10-14): at 0.55 and over, 41 answered it, 27 were related
# and 3 were off. Under 0.55 the score says little: 58 answered, 86 were related, 85 were off, in every
# band alike. So a lower score is not held against a passage, and the model reads it to decide.
STRONG = 0.55
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
    # strong, unsure or nothing. For the log and the check. The model is not told: it reads the passages itself.
    label: str
    # What each number in the text stands for: number -> (name, link). "A" is the assignment list.
    sources: Dict[str, Tuple[str, str]] = field(default_factory=dict)


def about_dates(message: str) -> bool:
    return bool(_DATE_WORDS.search(message))


def label_of(hits: Sequence[Hit]) -> str:
    if not hits:
        return "nothing"
    return "strong" if max(hit.similarity for hit in hits) >= STRONG else "unsure"


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


def _due_at(item: dict) -> Optional[datetime]:
    """The moment an assignment is due, when the course file stores it."""
    try:
        return datetime.fromisoformat(item.get("due") or "")
    except ValueError:
        return None


def _is_past(item: dict, now: datetime) -> bool:
    due = _due_at(item)
    if due is not None:
        return due < now
    return bool(item["date"]) and item["date"] < now.astimezone(BANGKOK).date().isoformat()


def _span(minutes: float) -> str:
    if minutes < 60:
        count = max(1, round(minutes))
        return f"{count} minute{'' if count == 1 else 's'}"
    count = round(minutes / 60)
    return f"{count} hour{'' if count == 1 else 's'}"


def _row(item: dict, now: datetime) -> str:
    """One assignment and how far off it is, like 'CSC345 "Lab 7", due today (13 Oct 2026) at 23:59, 9 hours left, 5 points'."""
    head = f'{item["course"]} "{item["title"]}"'
    try:
        day = date.fromisoformat(item["date"])
    except ValueError:
        return f"{head}, no due date"
    due = _due_at(item)
    days = (day - now.astimezone(BANGKOK).date()).days
    if days > 1:
        when = f"due in {days} days"
    elif days < -1:
        when = f"was due {-days} days ago"
    elif days == 0:
        when = "was due today" if due is not None and due < now else "due today"
    else:
        when = {1: "due tomorrow", -1: "was due yesterday"}[days]
    line = f"{head}, {when} ({day.day} {day:%b %Y})"
    # A course file from before due times were stored has the time in the assignment's text
    written = re.search(r"^Due \d{4}-\d{2}-\d{2} (\d{2}:\d{2})", item["text"], re.MULTILINE)
    if due is not None:
        line += f" at {due.astimezone(BANGKOK):%H:%M}"
        if days == 0:
            minutes = (due - now).total_seconds() / 60
            line += f", {_span(abs(minutes))} {'left' if minutes >= 0 else 'ago'}"
    elif written:
        line += f" at {written.group(1)}"
    points = (item.get("meta") or {}).get("points")
    in_text = re.search(r"^([\d.]+ points)$", item["text"], re.MULTILINE)
    if isinstance(points, (int, float)):
        line += f", {points:g} points"
    elif in_text:
        line += f", {in_text.group(1)}"
    return line


def assignment_rows(items: List[dict], now: datetime, due: Optional[str] = None,
                    window: Optional[Tuple[date, date]] = None, everything: bool = False) -> Tuple[List[str], int]:
    """
    Assignments as one line each, and how many of them. Only the ones the question is
    about: the days of window when it names days, "upcoming" or "past" for due, all of
    them for everything. Otherwise only what is still to come, with a count of the past
    ones. Still to come is first, the soonest on top, then what has passed, the latest on top.
    """
    undated = [item for item in items if not item["date"]]
    upcoming = [item for item in items if item["date"] and not _is_past(item, now)]
    past = [item for item in reversed(items) if _is_past(item, now)]
    if window:
        first, last = window
        within = [item for item in items if first.isoformat() <= item["date"] <= last.isoformat()]
        days = f"from {first.day} {first:%b} to {last.day} {last:%b %Y}"
        lines = ([f"due {days}:"] + [f"- {_row(item, now)}" for item in within] if within
                 else [f"nothing in the teams assignments is due {days}"])
        later = [item for item in items if item["date"] > last.isoformat()]
        if later:
            lines.append(f"next after that: {_row(later[0], now)}")
        return lines, len(within)
    if due:
        chosen, left_out = {"upcoming": upcoming + undated, "past": past}[due], 0
    elif everything:
        chosen, left_out = upcoming + undated + past, 0
    else:
        # A question about homework is about what is still to do
        chosen, left_out = upcoming + undated, len(past)
    chosen = chosen[:MAX_ROWS]
    lines = [f"- {_row(item, now)}" for item in chosen]
    if not chosen and left_out:
        lines.append("nothing in the teams assignments is due from now on")
    if left_out:
        lines.append(f"{left_out} past {'one is' if left_out == 1 else 'ones are'} not listed")
    return lines, len(chosen)


def _day(day: str) -> str:
    try:
        parsed = date.fromisoformat(day)
    except ValueError:
        return ""
    return f"{parsed.day} {parsed:%b %Y}"


def source_label(hit: Hit) -> str:
    """How a passage is named in a reply's source line: what it is, where in it, and from which day."""
    name = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", hit.title).replace("[", "(").replace("]", ")")
    day = _day(hit.date)
    if hit.kind == "assignment":
        return f"{name} (assignment, due {day})" if day else f"{name} (assignment)"
    if hit.kind == "transcript" and day:
        return f"{hit.course} lecture {day}" + (f", {hit.locator}" if hit.locator else "")
    label = f"{name}, {hit.locator}" if hit.locator else name
    return f"{label} ({day})" if day else label


# The model sometimes puts it at the end of its last sentence instead of on a line of its own
_SOURCE_LINE = re.compile(r"(?:^|(?<=\s))-# Source:(.*)$", re.MULTILINE | re.IGNORECASE)
_MARK = re.compile(r"\[(\d+|A)\]")


def render_sources(text: str, sources: Dict[str, Tuple[str, str]]) -> str:
    """
    Write a reply's source line from the numbers the model put on it. The model never
    types a link: it names the passages it used, and their names, dates and links are
    filled in here. A number that stands for nothing is dropped, and a line left with
    nothing goes.
    """
    lines = list(_SOURCE_LINE.finditer(text))
    if not lines:
        return text
    line = lines[-1]
    named = [sources[key] for key in dict.fromkeys(_MARK.findall(line.group(1))) if key in sources]
    # A number set in the answer itself says nothing to the person reading it. Code is left as it
    # is, and so is a number glued to a word, since "rows[2]" is an index and not a passage.
    def without_marks(prose: str) -> str:
        return re.sub(r" ?(?<![\w\]])((?:\[(?:\d+|A)\])+)(?![\w(\[])",
                      lambda marks: "" if all(key in sources for key in _MARK.findall(marks.group(1))) else marks.group(0), prose)
    pieces = re.split(r"(```.*?```|`[^`\n]*`)", text[:line.start()], flags=re.DOTALL)
    body = "".join(piece if index % 2 else without_marks(piece) for index, piece in enumerate(pieces))
    rest = text[line.end():]
    if not named:
        return (body.rstrip() + rest).rstrip()
    shown = ", ".join(f"[{name}](<{link}>)" if link else name for name, link in named)
    return f"{body.rstrip()}\n-# Source: {shown}{rest}"


def format_hits(query: str, hits: List[Hit], names: dict, rows: Sequence[str] = (), today: Optional[date] = None,
                searched: bool = True, start: int = 1, repeats: Sequence[str] = ()) -> str:
    """
    A lookup in a === block for the model: each passage under a numbered line that says
    where it is from, then the dated list. searched is False when the list alone
    answers the question and no passages were looked for. start is the first number,
    so the passages of a later lookup do not reuse the numbers of an earlier one. repeats
    are the numbers of passages this search found that were shown before: they are named, not sent again.
    """
    lines = [f'=== COURSE MATERIAL for "{query}" ===']
    shown = " ".join(f"[{number}]" for number in repeats)
    if repeats and not hits:
        lines.append(f"nothing new: this search found only what was already shown as {shown}. answer from those, "
                     "or say u couldnt find it")
    elif repeats:
        lines.append(f"this search also found what was already shown as {shown}. their text is not repeated here")
    elif not hits and searched:
        lines.append("nothing in the course material matches. tell them u couldnt find it there" if not rows
                     else "no passage in the course material matches.")
    for number, hit in enumerate(hits, start):
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
        lines.append(f"[A] ASSIGNMENTS posted in teams{when}:")
        lines.extend(rows)
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    lines.append("=== END ===")
    return "\n".join(lines)


def _key(hit: Hit) -> tuple:
    """What makes two results the same passage."""
    return (hit.course, hit.kind, hit.title, hit.locator)


async def retrieve(store: CourseStore, embed: Callable[[str], Awaitable[List[float]]], words: str, codes: Sequence[str],
                   now: datetime, kind: Optional[str] = None, since: Optional[str] = None, dated: bool = False,
                   due: Optional[str] = None, asked: Optional[str] = None, first_number: int = 1,
                   seen: Optional[Dict[tuple, str]] = None) -> Found:
    """
    Look words up in the courses in codes, or in every course when codes is empty.
    kind puts results of that kind first without leaving the others out. dated adds
    the list of assignments, counted from now, and due narrows that list to "upcoming" or "past". asked
    is the message as it was written, which says which days and which assignments
    the list is wanted for. first_number is the number the first passage gets. seen
    holds the passages this conversation was already shown, by the number each has.
    """
    asked = asked or words
    dated = dated or bool(due)
    today = now.astimezone(BANGKOK).date()
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
        found_keys = set()
        for hit in await asyncio.to_thread(search):
            if _key(hit) not in found_keys:
                found_keys.add(_key(hit))
                hits.append(hit)
        hits = sorted(hits, key=lambda hit: (bool(kind) and hit.kind != kind, -hit.similarity))[:RESULTS]

    rows: List[str] = []
    count = 0
    if dated and items:
        rows, count = assignment_rows(items, now, due, window_of(asked, today), bool(_PAST_WORDS.search(asked)))
    elif dated:
        rows = [f"- none posted{' for ' + ', '.join(codes) if codes else ''}"]
    names = {course["code"]: course["name"] for course in store.courses()}
    label = label_of(hits)
    # A passage shown earlier in the conversation keeps its number and is not sent again
    seen = seen if seen is not None else {}
    repeats = list(dict.fromkeys(seen[_key(hit)] for hit in hits if _key(hit) in seen))
    hits = [hit for hit in hits if _key(hit) not in seen]
    seen.update({_key(hit): str(number) for number, hit in enumerate(hits, first_number)})
    sources = {str(number): (source_label(hit), hit.url) for number, hit in enumerate(hits, first_number)}
    if dated:
        none = f": none posted{' for ' + ', '.join(codes) if codes else ''}" if not items else " list"
        sources["A"] = (f"teams assignments{none}", "")
    return Found(format_hits(words, hits, names, rows, today, searched, first_number, repeats), hits, count, label, sources)

