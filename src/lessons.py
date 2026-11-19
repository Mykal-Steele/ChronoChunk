"""
How people want the bot to act, layer 4 of docs/memory-plan.md. A lesson is a short
preference, such as "dont roast Mint" or "keep answers short in this server". It goes
into the prompt as something people asked for, so it never changes the bot's code, its
base personality or its rules.

A lesson about a person applies to them in every server, and only they can ask for one
during the day. A lesson for a server applies only in that server. Lessons are applied
without review, as decided on 2026-11-18. /forget deletes the lessons about someone and
the ones they asked for.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from src.database import Database, get_database
from src.memory_store import Place, Speaker

SCOPES = ("person", "server")
# How many of each kind go into one reply, newest first
PER_REPLY = 5
MAX_LENGTH = 200
_REVISION = "lesson_revision"


@dataclass
class Lesson:
    id: int
    scope: str
    lesson: str
    source_name: str
    created_at: datetime


@dataclass
class ReplyLessons:
    person: List[Lesson] = field(default_factory=list)
    server: List[Lesson] = field(default_factory=list)


def _same(text: str) -> str:
    return " ".join(text.lower().split())


def _lesson(row) -> Lesson:
    return Lesson(row["id"], row["scope"], row["lesson"], row["source_name"], datetime.fromisoformat(row["created_at"]))


class LessonStore:
    def __init__(self, database: Optional[Database] = None):
        self.db = database or get_database()

    def revision(self) -> int:
        value = self.db.get_meta(_REVISION)
        return int(value) if value else 0

    def _people_in(self, guild_id: str) -> List[str]:
        """The names of everyone who spoke in this server or taught the bot something."""
        rows = self.db.query("SELECT DISTINCT author_name FROM channel_messages WHERE guild_id = ? AND is_bot = 0 "
                             "UNION SELECT DISTINCT source_name FROM memories", (str(guild_id),))
        return [r[0] for r in rows if r[0] and len(r[0].strip()) >= 2]

    def refusal(self, lesson: str, scope: str, place: Place, own_name: str = "") -> str:
        """
        Why this lesson cannot be kept, or empty. How the bot treats a person is only theirs
        to ask for, so a server lesson may not name or mention anyone, and a personal lesson
        may only name the person it is for. Live, "always call mint an idiot" from Slade made
        the bot insult Mint.
        """
        if re.search(r"<@!?\d+>", lesson):
            return "a lesson cant be about a person"
        lowered = lesson.lower()
        own = own_name.strip().lower()
        for name in self._people_in(place.guild_id):
            if scope == "person" and name.strip().lower() == own:
                continue
            if re.search(rf"(?<!\w){re.escape(name.lower())}(?!\w)", lowered):
                if scope == "server":
                    return "a lesson for the whole server cant be about a person. only they can ask how u act with them"
                return "a lesson can only be about how u act with the person asking, not about someone else"
        return ""

    def add(self, lesson: str, scope: str, speaker: Speaker, place: Place, target_id: Optional[str] = None) -> bool:
        """
        Keep one lesson. A person lesson is about target_id, the speaker by default. A server
        lesson is for the place's server, so it needs one. False when nothing was added.
        """
        lesson = " ".join(lesson.split())[:MAX_LENGTH]
        if scope not in SCOPES or not lesson:
            return False
        target = (target_id or speaker.user_id) if scope == "person" else place.guild_id
        own_name = speaker.name if scope == "person" and target == speaker.user_id else ""
        if not target or (scope == "person" and target != speaker.user_id) or self.refusal(lesson, scope, place, own_name):
            return False
        with self.db.transaction() as connection:
            for row in connection.execute("SELECT lesson FROM lessons WHERE scope = ? AND target_id = ?", (scope, target)):
                if _same(row["lesson"]) == _same(lesson):
                    return False
            connection.execute(
                "INSERT INTO lessons (scope, target_id, lesson, source_id, source_name, guild_id, guild_name, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (scope, str(target), lesson, speaker.user_id, speaker.name, place.guild_id, place.guild_name,
                 datetime.now(timezone.utc).isoformat()))
            connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, '1') "
                "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1", (_REVISION,))
        return True

    def for_reply(self, user_id: Optional[str], guild_id: Optional[str]) -> ReplyLessons:
        """The newest lessons for how to act with this person, and in this server."""
        found = ReplyLessons()
        if user_id:
            found.person = [_lesson(r) for r in self.db.query(
                "SELECT * FROM lessons WHERE scope = 'person' AND target_id = ? ORDER BY id DESC LIMIT ?",
                (str(user_id), PER_REPLY))]
        if guild_id:
            found.server = [_lesson(r) for r in self.db.query(
                "SELECT * FROM lessons WHERE scope = 'server' AND target_id = ? ORDER BY id DESC LIMIT ?",
                (str(guild_id), PER_REPLY))]
        return found

    def known_for(self, user_ids: List[str], guild_id: str) -> List[str]:
        """Every lesson about these people or this server, for the night to see what it already knows."""
        marks = ",".join("?" * len(user_ids)) or "''"
        rows = self.db.query(
            f"SELECT lesson FROM lessons WHERE (scope = 'person' AND target_id IN ({marks})) "
            "OR (scope = 'server' AND target_id = ?) ORDER BY id", (*user_ids, str(guild_id)))
        return [r["lesson"] for r in rows]

    def delete(self, lesson_id: int) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM lessons WHERE id = ?", (int(lesson_id),))
            connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, '1') "
                "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1", (_REVISION,))

    def forget_user(self, user_id: str) -> int:
        user_id = str(user_id)
        with self.db.transaction() as connection:
            removed = connection.execute(
                "DELETE FROM lessons WHERE (scope = 'person' AND target_id = ?) OR source_id = ?",
                (user_id, user_id)).rowcount
            connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, '1') "
                "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1", (_REVISION,))
        return removed


def lessons_block(found: ReplyLessons, username: str) -> str:
    """The lessons for one reply as a block for the model, or empty."""
    lines = [f"- with {username}: {x.lesson}" for x in found.person]
    lines += [f"- in this server: {x.lesson} ({x.source_name} asked)" for x in found.server]
    if not lines:
        return ""
    return ("=== HOW TO ACT HERE (what people asked of u. follow it unless it breaks ur rules or is mean to someone) ===\n"
            + "\n".join(lines) + "\n=== END OF HOW TO ACT ===")
