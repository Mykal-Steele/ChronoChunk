"""
Course facts from the Teams Assignments app: what is due, when, and what it asks for.

Only course facts are kept: title, instructions, due and close dates, points and the
names of attached files. An assignment is material of its course, not anyone's work,
so nothing about a person's submissions, grades or classmates is stored. The app's
calls cannot be made by a script directly (the sign-in token stays encrypted), so the
sync reads what the Assignments pages themselves load.
"""
from __future__ import annotations

import html as html_lib
import re
import time
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sync.material import BANGKOK

ASSIGNMENTS_HOST = "assignments.edu.cloud.microsoft"
# The app runs in a frame inside Teams and has an address of its own for every class and assignment
APP = f"https://{ASSIGNMENTS_HOST}"

# Tabs a class's list shows. Completed holds past work, Upcoming and Past due hold what is next.
TABS = ("Upcoming", "Past due", "Completed")

_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_LIST_PATH = re.compile(rf"/edu/classes/{_ID}/assignments$")
_DETAIL_PATH = re.compile(rf"/edu/classes/{_ID}/assignments/{_ID}$")

# How long the app may take to show up inside Teams
APP_SECONDS = 40
# How long a class's list may take to show its tabs
LIST_SECONDS = 25
# How long a tab may take to answer after a click, or to show up once the list has answered.
# The tab that is already open does not load again.
TAB_SECONDS = 4
# How long one assignment may take to open. It took about a second in every test.
DETAIL_SECONDS = 15
# One real run in four lost a single assignment to a page that never loaded it, so it is asked for again
DETAIL_TRIES = 2


