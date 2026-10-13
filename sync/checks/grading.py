"""
How a check run is graded. Each message the bot answered gets a pass or a miss by the
rules of its group, and a release ships only when every line of the gate is met.
Plain functions, so the rules themselves are tested.
"""
import json
import re
from typing import Dict, List

# What the judge may say about a reply to a flagged message:
#   correct            says what the material says
#   incomplete         right as far as it goes, leaves part out
#   wrong_right_place  read the right passage and still got it wrong
#   wrong_place        answered from a passage that is not about the question
#   not_found          said it could not find it
#   made_up            states something no passage it was shown supports
VERDICTS = ("correct", "incomplete", "wrong_right_place", "wrong_place", "not_found", "made_up")
# Groups whose message carries a flag, or follows one in a session, and is about class
FLAGGED = ("fact", "due", "assignment", "thai", "followup", "absent", "partial")
# Of those, the ones the material cannot answer. Saying so is the pass.
UNANSWERABLE = ("absent", "partial")

# The bot telling someone about the flags: "-course", a code such as "-csc340", or "add -c". A bare
# "-c" is not enough, since "gcc -c" is a compiler flag.
_FLAG_TALK = re.compile(r"(?<![\w-])-(course|[a-z]{3}\d{3})(?![\w-])|\b(add|use|put|with|type|include|end)\b[^.\n]{0,20}(?<![\w-])-c\b",
                        re.IGNORECASE)
_COURSE_TALK = re.compile(
    r"course material|saved material|\bur (class|course|slides|lectures?)\b|\b(the|ur|your) (slides|lecture notes)\b|"
    r"teams assignments", re.IGNORECASE)


def parse_verdict(answer: str) -> Dict:
    """The judge's answer as a verdict, a reason and what it thought of each passage. "unreadable" when it is not one."""
    found = re.search(r"\{.*\}", answer or "", re.DOTALL)
    try:
        data = json.loads(found.group(0)) if found else {}
    except ValueError:
        data = {}
    if data.get("verdict") not in VERDICTS:
        return {"verdict": "unreadable", "why": (answer or "")[:200], "passages": {}}
    marks = data.get("passages")
    passages = {str(number): str(mark) for number, mark in marks.items()} if isinstance(marks, dict) else {}
    return {"verdict": data["verdict"], "why": str(data.get("why") or ""), "passages": passages}


def course_talk(reply: str) -> bool:
    """Whether a reply brings up course material, the flags or a source line."""
    return bool(_COURSE_TALK.search(reply) or _FLAG_TALK.search(reply))


def grade(case: Dict, outcome: Dict) -> Dict:
    """
    Pass or miss for one message. outcome has the reply, how many lookups ran before
    the model was asked, how many searches the model made, the names of the sources
    it was shown, and the judge's verdict.
    """
    group = case["group"]
    reply = outcome["reply"]
    touched = outcome["lookups"] + outcome["searches"]
    if group in ("chat", "session_chat"):
        return {"ok": not touched and not course_talk(reply), "looked_up": bool(touched), "right_place": True,
                "verdict": None, "why": "course talk in the reply" if course_talk(reply) else ""}
    if group == "unflagged":
        points = bool(_FLAG_TALK.search(reply))
        return {"ok": not touched and points, "looked_up": bool(touched), "right_place": True, "verdict": None,
                "why": "" if points else "does not point to the flag"}
    # A follow-up may be answered from what the session already holds
    looked_up = bool(touched) or group == "followup"
    wanted = case.get("expect_source") or []
    right_place = not wanted or any(name.lower() in source.lower() for name in wanted for source in outcome["sources"])
    verdict = outcome.get("verdict")
    good = ("not_found",) if group in UNANSWERABLE else ("correct", "incomplete")
    return {"ok": looked_up and right_place and verdict in good, "looked_up": looked_up, "right_place": right_place,
            "verdict": verdict, "why": ""}


def _share(part: int, whole: int) -> str:
    return f"{part} of {whole} ({round(100 * part / whole)}%)" if whole else "none asked"


def gate(results: List[Dict]) -> Dict:
    """Whether a release may ship, line by line. Each result is a grade with its group and reply."""
    flagged = [r for r in results if r["group"] in FLAGGED]
    answerable = [r for r in flagged if r["group"] not in UNANSWERABLE]
    unanswerable = [r for r in flagged if r["group"] in UNANSWERABLE]
    chat = [r for r in results if r["group"] == "chat"]
    session_chat = [r for r in results if r["group"] == "session_chat"]
    unflagged = [r for r in results if r["group"] == "unflagged"]
    placed = sum(1 for r in answerable if r["right_place"])
    met = {
        "lookup on every flagged message": all(r["looked_up"] for r in flagged),
        "right place": not answerable or placed / len(answerable) >= 0.9,
        "nothing made up": not any(r["verdict"] == "made_up" for r in flagged),
        "says so when it is not there": all(r["ok"] for r in unanswerable),
        "no course talk in plain chat": all(r["ok"] for r in chat),
        "no course talk in other talk inside a session": all(r["ok"] for r in session_chat),
        "points to the flag when asked about class without one": all(r["ok"] for r in unflagged),
        "no empty links": not any("](<>)" in r["reply"] for r in results),
    }
    lines = {
        "lookup on every flagged message": _share(sum(1 for r in flagged if r["looked_up"]), len(flagged)),
        "right place": _share(placed, len(answerable)),
        "answers judged correct or incomplete": _share(sum(1 for r in answerable if r["verdict"] in ("correct", "incomplete")), len(answerable)),
        "made up": str(sum(1 for r in flagged if r["verdict"] == "made_up")),
        "says so when it is not there": _share(sum(1 for r in unanswerable if r["ok"]), len(unanswerable)),
        "no course talk in plain chat": _share(sum(1 for r in chat if r["ok"]), len(chat)),
        "no course talk in other talk inside a session": _share(sum(1 for r in session_chat if r["ok"]), len(session_chat)),
        "points to the flag when asked about class without one": _share(sum(1 for r in unflagged if r["ok"]), len(unflagged)),
    }
    failed = [line for line, ok in met.items() if not ok]
    return {"passed": not failed, "failed": failed, "lines": lines}
