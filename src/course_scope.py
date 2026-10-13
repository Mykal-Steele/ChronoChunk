"""
The course flag. Course material is only brought into a reply when the message asks
for it, with a flag at its end: "-course", or "-c" for short, means the message is
about a course and says which one in its own words, and "-csc340" names the course by
its code. A message that does not end in one is plain chat.
"""
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import List, Sequence

from src.course_store import _STOP_WORDS

# The flags that end a message, each a word of its own: "... -c", "... -csc340 -csc345"
_FLAGS = re.compile(r"(?:(?:^|\s+)-(?:course|c|[a-z]{3}\d{3}))+\s*$", re.IGNORECASE)
_CODE = re.compile(r"[A-Z]{3}\d{3}")

# A word shorter than this has to be written exactly. A longer one may carry a typo.
FUZZY_MIN_CHARS = 6
# How alike a long word and the word it stands for have to be
FUZZY_MATCH = 0.8


@dataclass
class Scope:
    """What a message says about course material."""
    # The message without its flags
    text: str
    # False for plain chat. Then nothing else here matters.
    flagged: bool = False
    # The courses it is about. Empty with flagged set means every course.
    codes: List[str] = field(default_factory=list)
    # Codes given as a flag that the course file does not hold
    unknown: List[str] = field(default_factory=list)


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _names(course: dict) -> List[List[str]]:
    """Every way a course can be called, each as its words."""
    return [words for name in (course["name"], *course["aliases"]) if (words := _words(name))]


def _same_word(said: str, wanted: str) -> bool:
    if said == wanted:
        return True
    # A word with a digit is a code, and one character off is another course
    return (len(wanted) >= FUZZY_MIN_CHARS and len(said) >= FUZZY_MIN_CHARS and wanted.isalpha() and said.isalpha()
            and SequenceMatcher(None, said, wanted).ratio() >= FUZZY_MATCH)


def _named(words: List[str], name: List[str]) -> bool:
    """Whether a name appears in the message, word for word. A long word in it may be misspelled."""
    return any(all(_same_word(said, wanted) for said, wanted in zip(words[start:start + len(name)], name, strict=True))
               for start in range(len(words) - len(name) + 1))


def courses_named(text: str, courses: List[dict]) -> List[str]:
    """The codes of the courses a message names: by code, by name, or by a nickname."""
    words = _words(text)
    # "csc 340" is the code too
    joined = " ".join(words)
    found = []
    for course in courses:
        codes = [course["code"], *(alias for alias in course["aliases"] if _CODE.fullmatch(alias.upper()))]
        by_code = any(re.search(rf"\b{code[:3].lower()} ?{code[3:]}\b", joined) for code in codes)
        if by_code or any(_named(words, name) for name in _names(course)):
            found.append(course["code"])
    return found


# Words that say someone is asking about a class, not what they are asking about. Left in, they
# make every question look a little like every lecture. Measured on 51 real questions (run E1b):
# without them a question the material cannot answer came back empty 10 times of 10, with them 2.
_ASKING = ("teacher lecture lectures lecturer slide slides class course courses say said says teach taught teaches "
           "talk talks according tell explain mention mentioned").split()


def search_words(text: str, courses: List[dict], codes: List[str]) -> str:
    """The message as a search: without the names of the courses in scope and without the words about asking."""
    names = [name for course in courses if course["code"] in codes
             for name in (course["code"], f"{course['code'][:3]} {course['code'][3:]}", course["name"], *course["aliases"])]
    def without(words: Sequence[str]) -> str:
        out = text
        # Longest first, so "ai class" goes before "ai" can leave "class" behind
        for name in sorted(words, key=len, reverse=True):
            out = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", " ", out, flags=re.IGNORECASE)
        return re.sub(r"\s+([?.,:;!])", r"\1", " ".join(out.split())).strip(" :;,.-?!")

    searched = without(names + _ASKING)
    if not any(word not in _STOP_WORDS for word in re.findall(r"[^\W_]+", searched.lower())):
        # Nothing to look for is left, so what the course is called was the topic: "what is ai"
        searched = without(_ASKING)
    return searched or text.strip()


def course_scope(message: str, courses: List[dict]) -> Scope:
    """Read the flags of a message. courses is CourseStore.courses()."""
    ending = _FLAGS.search(message)
    if not ending:
        return Scope(message)
    # A flag anywhere else is part of what they wrote, like "gcc -c"
    text = message[:ending.start()].strip()
    flags = [flag[1:].upper() for flag in ending.group().split() if _CODE.fullmatch(flag[1:].upper())]
    by_code = {course["code"]: course["code"] for course in courses}
    by_code.update({alias.upper(): course["code"] for course in courses for alias in course["aliases"]
                    if _CODE.fullmatch(alias.upper())})
    codes = list(dict.fromkeys(by_code[flag] for flag in flags if flag in by_code))
    unknown = list(dict.fromkeys(flag for flag in flags if flag not in by_code))
    # A code says exactly which course, so what the message names is not asked
    if not flags:
        codes = courses_named(text, courses)
    return Scope(text, True, codes, unknown)
