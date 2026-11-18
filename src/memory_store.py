"""
What people teach the bot, kept for good and shared by every server it is in. Layer 1 of
docs/memory-plan.md: each memory keeps who said it, when and where, and a correction
replaces the old memory instead of sitting next to it.

Trust rules: what someone says about themselves only they can change. A claim about
anything else can be corrected by anyone, unless someone other than the corrector said
the same thing, which makes it confirmed.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from src.database import Database, get_database

RECALL_LIMIT = 10

# Words that say nothing about what a memory is about
_STOPWORDS = frozenset("""
a an and are as at be but by can do does for from had has have he her his how i if in is it its
me my of on or our she so that the their them they this to u ur was we were what when where which
who why will with you your about any just like than then there these those been being did
""".split())


@dataclass
class Speaker:
    user_id: str
    name: str


@dataclass
class Place:
    guild_id: str = ""
    guild_name: str = ""
    channel_id: str = ""
    channel_name: str = ""


def place_of(channel) -> Place:
    """The server and channel of a Discord channel. A DM has no server."""
    guild = getattr(channel, "guild", None)
    return Place(
        guild_id=str(guild.id) if guild else "", guild_name=getattr(guild, "name", "") if guild else "",
        channel_id=str(getattr(channel, "id", "") or ""), channel_name=getattr(channel, "name", "") or "",
    )


@dataclass
class Memory:
    id: int
    content: str
    about: str
    kind: str
    source_id: str
    source_name: str
    guild_name: str
    channel_name: str
    created_at: datetime
    confirmed_by: List[str] = field(default_factory=list)


@dataclass
class Saved:
    """What remember did: the memory's number, the one it replaced if any, and a note for the model."""
    memory_id: int
    replaced: Optional[int] = None
    note: str = ""


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS and len(w) > 1]


def _same(text: str) -> str:
    return " ".join(text.lower().split())


class MemoryStore:
    def __init__(self, database: Optional[Database] = None):
        self.db = database or get_database()

    def _live(self, memory_id: int):
        rows = self.db.query("SELECT * FROM memories WHERE id = ? AND replaced_by IS NULL", (memory_id,))
        return rows[0] if rows else None

    def remember(self, content: str, about: str, kind: str, speaker: Speaker, place: Place,
                 replaces: Optional[int] = None) -> Saved:
        content, about = content.strip(), about.strip()
        kind = "self" if kind == "self" else "claim"
        # The same thing from someone else confirms it instead of storing it twice
        for row in self.db.query("SELECT * FROM memories WHERE replaced_by IS NULL AND about = ? COLLATE NOCASE",
                                 (about,)):
            if _same(row["content"]) == _same(content):
                confirmed = json.loads(row["confirmed_by"])
                if row["source_id"] != speaker.user_id and speaker.user_id not in [c[0] for c in confirmed]:
                    confirmed.append([speaker.user_id, speaker.name])
                    self.db.execute("UPDATE memories SET confirmed_by = ? WHERE id = ?",
                                    (json.dumps(confirmed), row["id"]))
                    return Saved(row["id"], note=f"already knew this as #{row['id']}, now confirmed by {speaker.name} too")
                return Saved(row["id"], note=f"already knew this as #{row['id']}")

        note = ""
        target = None
        if replaces is not None:
            target = self._live(int(replaces))
            if target is None:
                note = f"there is no memory #{replaces} to replace, so this was saved as a new one"
            elif target["kind"] == "self" and target["source_id"] != speaker.user_id:
                note = (f"#{replaces} is what {target['source_name']} said about themselves, and only "
                        f"{target['source_name']} can change it. both are kept")
                target = None
            elif any(c[0] != speaker.user_id for c in json.loads(target["confirmed_by"])):
                note = f"#{replaces} was confirmed by others, so one person cannot overwrite it. both are kept"
                target = None
        now = datetime.now(timezone.utc).isoformat()
        with self.db.transaction() as connection:
            memory_id = connection.execute(
                "INSERT INTO memories (content, about, kind, source_id, source_name, guild_id, guild_name, "
                "channel_id, channel_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (content, about, kind, speaker.user_id, speaker.name, place.guild_id, place.guild_name,
                 place.channel_id, place.channel_name, now)).lastrowid
            if target is not None:
                connection.execute("UPDATE memories SET replaced_by = ? WHERE id = ?", (memory_id, target["id"]))
        assert memory_id is not None
        if target is not None:
            return Saved(memory_id, replaced=target["id"], note=f"replaced #{target['id']}")
        return Saved(memory_id, note=note)

    def recall(self, query: str, limit: int = RECALL_LIMIT) -> List[Memory]:
        """The live memories that share the most words with the query, newest first on a tie."""
        wanted = set(_words(query))
        if not wanted:
            return []
        scored = []
        for row in self.db.query("SELECT * FROM memories WHERE replaced_by IS NULL"):
            score = len(wanted & set(_words(f"{row['content']} {row['about']}")))
            if score:
                scored.append((score, row["created_at"], row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [_memory(row) for _, _, row in scored[:limit]]

    def forget_user(self, user_id: str) -> int:
        """Delete everything this person taught. Returns how many memories went."""
        return self.db.execute("DELETE FROM memories WHERE source_id = ?", (str(user_id),))


def _memory(row) -> Memory:
    return Memory(
        id=row["id"], content=row["content"], about=row["about"], kind=row["kind"],
        source_id=row["source_id"], source_name=row["source_name"], guild_name=row["guild_name"],
        channel_name=row["channel_name"], created_at=datetime.fromisoformat(row["created_at"]),
        confirmed_by=[name for _, name in json.loads(row["confirmed_by"])],
    )
