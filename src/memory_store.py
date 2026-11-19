"""
What people teach the bot, kept for good and shared by every server it is in. Layer 1 of
docs/memory-plan.md: each memory keeps who said it, when and where, and a correction
replaces the old memory instead of sitting next to it. Layer 2: each memory also keeps its
embedding, so recall finds the memories that fit a message by meaning.

Trust rules: what someone says about themselves only they can change. A claim about
anything else can be corrected by anyone, unless someone other than the corrector said
the same thing, which makes it confirmed.
"""
import json
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from src.database import Database, get_database

RECALL_LIMIT = 10
# How close in meaning a memory has to be to the message to come back, as cosine similarity.
# gemini-embedding-2 gives about 0.5 to any pair, so no single number splits fitting from not:
# on 28 real questions the weakest fit scored 0.56 and the strongest miss 0.63. A memory comes
# back when it clears MIN_SIMILARITY and is within NEAR_BEST of the best one. The misses that
# got through at 0.58 (0.60 and 0.61) put unrelated memories into the reply, so the floor is
# 0.62: no wrong memory came back, and two weak fits (0.56, 0.60) were missed (run.md, 2026-11-19).
MIN_SIMILARITY = 0.62
NEAR_BEST = 0.08
# What else moves a memory up among the ones that fit, on top of the similarity: being newer
# (halving every 30 days), being backed by more people, and sharing words with the message
RECENCY_WEIGHT = 0.05
RECENCY_HALF_LIFE_DAYS = 30
CONFIRMED_WEIGHT = 0.02
WORD_WEIGHT = 0.01
MOST_COUNTED = 3
# Meta keys: the revision of the whole memory, and when each person last ran /forget
_REVISION = "memory_revision"
_FORGOT = "memory_forgot:"

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
    """What remember did: the memory's number (None when refused), the one it replaced, and a note for the model."""
    memory_id: Optional[int]
    replaced: Optional[int] = None
    note: str = ""


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS and len(w) > 1]


def _same(text: str) -> str:
    return " ".join(text.lower().split())


def document_text(content: str, about: str) -> str:
    """What is embedded for a memory: what it is about, then what it says."""
    return f"{about}: {content}" if about else content


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _recency(created_at: str, now: datetime) -> float:
    days = max((now - datetime.fromisoformat(created_at)).total_seconds() / 86400, 0.0)
    return 0.5 ** (days / RECENCY_HALF_LIFE_DAYS)


