"""Spots a request for study material, so only that reply gets a fixed shape and more room.

The bot talks like itself in every reply. A quiz or a study guide needs a shape that normal
chat does not: a set number of questions, four options each, and an answer key. Those rules
are added for the one reply that asks for them, never to the personality prompt.
"""
import re
from dataclasses import dataclass
from typing import Optional

# Nobody needs more than this in one Discord reply. A bigger number is treated as this.
MAX_STUDY_ITEMS = 100
# A quiz with no number in the request gets this many questions.
DEFAULT_QUIZ_ITEMS = 10

_QUIZ = re.compile(
    r"\b(?:quiz(?:zes)?|mcqs?|multiple[- ]choice(?:\s+questions?)?|practice\s+(?:questions?|exam|test)|"
    r"mock\s+(?:exam|test)|exam\s+questions?|revision\s+questions?|test\s+me|flashcards?|short[- ]answer(?:\s+questions?)?|"
    r"theory\s+questions?|open[- ]ended\s+questions?)\b",
    re.IGNORECASE,
)
_SHORT = re.compile(r"\b(?:short[- ]answer|theory\s+questions?|open[- ]ended)\b", re.IGNORECASE)
_GUIDE = re.compile(
    r"\b(?:study\s+guide|revision\s+guide|revision\s+notes?|cheat\s?sheet|study\s+notes?|exam\s+notes?|exam\s+prep)\b",
    re.IGNORECASE,
)
# "30 questions", "30 mcqs", "30 multiple choice", "30-question quiz"
_COUNT = re.compile(
    r"\b(\d{1,3})\s*(?:x\s*)?(?:multiple[- ]choice(?:\s+questions?)?|mcqs?|(?:(?:practice|revision)\s+)?questions?|problems?|items?)\b",
    re.IGNORECASE,
)
_COUNT_BEFORE = re.compile(r"\b(\d{1,3})[- ](?:question|mcq|item|problem)\b", re.IGNORECASE)
_ASK = re.compile(
    r"\b(?:make|create|write|give|generate|prepare|build|need|want|send|produce|do|try|practice|"
    r"help\s+me|test\s+me)\b",
    re.IGNORECASE,
)
# Someone asking for the whole story, not a one-line answer.
_DETAIL = re.compile(
    r"\b(?:in\s+detail|detailed|fully|full\s+explanation|step[- ]by[- ]step|everything\s+about|all\s+about|"
    r"with\s+examples?|worked\s+examples?|teach\s+me|walk\s+me\s+through|explain\s+.+\s+(?:properly|completely)|"
    r"chapter[- ]by[- ]chapter|one\s+chapter\s+at\s+a\s+time|go\s+through\s+.+\s+chapter|"
    r"whole\s+chapter|entire\s+chapter|deep\s+dive)\b",
    re.IGNORECASE,
)
# Words that tell the bot what shape to use, not what the material is about. They are
# left out of a course search so "make 30 mcqs for ai exam 2" searches for AI, not "make".
_STUDY_WORDS = re.compile(
    r"\b(?:please|pls|plz|can|could|would|u|you|me|i|we|us|my|our|a|an|the|some|any|of|for|on|about|from|"
    r"with|and|or|to|in|at|by|make|create|write|give|generate|prepare|build|need|want|send|produce|do|try|"
    r"help|practice|test|quiz|quizzes|mcq|mcqs|multiple|choice|question|questions|problem|problems|item|items|"
    r"study|guide|notes?|revision|cheat|sheet|answer|answers|key|keys|short|theory|open|ended|"
    r"cover|covering|covers|topic|topics|explain|explanation|detailed|detail|step|steps|worked|example|examples)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StudyRequest:
    """What kind of study material was asked for, and how many items when they named a number."""
    kind: str  # "quiz", "short" or "guide"
    count: int = 0

    @property
    def items(self) -> int:
        """The number of questions to write: what they asked for, or a sensible quiz default."""
        return self.count or (DEFAULT_QUIZ_ITEMS if self.kind in ("quiz", "short") else 0)


def _count(text: str) -> int:
    """The number of questions asked for, capped, or 0 when none was named."""
    found = _COUNT.search(text) or _COUNT_BEFORE.search(text)
    if not found:
        return 0
    number = int(found.group(1))
    if number == 0:
        return -1
    return min(number, MAX_STUDY_ITEMS)


def study_request(text: str) -> Optional[StudyRequest]:
    """
    The study material a message asks for, None for ordinary chat. A bare "30 mcqs" counts
    as a request; "I got 30 questions wrong" does not, because it names a quiz word but no
    request and no quiz noun next to the number.
    """
    if not text:
        return None
    count = _count(text)
    if count < 0:
        return None
    guide = _GUIDE.search(text)
    quiz = _QUIZ.search(text)
    asked = bool(_ASK.search(text))
    if guide and (asked or count or re.search(r"\b(?:for|on)\b", text, re.IGNORECASE)):
        return StudyRequest("guide", count)
    plain_questions = re.search(r"\b(?:questions?|problems?|items?)\b", text, re.IGNORECASE)
    if quiz and (asked or count):
        return StudyRequest("short" if _SHORT.search(text) else "quiz", count)
    if count and asked and plain_questions:
        return StudyRequest("short" if _SHORT.search(text) else "quiz", count)
    return None


def wants_detail(text: str) -> bool:
    """Whether they asked for the full story, so a normal explanation gets more room too."""
    return bool(text and _DETAIL.search(text))


def study_search_words(text: str, request: Optional[StudyRequest] = None) -> str:
    """
    The topic of a study request without the words that ask for the quiz. "make 30 mcqs on
    deadlocks for my os exam" becomes "deadlocks os", so the search is about the material.
    """
    if request and request.count:
        text = re.sub(rf"\b{request.count}\b", " ", text, count=1)
    words = _STUDY_WORDS.sub(" ", text)
    return " ".join(words.split())


def study_prompt(request: StudyRequest) -> str:
    """
    The shape of the study reply, added after the personality prompt for this reply only.
    It says nothing about how the bot sounds: the voice rules still come from the personality.
    """
    if request.kind == "guide":
        return """
STUDY GUIDE (this reply only: the voice stays exactly the same):
- they asked for a study guide. Cover every topic the course material holds for this exam, in a sensible teaching order.
- For each topic: what it is, how it works, when to use it, and at least one worked example with the steps shown.
- Put the topics in your own words and use the course's names for things. Do not pad, repeat, or add topics the material does not hold.
- If the material is thin on a topic, say so in one line and give what it does hold instead of inventing the rest.
- Formulas go in plain text with normal symbols (for example P(A|B) = P(A and B) / P(B)), never raw LaTeX and never $...$.
- The short-chat rules (1 to 3 lines, no lists) do not apply to this reply. The personality, tone and slang do.
"""
    count = request.items
    if request.count:
        amount = f"exactly {count} questions"
    else:
        amount = f"about {count} questions"
    if request.kind == "short":
        return f"""
STUDY MATERIAL (this reply only: the voice stays exactly the same):
- they asked for short-answer theory practice. Write {amount}, numbered 1 to {count}.
- Every question asks for an explanation, a comparison, a definition, or a short worked answer. No multiple-choice options.
- Cover the topics in the course material, not only the words of their message. If they named an exam, cover the material for that exam.
- Mix recall, explanation, application and "compare these two". No duplicate or near-duplicate questions.
- If the material cannot support {count} distinct questions, say which topics are thin, then write as many distinct questions as it does support.
- Put a model answer at the end. Each answer is two to five sentences and names the key points a marker would look for.
- Formulas go in plain text with normal symbols (for example P(A|B) = P(A and B) / P(B)), never raw LaTeX and never $...$.
- The short-chat rules (1 to 3 lines, no lists) do not apply to this reply. The personality, tone and slang do.
"""
    return f"""
STUDY MATERIAL (this reply only: the voice stays exactly the same):
- they asked for a quiz or practice exam. Write {amount}, numbered 1 to {count}.
- Every question has one clear ask and exactly four options, A to D, with exactly one correct answer.
- Cover the topics in the course material, not only the words of their message. If they named an exam, cover the material for that exam.
- Mix recall, calculation, application and "which statement is true". The wrong options must be mistakes a student would actually make, not obvious jokes.
- No duplicate or near-duplicate questions. If the material cannot support {count} distinct questions, say which topics are thin, then write as many distinct questions as it does support.
- Put an answer key at the end. For each number give the letter and one or two sentences on why it is right.
- Formulas go in plain text with normal symbols (for example P(A|B) = P(A and B) / P(B)), never raw LaTeX and never $...$.
- The short-chat rules (1 to 3 lines, no lists) do not apply to this reply. The personality, tone and slang do.
"""


DETAIL_PROMPT = """
FULL ANSWER (this reply only: the voice stays exactly the same):
- they asked for the full explanation, not the short version. Answer the whole question, with the steps and an example where they help.
- The 1 to 3 line rule does not apply to this reply. The personality, tone and slang do.
"""



REWRITE_PROMPT = """
REWRITE REQUEST (this reply only: the voice stays exactly the same):
- they asked u to rewrite or rephrase something already said, not to answer a new question about the exam.
- "that", "this" or "it" means the message they replied to. keep its facts and do the new task with them.
- if they ask to connect it to a course topic, name the topic u use and explain it through the thing they pointed at. do not reply with exam dates, rooms, marks or rules unless the rewrite itself needs one of them.
- if they did not name a topic and the course material does not make the choice obvious, ask which topic in one short line.
- the short-chat rules (1 to 3 lines, no lists) do not apply to a rewrite that needs room. the personality, tone and slang do.
"""
