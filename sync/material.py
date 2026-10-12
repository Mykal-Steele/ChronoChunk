"""
The rules that turn what a class site holds into searchable chunks: which files count
as course material, what a course is called, and how a transcript is cut up. Plain
functions with no network and no model, so they can be tested on their own.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

DOCUMENTS = (".pdf", ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".txt")
OFFICE = (".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls")
VIDEO = (".mp4", ".mov", ".webm", ".mkv")
# Student Work holds the signed-in person's own submissions
SKIPPED_LIBRARIES = ("Student Work",)

# About this many seconds of speech per chunk. Each chunk starts with the last lines
# of the one before it, so a sentence cut at the edge is whole in one of them.
CHUNK_SECONDS = 75
OVERLAP_ENTRIES = 2
# A chunk longer than this is cut. One slide or 75 seconds of speech stays well under it.
CHUNK_MAX_CHARS = 6000
# A page with less text than this says nothing worth finding
CHUNK_MIN_CHARS = 20
# This many Thai letters in a text mean it needs translating
THAI_LETTERS = 8

BANGKOK = timezone(timedelta(hours=7))

_CODE = r"[A-Z]{3}\d{3}"


@dataclass(frozen=True)
class Course:
    code: str
    name: str
    aliases: Tuple[str, ...]


def extension(name: str) -> str:
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def course_from_title(title: str, prefix: str) -> Optional[Course]:
    """
    The course a class team is for, from its title. "1-2569_CSC340/BIF651 ARTIFICIAL
    INTELLIGENCE" is CSC340, also known as BIF651. None when the title has no course code.
    """
    rest = title[len(prefix):].lstrip("_- ") if title.startswith(prefix) else title
    codes = re.match(rf"({_CODE})((?:\s*/\s*{_CODE})*)", rest)
    if not codes:
        return None
    aliases = tuple(re.findall(_CODE, codes.group(2)))
    name = rest[codes.end():]
    name = re.sub(r"^\s*\([^)]*\)", "", name)                      # a section mark such as "(G.7)"
    name = re.sub(rf"\s+{_CODE}\s*\(Pre\).*$", "", name)             # the prerequisites some titles end with
    name = re.sub(r"\s*-\s*Mentoring.*$", "", name, flags=re.IGNORECASE)   # a private channel's own site
    name = " ".join(name.split())
    if name.isupper():
        name = name.title()
    return Course(codes.group(1), name or codes.group(1), aliases)


def wanted(library: str, folder: str, name: str, modified: str, semester_start: str) -> Tuple[bool, str]:
    """Whether a file is course material to read, and when it is not, why."""
    if library in SKIPPED_LIBRARIES:
        return False, "the student's own work"
    if "/Recordings" in folder:
        return False, "a recording"
    kind = extension(name)
    if kind not in DOCUMENTS:
        return False, f"type {kind or 'unknown'}"
    if modified[:10] < semester_start:
        return False, "older than the semester"
    return True, ""


def local_day(moment: str) -> str:
    """The Bangkok date of a UTC time such as 2026-08-04T02:00:49Z, '' when it cannot be read."""
    try:
        parsed = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return parsed.astimezone(BANGKOK).date().isoformat()


def recording_title(name: str) -> str:
    """A recording's file name without the extension and the suffix Teams adds."""
    title = name.rsplit(".", 1)[0]
    title = re.sub(r"-\d{8}_\d{6}(UTC)?-Meeting (Recording|Transcript)$", "", title)
    return re.sub(r"-Meeting (Recording|Transcript)$", "", title)


def _seconds(offset: str) -> float:
    hours, minutes, rest = offset.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def clock(total: float) -> str:
    return f"{int(total) // 3600}:{int(total) % 3600 // 60:02d}:{int(total) % 60:02d}"


def transcript_chunks(entries: List[dict]) -> List[Tuple[str, str]]:
    """
    A transcript as (start time, text) chunks of about CHUNK_SECONDS each. Speaker names
    are left out: they are often a room's account, and sometimes a student.
    """
    spoken = [entry for entry in entries if (entry.get("text") or "").strip()]
    groups: List[List[dict]] = []
    group: List[dict] = []
    for entry in spoken:
        group.append(entry)
        if _seconds(entry["endOffset"]) - _seconds(group[0]["startOffset"]) >= CHUNK_SECONDS:
            groups.append(group)
            group = group[-OVERLAP_ENTRIES:]
    # What is left is new speech only when it holds more than the overlap carried over
    if len(group) > (OVERLAP_ENTRIES if groups else 0):
        groups.append(group)
    return [(clock(_seconds(group[0]["startOffset"])), " ".join(entry["text"].strip() for entry in group)[:CHUNK_MAX_CHARS])
            for group in groups]


def has_thai(text: str) -> bool:
    return len(re.findall(r"[ก-๛]", text)) >= THAI_LETTERS


def embedded_text(course: Course, kind: str, title: str, section: str, locator: str, text: str) -> str:
    """What is embedded for a chunk: its text under a line that says where it is from."""
    where = " | ".join(part for part in (f"{course.code} {course.name}", kind, title, section, locator) if part)
    return f"{where}\n{text}"
