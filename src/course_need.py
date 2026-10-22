"""
The one call before a reply that decides whether the answer needs the user's
Microsoft Teams course material, and records what they actually asked for.

This is a judgment call, not a keyword match. The model is shown the courses and
the kinds of material they hold, the message it replies to, and the recent chat,
so it can tell a course question from a rewrite, a roleplay, or an ordinary chat.
"""
import json
from dataclasses import dataclass, field
from typing import List, Sequence

from src.study import MAX_STUDY_ITEMS

# The fields the model must fill in. They are sent as a forced tool call, so the
# answer is structured even on deployments that do not support JSON response format.
COURSE_CHECK_TOOL = "check_course_need"


@dataclass
class CourseNeed:
    """What the message needs, from the classifier that read it in context."""
    needs_course: bool = False
    # Courses the answer is about. Empty means every course, or no course when needs_course is false.
    codes: List[str] = field(default_factory=list)
    # The English topic to search, written by the model. Empty means no search, or let the reply model search.
    search_query: str = ""
    # A short reading of the request, including what "this/that/it" points at.
    context: str = ""
    # True when they want an earlier message transformed rather than a new answer.
    rewrite: bool = False
    # The shape of the reply, independent of whether it needs course material.
    study_kind: str = "none"  # "none", "quiz", "short" or "guide"
    # How many questions they asked for, 0 when they did not name a number.
    study_items: int = 0
    # True when the answer depends on how a named app, website or UI works and is not in the course material.
    needs_web: bool = False
    # True when they want the full explanation rather than the short version.
    detail: bool = False


def check_tool(codes: Sequence[str]) -> dict:
    """The forced tool that returns the classification. The course list is fixed to the real ones."""
    course: dict = {"type": "string", "description": "A course code from the catalog."}
    if codes:
        course["enum"] = list(codes)
    return {
        "type": "function",
        "function": {
            "name": COURSE_CHECK_TOOL,
            "description": "Decide whether answering the current message needs the user's Microsoft Teams course material, "
                           "and record what they actually want.",
            "parameters": {
                "type": "object",
                "properties": {
                    "needs_course": {
                        "type": "boolean",
                        "description": "True only when a correct answer needs facts from the course catalog "
                                       "(slides, lecture recordings, class posts or assignments).",
                    },
                    "course_codes": {
                        "type": "array",
                        "items": course,
                        "description": "The courses the answer is about. Empty when needs_course is false, "
                                       "or when every course may hold the answer.",
                    },
                    "search_query": {
                        "type": "string",
                        "description": "The English topic to look up, ten words or fewer, with no instructions "
                                       "and no quiz words. Empty when needs_course is false.",
                    },
                    "context": {
                        "type": "string",
                        "description": "One or two sentences saying what they actually want, including what "
                                       "'this', 'that' or 'it' points at and any roleplay or style they asked for.",
                    },
                    "is_rewrite": {
                        "type": "boolean",
                        "description": "True when they want an earlier message said again another way, "
                                       "not a new answer about a course.",
                    },
                    "study_kind": {
                        "type": "string",
                        "enum": ["none", "quiz", "short", "guide"],
                        "description": "The shape they asked for: multiple-choice quiz, short-answer practice, "
                                       "or a study guide. 'none' for ordinary chat, including a general explanation.",
                    },
                    "study_items": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": MAX_STUDY_ITEMS,
                        "description": "The number of questions they named, or 0 when they named none. "
                                       "This is independent of whether the material is needed.",
                    },
                    "wants_detail": {
                        "type": "boolean",
                        "description": "True when they asked for a full explanation, a chapter walkthrough, "
                                       "or another answer that needs more room than short chat.",
                    },
                    "needs_web": {
                        "type": "boolean",
                        "description": "True when a correct answer depends on how a named app, website, "
                                       "widget or UI setting works and the course material does not cover it. "
                                       "False for ordinary chat and for facts the course material holds.",
                    },
                },
                "required": ["needs_course", "course_codes", "search_query", "context", "is_rewrite",
                             "study_kind", "study_items", "wants_detail", "needs_web"],
                "additionalProperties": False,
            },
        },
    }