def clean_html(instructions: object) -> str:
    """Instructions as plain text. The app sends {"content": "<div>...", "contentType": "html"}."""
    if not isinstance(instructions, dict):
        return ""
    content = str(instructions.get("content") or "")
    if not content:
        return ""
    # What sits inside a style or script block is not something a person reads
    content = re.sub(r"<(style|script)\b.*?</\1\s*>", " ", content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", content)
    text = html_lib.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def dedupe(items: Iterable[dict]) -> List[dict]:
    """One copy per assignment id, first one wins."""
    seen: Set[str] = set()
    out: List[dict] = []
    for item in items:
        key = str(item.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def parse_points(grading: object) -> str:
    """"5 points" from the grading block, "" when it carries no points."""
    if not isinstance(grading, dict):
        return ""
    points = grading.get("maxPoints")
    return f"{points} points" if isinstance(points, (int, float)) else ""


# Course facts only. Everything else a list or detail call carries (submissions,
# submissionAggregates, member lists, user ids, who it was assigned to) is personal
# and is dropped before anything is kept.
KEPT_FIELDS = ("id", "classId", "displayName", "instructions", "dueDateTime", "closeDateTime",
               "assignedDateTime", "createdDateTime", "grading", "rubric", "resources", "webUrl")


def strip(item: dict) -> dict:
    """Only the course facts of an assignment. Personal fields never leave the browser."""
    kept = {key: item.get(key) for key in KEPT_FIELDS if item.get(key) is not None}
    rubric = kept.get("rubric")
    if isinstance(rubric, dict):
        kept["rubric"] = {"title": rubric.get("title")} if rubric.get("title") else None
        if kept["rubric"] is None:
            kept.pop("rubric")
    resources = kept.get("resources")
    if isinstance(resources, list):
        names = _resource_names(resources)
        kept["resources"] = [{"resource": {"displayName": name}} for name in names]
    return kept


def wanted(item: dict, semester_start: str) -> bool:
    """An assignment of this semester that students can still be asked about."""
    if str(item.get("status") or "assigned") != "assigned":
        return False
    # The due date says which semester the work belongs to. Work with no due date goes by when it was given out.
    moment = str(item.get("dueDateTime") or item.get("assignedDateTime") or item.get("createdDateTime") or "")
    return moment[:10] >= semester_start


def course_facts(entries: Iterable[dict], semester_start: str, details: Optional[Dict[str, dict]] = None) -> List[dict]:
    """What is kept of the assignments a class list sent: this semester's, course facts only."""
    kept: List[dict] = []
    for entry in dedupe(entries):
        # Decided on the whole entry, because strip() takes the status away
        if not wanted(entry, semester_start):
            continue
        detail = (details or {}).get(str(entry["id"])) or {}
        # The list leaves instructions, points and attached files empty. Opening the assignment has them.
        kept.append(strip({**entry, **{key: value for key, value in detail.items() if value is not None}}))
    return kept


def _resource_names(resources: object) -> List[str]:
    names: List[str] = []
    if not isinstance(resources, list):
        return names
    for entry in resources:
        if not isinstance(entry, dict):
            continue
        inner = entry.get("resource")
        resource = inner if isinstance(inner, dict) else entry
        name = str(resource.get("displayName") or "").strip()
        if name:
            names.append(name)
    return names


def _bangkok(moment: object) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(moment or "").replace("Z", "+00:00")).astimezone(BANGKOK)
    except ValueError:
        return None


def due_day(item: Dict) -> str:
    """The Bangkok date an assignment is due, as YYYY-MM-DD. '' when it has no due date."""
    due = _bangkok(item.get("dueDateTime"))
    return due.date().isoformat() if due else ""


def due_time(item: Dict) -> str:
    """The moment an assignment is due, in Bangkok time with its zone: 2026-11-16T23:59:00+07:00. '' when it has none."""
    due = _bangkok(item.get("dueDateTime"))
    return due.isoformat(timespec="seconds") if due else ""


def facts_meta(item: Dict) -> Dict:
    """What a list of assignments shows beside the title and the due time: points, when it closes, attached files."""
    meta: Dict = {}
    grading = item.get("grading")
    if isinstance(grading, dict) and isinstance(grading.get("maxPoints"), (int, float)):
        meta["points"] = grading["maxPoints"]
    closes = _bangkok(item.get("closeDateTime"))
    if closes:
        meta["closes"] = closes.isoformat(timespec="seconds")
    attached = _resource_names(item.get("resources"))
    if attached:
        meta["attached"] = attached
    return meta


def assignment_text(item: Dict) -> str:
    """One assignment as a single chunk of text: facts only, no personal fields."""
    lines = [str(item.get("displayName") or "").strip()]
    due, close = _bangkok(item.get("dueDateTime")), _bangkok(item.get("closeDateTime"))
    if due:
        lines.append(f"Due {due:%Y-%m-%d %H:%M} Bangkok time")
    if close and close != due:
        lines.append(f"Closes {close:%Y-%m-%d %H:%M} Bangkok time")
    points = parse_points(item.get("grading"))
    if points:
        lines.append(points)
    instructions = clean_html(item.get("instructions"))
    if instructions:
        lines.append(instructions)
    for name in _resource_names(item.get("resources")):
        lines.append(f"Attached: {name}")
    rubric = item.get("rubric")
    if isinstance(rubric, dict) and rubric.get("title"):
        lines.append(f"Rubric: {rubric['title']}")
    return "\n".join(line for line in lines if line)


def recorded(path: str) -> bool:
    """Whether an answer of the app is read at all: a class's list and an assignment itself, nothing else."""
    return bool(_LIST_PATH.search(path) or _DETAIL_PATH.search(path))


def _class_lists(answers: Sequence[dict], class_id: str) -> List[dict]:
    return [answer["body"] for answer in answers
            if answer["status"] == 200 and answer["path"].endswith(f"/classes/{class_id}/assignments")
            and isinstance(answer["body"], dict)]


def listed(answers: Sequence[dict], class_id: str) -> List[dict]:
    """Every assignment the recorded lists of one class hold, each once."""
    return dedupe(entry for body in _class_lists(answers, class_id) for entry in body.get("value") or []
                  if isinstance(entry, dict))


def more_to_load(answers: Sequence[dict], class_id: str) -> bool:
    """Whether the last list a class sent says more rows follow."""
    lists = _class_lists(answers, class_id)
    return bool(lists and lists[-1].get("@odata.nextLink"))


def opened(answers: Sequence[dict], class_id: str, assignment_id: str) -> Optional[dict]:
    """The full assignment the app loaded when it was opened, None when it has not arrived."""
    for answer in answers:
        if (answer["status"] == 200 and answer["path"].endswith(f"/classes/{class_id}/assignments/{assignment_id}")
                and isinstance(answer["body"], dict) and answer["body"].get("id")):
            return answer["body"]
    return None


def _app_frame(page):
    """The frame the Assignments app runs in. Raises while there is none, which a wait reads as "not yet"."""
    for frame in page.frames:
        if ASSIGNMENTS_HOST in (frame.url or ""):
            return frame
    raise LookupError("the Assignments app is not open")


def _until(page, test: Callable[[], object], seconds: float) -> bool:
    """Wait until test is true. False when it still is not after this many seconds."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if test():
                return True
        except Exception:
            # The frame is between two pages
            pass
        page.wait_for_timeout(200)
    return False


def _read_list(page, recorder, class_id: str) -> Optional[List[dict]]:
    """Open one class's list and go through its tabs. None when the list did not load."""
    start = len(recorder.answers)
    _app_frame(page).goto(f"{APP}/classes/{class_id}/list", wait_until="commit")
    tabs = lambda: _app_frame(page).get_by_role("tab").count() >= len(TABS)
    if not _until(page, lambda: tabs() or _class_lists(recorder.answers[start:], class_id), LIST_SECONDS):
        return None
    # A class that never had an assignment answers with an empty list and shows no tabs at all
    if not _until(page, tabs, TAB_SECONDS):
        return listed(recorder.answers[start:], class_id)
    for tab in TABS:
        mark = len(recorder.answers)
        _app_frame(page).get_by_role("tab", name=re.compile(tab)).first.click()
        _until(page, lambda mark=mark: _class_lists(recorder.answers[mark:], class_id), TAB_SECONDS)
        # A list comes 20 rows at a time, and the next rows load when the last one scrolls into view
        while more_to_load(recorder.answers[mark:], class_id):
            seen = len(recorder.answers)
            _app_frame(page).get_by_role("listitem").last.scroll_into_view_if_needed()
            if not _until(page, lambda seen=seen: _class_lists(recorder.answers[seen:], class_id), TAB_SECONDS):
                break
    return listed(recorder.answers[start:], class_id)


def _open(page, recorder, class_id: str, assignment_id: str) -> Optional[dict]:
    """Open one assignment and hand back what the app loaded for it. None when it did not come, after a second try."""
    for _ in range(DETAIL_TRIES):
        mark = len(recorder.answers)
        try:
            _app_frame(page).goto(f"{APP}/classes/{class_id}/assignments/{assignment_id}", wait_until="commit")
        except Exception:
            continue
        if _until(page, lambda mark=mark: opened(recorder.answers[mark:], class_id, assignment_id), DETAIL_SECONDS):
            return opened(recorder.answers[mark:], class_id, assignment_id)
    return None


def collect(context, class_ids: Sequence[str], semester_start: str) -> Tuple[List[dict], List[str]]:
    """
    The assignments of every class, past ones included, as course facts, and what went
    wrong on the way. A class's list has no instructions, points or attached files, so
    every assignment of this semester is opened once for those.

    The list a class sends also carries the signed-in person's own submissions and
    whether they finished. course_facts() drops all of that before anything is
    returned. The calls that are only about people (/me/work, /submissions, /members)
    are never read.
    """
    from sync.session import Recorder, open_teams

    recorder = Recorder(context, ASSIGNMENTS_HOST, recorded)
    page = open_teams(context)
    page.get_by_role("button", name=re.compile(r"^Assignments")).first.click()
    if not _until(page, lambda: _app_frame(page), APP_SECONDS):
        raise RuntimeError("the Assignments app did not open in Teams")

    facts: List[dict] = []
    problems: List[str] = []
    for class_id in class_ids:
        try:
            entries = _read_list(page, recorder, class_id)
        except Exception as error:
            problems.append(f"assignments of class {class_id}: {str(error)[:200]}")
            continue
        if entries is None:
            problems.append(f"assignments of class {class_id}: the list did not load")
            continue
        details: Dict[str, dict] = {}
        for entry in entries:
            if not wanted(entry, semester_start):
                continue
            detail = _open(page, recorder, class_id, str(entry["id"]))
            if detail is not None:
                details[str(entry["id"])] = detail
            else:
                problems.append(f"assignment {entry.get('displayName')!r} could not be opened, kept without its instructions")
        facts.extend(course_facts(entries, semester_start, details))
    return facts, problems
