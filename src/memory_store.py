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
from typing import Callable, List, Optional, Sequence

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
# Memories at least this close are judged at night, to merge repeats and settle corrections
TIDY_SIMILARITY = 0.8
TIDY_PAIRS_PER_NIGHT = 20
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
    # Whether the person the reply is for can see a channel of this server, by its id. None
    # when that is not known, and then only this channel counts as one they can see.
    can_view: Optional[Callable[[str], bool]] = field(default=None, compare=False, repr=False)


def _viewer(guild, member) -> Callable[[str], bool]:
    def can_view(channel_id: str) -> bool:
        try:
            channel = guild.get_channel(int(channel_id))
            return bool(channel is not None and channel.permissions_for(member).view_channel)
        except Exception:
            return False
    return can_view


def place_of(channel, member=None) -> Place:
    """The server and channel of a Discord channel, and what member can see there. A DM has no server."""
    guild = getattr(channel, "guild", None)
    return Place(
        guild_id=str(guild.id) if guild else "", guild_name=getattr(guild, "name", "") if guild else "",
        channel_id=str(getattr(channel, "id", "") or ""), channel_name=getattr(channel, "name", "") or "",
        can_view=_viewer(guild, member) if guild is not None and member is not None else None,
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


def _recency(created_at: str, now: datetime, last_used_at: Optional[str] = None) -> float:
    """1 for a memory made or used just now, halving every RECENCY_HALF_LIFE_DAYS since the later of the two."""
    latest = max(created_at, last_used_at or "")
    days = max((now - datetime.fromisoformat(latest)).total_seconds() / 86400, 0.0)
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
            if kind == "self" and row["source_id"] != speaker.user_id:
                # Their own word about themselves stays theirs, see remember
                continue
            return row
        return None

    @staticmethod
    def _absorb_claims(connection, memory_id: int, content: str, about: str) -> None:
        """
        Someone just said about themselves what others had already claimed about them. Their
        report takes over, so only they can change it, and the others count as confirming it.
        """
        claims = [r for r in connection.execute(
            "SELECT * FROM memories WHERE replaced_by IS NULL AND kind = 'claim' AND about = ? COLLATE NOCASE AND id != ?",
            (about, memory_id)) if _same(r["content"]) == _same(content)]
        if not claims:
            return
        confirmed: list = []
        for claim in claims:
            for person in [[claim["source_id"], claim["source_name"]], *json.loads(claim["confirmed_by"])]:
                if person[0] not in [c[0] for c in confirmed]:
                    confirmed.append(person)
            connection.execute("UPDATE memories SET replaced_by = ? WHERE id = ?", (memory_id, claim["id"]))
        connection.execute("UPDATE memories SET confirmed_by = ? WHERE id = ?", (json.dumps(confirmed), memory_id))

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
            if kind == "self" and memory_id is not None:
                self._absorb_claims(connection, memory_id, content, about)
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
            score = (similarity + RECENCY_WEIGHT * _recency(row["created_at"], now, row["last_used_at"])
                     + CONFIRMED_WEIGHT * min(confirmed, MOST_COUNTED) + WORD_WEIGHT * min(shared, MOST_COUNTED))
            scored.append((score, shared, row["created_at"], row))
        scored.sort(key=lambda item: item[:3], reverse=True)
        return [_memory(row) for *_, row in scored[:limit]]

    def mark_used(self, memory_ids: Sequence[int]) -> None:
        """Note that recall showed these memories. A memory that keeps being used does not fade."""
        if not memory_ids:
            return
        marks = ",".join("?" * len(memory_ids))
        with self.db.transaction() as connection:
            connection.execute(f"UPDATE memories SET last_used_at = ? WHERE id IN ({marks})",
                               (datetime.now(timezone.utc).isoformat(), *[int(i) for i in memory_ids]))

    def pairs_to_tidy(self, model: str, limit: int = TIDY_PAIRS_PER_NIGHT) -> List[tuple]:
        """
        (older memory, newer memory) pairs close enough to be the same thing or a correction,
        not judged before, closest first. Two people's reports about themselves are never paired.
        """
        rows = self.db.query(
            "SELECT a_id, b_id FROM (SELECT a.id AS a_id, b.id AS b_id, "
            "1 - vec_distance_cosine(a.embedding, b.embedding) AS similarity "
            "FROM memories a JOIN memories b ON a.id < b.id "
            "WHERE a.replaced_by IS NULL AND b.replaced_by IS NULL AND a.embedding_model = ? AND b.embedding_model = ? "
            "AND length(a.embedding) = length(b.embedding) "
            "AND NOT (a.kind = 'self' AND b.kind = 'self' AND a.source_id != b.source_id) "
            "AND NOT EXISTS (SELECT 1 FROM memory_pairs_checked c WHERE c.first = a.id AND c.second = b.id)) "
            "WHERE similarity >= ? ORDER BY similarity DESC LIMIT ?",
            (model, model, TIDY_SIMILARITY, limit))
        pairs = []
        for row in rows:
            first, second = self.get(row["a_id"]), self.get(row["b_id"])
            if first is not None and second is not None:
                pairs.append((first, second) if first.created_at <= second.created_at else (second, first))
        return pairs

    def get(self, memory_id: int) -> Optional[Memory]:
        rows = self.db.query("SELECT * FROM memories WHERE id = ?", (int(memory_id),))
        return _memory(rows[0]) if rows else None

    def pair_checked(self, first: int, second: int) -> None:
        low, high = sorted((int(first), int(second)))
        self.db.execute("INSERT OR IGNORE INTO memory_pairs_checked (first, second) VALUES (?, ?)", (low, high))

    @staticmethod
    def _unchanged(connection, *memories: "Memory") -> dict:
        """The live rows of these memories by id, or empty when any was deleted, replaced or its number reused."""
        rows = {}
        for memory in memories:
            row = connection.execute("SELECT * FROM memories WHERE id = ? AND replaced_by IS NULL", (memory.id,)).fetchone()
            if row is None or row["created_at"] != memory.created_at.isoformat() or row["content"] != memory.content:
                return {}
            rows[memory.id] = row
        return rows

    def merge(self, keep: "Memory", drop: "Memory") -> bool:
        """
        drop says what keep says: drop points to keep, and whoever said drop is added to those
        who confirmed keep. False when either changed since it was read, or when one of them is
        someone's report about themselves from a different person, which only they may change.
        """
        with self.db.transaction() as connection:
            rows = self._unchanged(connection, keep, drop)
            if not rows:
                return False
            kept, dropped = rows[keep.id], rows[drop.id]
            if "self" in (kept["kind"], dropped["kind"]) and kept["source_id"] != dropped["source_id"]:
                return False
            confirmed = json.loads(kept["confirmed_by"])
            seen = {kept["source_id"], *[c[0] for c in confirmed]}
            for person in [[dropped["source_id"], dropped["source_name"]], *json.loads(dropped["confirmed_by"])]:
                if person[0] not in seen:
                    confirmed.append(person)
                    seen.add(person[0])
            connection.execute("UPDATE memories SET confirmed_by = ? WHERE id = ?", (json.dumps(confirmed), kept["id"]))
            connection.execute("UPDATE memories SET replaced_by = ? WHERE id = ?", (kept["id"], dropped["id"]))
            self._bump(connection)
        return True

    def settle(self, older: "Memory", newer: "Memory") -> bool:
        """
        newer corrects older. Done only when whoever said newer could have replaced older by
        hand, under the same trust rules as remember, and neither changed since it was read.
        """
        with self.db.transaction() as connection:
            rows = self._unchanged(connection, older, newer)
            if not rows:
                return False
            old, new = rows[older.id], rows[newer.id]
            if self._may_replace(old, Speaker(new["source_id"], new["source_name"])):
                return False
            connection.execute("UPDATE memories SET replaced_by = ? WHERE id = ?", (new["id"], old["id"]))
            self._bump(connection)
        return True

    def delete(self, memory_id: int) -> None:
        """Delete one memory for good, from /memory."""
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM memories WHERE id = ?", (int(memory_id),))
            self._bump(connection)

    def has_memories(self) -> bool:
        """Whether there is any live memory, so a reply with nothing to recall skips the embedding."""
        return bool(self.db.query("SELECT 1 FROM memories WHERE replaced_by IS NULL LIMIT 1"))

    def set_embedding(self, memory_id: int, vector: Sequence[float], model: str,
                      created_at: Optional[str] = None) -> None:
        """
        Keep the embedding of one memory and the model that made it. Not a change to what is
        remembered. created_at is the memory's own, read before the embedding call: /forget can
        delete the memory during the call and SQLite can give the next one the same id, so a
        vector is only kept on the memory it was made from.
        """
        sql = "UPDATE memories SET embedding = ?, embedding_model = ? WHERE id = ?"
        parameters: tuple = (_pack(vector), model, int(memory_id))
        if created_at is not None:
            sql += " AND created_at = ?"
            parameters += (created_at,)
        with self.db.transaction() as connection:
            connection.execute(sql, parameters)

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
            # What other people said and was folded into this person's memories stands on its own again
            connection.execute(
                "UPDATE memories SET replaced_by = NULL WHERE source_id != ? AND replaced_by IN "
                "(SELECT id FROM memories WHERE source_id = ?)", (user_id, user_id))
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