def check_prompt(catalog: str, course_mode: str) -> str:
    """The system prompt for the classifier. It reads the catalog, not a list of trigger words."""
    return f"""You route one Discord message for a study assistant. You do not answer it.

COURSE CATALOG: what the assistant has copied from the user's Microsoft Teams:
{catalog}

COURSE MODE: {course_mode}

Read the recent conversation, the message they replied to, and the current message. Then decide:

1. needs_course
True only when a correct answer needs facts that are in COURSE CATALOG: what a specific lecture, slide,
class post, assignment, teacher or course said; when class work is due; what a course covers; or course
study material made from those files.
False for ordinary chat, general knowledge, coding, weather, writing, translation, roleplay, and for
rewriting or rephrasing an earlier message. A general question ("what is PCA") is false unless the
conversation or an explicit course flag shows they mean their class. A general quiz, study guide or
explanation is false too unless they name a course, reply to course material, or the conversation is
clearly about one. When unsure, false.

2. course_codes
Only codes that appear in COURSE CATALOG. Leave empty when needs_course is false, or when the answer could
come from any course. If they named a class in their own words, match it to the catalog yourself.

3. search_query
When needs_course is true, the English topic to look up: the subject of the question, ten words or fewer,
not the instruction. For a quiz or study guide, use the topics it should cover. Never include "make",
"quiz", "exam", "explain" or the course name. Empty otherwise.

4. context
What they actually want, in one or two sentences, based on the current message, the message it replies to,
and the recent chat. Keep the referent clear: if they say "this", "that" or "it", say what it points at.
Keep roleplay, tone and style requests. For a rewrite, say what is being rewritten and what the new shape
is. This is a reading of their request for the answering model, not an answer to it.

5. is_rewrite
True when they want an earlier message transformed (rewritten, rephrased, turned into a roleplay, explained
in another style, translated, made shorter or longer). False when they ask a new question. A rewrite can
still need course material if they also ask for a course fact; in that case set needs_course true too.

6. study_kind and study_items
Set study_kind from what they want made, not from a list of trigger words. "quiz" is multiple-choice or
practice questions, "short" is short-answer or theory questions, and "guide" is a study guide, revision
notes or exam prep. Set study_items to the number they named, capped at 100; use 0 when they named no
number. A study request can have needs_course false: a general quiz about a well-known subject is still a
quiz, it just does not need their class files. "quiz me on deadlocks" is a quiz with 0 items.

7. wants_detail
True when they ask for the full explanation, a chapter walkthrough, step-by-step teaching, or another
answer that needs more room than short chat. False for a quick definition or ordinary chat.

8. needs_web
True when a correct answer depends on how a named app, website, widget or UI setting works and the course
material does not cover it. Examples: "what does Drop do in Orange", "why is this button greyed out in
Teams". False for ordinary chat, general knowledge, coding, weather, and for facts the course material holds.
This can be true while needs_course is false.

The message they replied to is what "this", "that" and "it" mean. Do not treat exam dates, rooms, marks or
rules as the topic of a rewrite unless they explicitly ask about them.
"""


def parse_check(arguments: str, codes: Sequence[str]) -> CourseNeed:
    """Read the forced tool arguments. Anything malformed falls back to no course lookup."""
    try:
        data = json.loads(arguments)
    except (TypeError, ValueError):
        return CourseNeed()
    if not isinstance(data, dict):
        return CourseNeed()
    allowed = list(codes)
    picked = data.get("course_codes")
    picked = picked if isinstance(picked, list) else []
    chosen = list(dict.fromkeys(str(code) for code in picked if str(code) in allowed))
    query = data.get("search_query")
    context = data.get("context")
    kind = data.get("study_kind")
    kind = kind if kind in ("quiz", "short", "guide") else "none"
    try:
        items = int(data.get("study_items") or 0)
    except (TypeError, ValueError):
        items = 0
    items = max(0, min(items, MAX_STUDY_ITEMS))
    return CourseNeed(
        needs_course=data.get("needs_course") is True,
        codes=chosen,
        search_query=(query.strip()[:300] if isinstance(query, str) else ""),
        context=(context.strip()[:800] if isinstance(context, str) else ""),
        rewrite=data.get("is_rewrite") is True,
        study_kind=kind,
        study_items=items,
        detail=data.get("wants_detail") is True,
        needs_web=data.get("needs_web") is True,
    )
