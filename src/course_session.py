"""
Course sessions. A message with a course flag opens one for the person who sent it,
in that channel. For an hour after, they just chat: what the bot looked up stays with
them, and the model decides whether it needs to look anything else up. Held in
memory, gone on a restart.
"""
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from config.config import Config

# The most characters of looked-up material a session carries. A lookup is about 10,000 at most.
MAX_MATERIAL_CHARS = 16000


@dataclass
class Session:
    # The courses the last flag was about. Empty means every course.
    codes: List[str]
    # What was looked up, oldest first, each a block as the model was shown it
    material: List[str] = field(default_factory=list)
    # What each passage number in the material stands for: number -> (name, link)
    sources: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    opened: float = 0.0


class CourseSessions:
    """Per channel and person: whether they are in a course session, and what it has gathered."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._sessions: Dict[str, Session] = {}

    def open(self, key: str, codes: List[str]) -> Session:
        """A flag was used: start the hour, or start it again. What was looked up before is kept."""
        session = self.get(key)
        if session is None:
            session = self._sessions[key] = Session(list(codes))
        session.codes = list(codes)
        session.opened = self._clock()
        return session

    def get(self, key: Optional[str]) -> Optional[Session]:
        """The session of this person here, None when there is none or its hour is over."""
        session = self._sessions.get(key or "")
        if session is None:
            return None
        if self._clock() - session.opened > Config.COURSE_SESSION_SECONDS:
            del self._sessions[key or ""]
            return None
        return session

    def add(self, key: Optional[str], block: str) -> None:
        """Keep one lookup. The oldest go when there is too much, never the one just added."""
        session = self.get(key)
        if session is None or block in session.material:
            return
        session.material.append(block)
        while len(session.material) > 1 and sum(len(piece) for piece in session.material) > MAX_MATERIAL_CHARS:
            session.material.pop(0)