class MemoryStore:
    def __init__(self, database: Optional[Database] = None):
        self.db = database or get_database()

    def revision(self) -> int:
        """A number that changes with every save, correction, confirmation and deletion."""
        value = self.db.get_meta(_REVISION)
        return int(value) if value else 0

    @staticmethod
    def _bump(connection) -> None:
        connection.execute(
            "INSERT INTO meta (key, value) VALUES (?, '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1", (_REVISION,))

    @staticmethod
    def _may_replace(row, speaker: Speaker) -> str:
        """Empty when speaker may replace this memory, otherwise why not."""
        if row["kind"] == "self":
            if row["source_id"] != speaker.user_id:
                return (f"#{row['id']} is what {row['source_name']} said about themselves, and only "
                        f"{row['source_name']} can change it. both are kept")
            return ""
        if json.loads(row["confirmed_by"]):
            # A claim someone else backed up has at least two people behind it, so one cannot overwrite it
            return f"#{row['id']} was confirmed by others, so one person cannot overwrite it. both are kept"
        return ""

    @staticmethod
    def _duplicate(connection, content: str, about: str, kind: str, speaker: Speaker):
        """A live memory that already says this. Two people's reports about themselves never merge."""
        for row in connection.execute(
                "SELECT * FROM memories WHERE replaced_by IS NULL AND about = ? COLLATE NOCASE", (about,)):
            if _same(row["content"]) != _same(content):
                continue
            if kind == "self" and row["kind"] == "self" and row["source_id"] != speaker.user_id:
                continue
            return row
        return None

    def remember(self, content: str, about: str, kind: str, speaker: Speaker, place: Place,
                 replaces: Optional[int] = None, started_at: Optional[datetime] = None) -> Saved:
        """
        Save one memory. started_at is when the reply began: a memory from a reply that was
        already running when they ran /forget is refused, so /forget cannot be undone by it.
        """
        content, about = content.strip(), about.strip()
        kind = "self" if kind == "self" else "claim"
        with self.db.transaction() as connection:
            if started_at is not None:
                forgot = connection.execute("SELECT value FROM meta WHERE key = ?",
                                            (_FORGOT + speaker.user_id,)).fetchone()
                if forgot and datetime.fromisoformat(forgot[0]) >= started_at:
                    return Saved(None, note="not saved: they ran /forget while this reply was being written")

            note = ""
            target = None
            if replaces is not None:
                rows = connection.execute("SELECT * FROM memories WHERE id = ? AND replaced_by IS NULL",
                                          (int(replaces),)).fetchall()
                if not rows:
                    note = f"there is no memory #{replaces} to replace, so this was saved as a new one"
                elif refusal := self._may_replace(rows[0], speaker):
                    note = refusal
                else:
                    target = rows[0]

            existing = self._duplicate(connection, content, about, kind, speaker)
            if existing is not None:
                if target is not None and target["id"] != existing["id"]:
                    # The corrected version is already known, so the old one just points to it
                    connection.execute("UPDATE memories SET replaced_by = ? WHERE id = ?", (existing["id"], target["id"]))
                    self._bump(connection)
                    return Saved(existing["id"], replaced=target["id"],
                                 note=f"replaced #{target['id']} with #{existing['id']}, which already said this")
                confirmed = json.loads(existing["confirmed_by"])
                if existing["source_id"] != speaker.user_id and speaker.user_id not in [c[0] for c in confirmed]:
                    confirmed.append([speaker.user_id, speaker.name])
                    connection.execute("UPDATE memories SET confirmed_by = ? WHERE id = ?",
                                       (json.dumps(confirmed), existing["id"]))
                    self._bump(connection)
                    return Saved(existing["id"],
                                 note=f"already knew this as #{existing['id']}, now confirmed by {speaker.name} too")
                return Saved(existing["id"], note=f"already knew this as #{existing['id']}")

            memory_id = connection.execute(
                "INSERT INTO memories (content, about, kind, source_id, source_name, guild_id, guild_name, "
                "channel_id, channel_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (content, about, kind, speaker.user_id, speaker.name, place.guild_id, place.guild_name,
                 place.channel_id, place.channel_name, datetime.now(timezone.utc).isoformat())).lastrowid
            if target is not None:
                connection.execute("UPDATE memories SET replaced_by = ? WHERE id = ?", (memory_id, target["id"]))
            self._bump(connection)
        assert memory_id is not None
        if target is not None:
            return Saved(memory_id, replaced=target["id"], note=f"replaced #{target['id']}")
        return Saved(memory_id, note=note)

    def recall(self, query: str, limit: int = RECALL_LIMIT, vector: Optional[Sequence[float]] = None,
               model: Optional[str] = None) -> List[Memory]:
        """
        The live memories that fit the query best. With vector, the query's embedding from
        model, a memory with a vector from the same model is judged by meaning and comes back
        when it is close enough and nearly as close as the best one. Any other memory is judged by the words it shares with the
        query. Among those that fit, newer, more confirmed and more word-sharing ones go first.
        """
        wanted = set(_words(query))
        packed = _pack(vector) if vector else None
        rows = self.db.query(
            "SELECT *, CASE WHEN ? IS NOT NULL AND embedding_model = ? AND length(embedding) = length(?) "
            "THEN vec_distance_cosine(embedding, ?) END AS distance FROM memories WHERE replaced_by IS NULL",
            (packed, model, packed, packed))
        now = datetime.now(timezone.utc)
        best = max((1.0 - row["distance"] for row in rows if row["distance"] is not None), default=0.0)
        scored = []
        for row in rows:
            shared = len(wanted & set(_words(f"{row['content']} {row['about']}")))
            if row["distance"] is not None:
                similarity = 1.0 - row["distance"]
                if similarity < MIN_SIMILARITY or similarity < best - NEAR_BEST:
                    continue
            elif shared:
                # A word match counts like a fair meaning match, so both kinds can be ranked together
                similarity = MIN_SIMILARITY
            else:
                continue
            confirmed = len(json.loads(row["confirmed_by"]))
            score = (similarity + RECENCY_WEIGHT * _recency(row["created_at"], now)
                     + CONFIRMED_WEIGHT * min(confirmed, MOST_COUNTED) + WORD_WEIGHT * min(shared, MOST_COUNTED))
            scored.append((score, shared, row["created_at"], row))
        scored.sort(key=lambda item: item[:3], reverse=True)
        return [_memory(row) for *_, row in scored[:limit]]

    def has_memories(self) -> bool:
        """Whether there is any live memory, so a reply with nothing to recall skips the embedding."""
        return bool(self.db.query("SELECT 1 FROM memories WHERE replaced_by IS NULL LIMIT 1"))

    def set_embedding(self, memory_id: int, vector: Sequence[float], model: str) -> None:
        """Keep the embedding of one memory and the model that made it. Not a change to what is remembered."""
        with self.db.transaction() as connection:
            connection.execute("UPDATE memories SET embedding = ?, embedding_model = ? WHERE id = ?",
                               (_pack(vector), model, int(memory_id)))

    def needs_embedding(self, memory_id: int, model: str) -> bool:
        rows = self.db.query("SELECT 1 FROM memories WHERE id = ? AND embedding IS NOT NULL AND embedding_model = ?",
                             (int(memory_id), model))
        return not rows

    def missing_embeddings(self, model: str, limit: int = 200) -> List[Memory]:
        """Live memories with no embedding from model: never embedded, or embedded by an older model."""
        rows = self.db.query(
            "SELECT * FROM memories WHERE replaced_by IS NULL AND (embedding IS NULL OR embedding_model IS NOT ?) "
            "ORDER BY id LIMIT ?", (model, limit))
        return [_memory(row) for row in rows]

    def forget_user(self, user_id: str) -> int:
        """
        Delete everything this person taught and take their name off what they confirmed. The
        time is kept, so a reply that was already running cannot save a memory for them after.
        """
        user_id = str(user_id)
        with self.db.transaction() as connection:
            removed = connection.execute("DELETE FROM memories WHERE source_id = ?", (user_id,)).rowcount
            for row in connection.execute("SELECT id, confirmed_by FROM memories WHERE confirmed_by LIKE ?",
                                          (f'%"{user_id}"%',)).fetchall():
                kept = [c for c in json.loads(row["confirmed_by"]) if c[0] != user_id]
                connection.execute("UPDATE memories SET confirmed_by = ? WHERE id = ?", (json.dumps(kept), row["id"]))
            connection.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                               (_FORGOT + user_id, datetime.now(timezone.utc).isoformat()))
            self._bump(connection)
        return removed


def _memory(row) -> Memory:
    return Memory(
        id=row["id"], content=row["content"], about=row["about"], kind=row["kind"],
        source_id=row["source_id"], source_name=row["source_name"], guild_name=row["guild_name"],
        channel_name=row["channel_name"], created_at=datetime.fromisoformat(row["created_at"]),
        confirmed_by=[name for _, name in json.loads(row["confirmed_by"])],
    )
