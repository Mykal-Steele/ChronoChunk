"""
Everything said in the server channels the bot is in, and the summary of each channel's
day. Layer 3 of docs/memory-plan.md: the raw messages are kept CHANNEL_LOG_KEEP_DAYS, so
the nightly job can write each day down, and the summaries are kept after that.

DMs are not logged. A summary is only shown inside the server it came from.
"""
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Sequence

from src.clock import BANGKOK
from src.database import Database, get_database
from src.memory_store import MIN_SIMILARITY, NEAR_BEST, Place, _pack

# A day with fewer messages from people than this is not worth a summary
MIN_MESSAGES = 5
EPISODE_LIMIT = 3


@dataclass
class Episode:
    id: int
    guild_name: str
    channel_name: str
    day: date
    summary: str


def _day_of(when: datetime) -> date:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(BANGKOK).date()


class ChannelLog:
    def __init__(self, database: Optional[Database] = None):
        self.db = database or get_database()

    def add(self, message, text: Optional[str] = None, is_bot: bool = False) -> bool:
        """Keep one message from a server channel. text is what to keep, the message's text by default."""
        channel = getattr(message, "channel", None)
        guild = getattr(channel, "guild", None)
        content = (message.content if text is None else text) or ""
        if channel is None or guild is None or not content.strip():
            return False
        when = getattr(message, "created_at", None) or datetime.now(timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        author = message.author
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO channel_messages (message_id, guild_id, guild_name, channel_id, channel_name, "
                "author_id, author_name, content, created_at, day, is_bot) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(message.id), str(guild.id), getattr(guild, "name", "") or "", str(channel.id),
                 getattr(channel, "name", "") or "", str(author.id), getattr(author, "display_name", "") or str(author.id),
                 content.strip(), when.astimezone(timezone.utc).isoformat(), _day_of(when).isoformat(), int(is_bot)))
        return True

    def edit(self, message_id, content: str) -> None:
        with self.db.transaction() as connection:
            connection.execute("UPDATE channel_messages SET content = ? WHERE message_id = ?", (content, str(message_id)))

    def delete(self, message_id) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM channel_messages WHERE message_id = ?", (str(message_id),))

    def messages_on(self, channel_id: str, day: date) -> list:
        return self.db.query("SELECT * FROM channel_messages WHERE channel_id = ? AND day = ? ORDER BY created_at",
                             (str(channel_id), day.isoformat()))

    def days_to_summarize(self, today: date) -> List[tuple]:
        """(channel id, day) for every finished day with enough talk from people and no summary yet, oldest first."""
        rows = self.db.query(
            "SELECT channel_id, day FROM channel_messages m WHERE day < ? AND is_bot = 0 "
            "AND NOT EXISTS (SELECT 1 FROM episodes e WHERE e.channel_id = m.channel_id AND e.day = m.day) "
            "GROUP BY channel_id, day HAVING count(*) >= ? ORDER BY day, channel_id",
            (today.isoformat(), MIN_MESSAGES))
        return [(row["channel_id"], date.fromisoformat(row["day"])) for row in rows]

    def save_episode(self, place: Place, day: date, summary: str, people: Sequence[str]) -> int:
        with self.db.transaction() as connection:
            episode_id = connection.execute(
                "INSERT OR REPLACE INTO episodes (guild_id, guild_name, channel_id, channel_name, day, summary, people, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (place.guild_id, place.guild_name, place.channel_id, place.channel_name, day.isoformat(),
                 summary.strip(), json.dumps(sorted(set(people))), datetime.now(timezone.utc).isoformat())).lastrowid
        assert episode_id is not None
        return episode_id

    def set_episode_embedding(self, episode_id: int, vector: Sequence[float], model: str) -> None:
        with self.db.transaction() as connection:
            connection.execute("UPDATE episodes SET embedding = ?, embedding_model = ? WHERE id = ?",
                               (_pack(vector), model, int(episode_id)))

    def missing_episode_embeddings(self, model: str, limit: int = 200) -> list:
        return self.db.query("SELECT id, summary FROM episodes WHERE embedding IS NULL OR embedding_model IS NOT ? "
                             "ORDER BY id LIMIT ?", (model, limit))

    def episode_count(self) -> int:
        return self.db.query("SELECT count(*) FROM episodes")[0][0]

    def has_episodes(self, guild_id: str) -> bool:
        return bool(guild_id) and bool(self.db.query("SELECT 1 FROM episodes WHERE guild_id = ? LIMIT 1", (str(guild_id),)))

    def find_episodes(self, vector: Optional[Sequence[float]], model: Optional[str], guild_id: str,
                      limit: int = EPISODE_LIMIT) -> List[Episode]:
        """
        The days in this server whose summary fits the query's embedding, by the same rule as
        memories: close enough, and nearly as close as the best one. Closest first.
        """
        if not vector or not model or not guild_id:
            return []
        packed = _pack(vector)
        rows = self.db.query(
            "SELECT *, vec_distance_cosine(embedding, ?) AS distance FROM episodes WHERE guild_id = ? "
            "AND embedding_model = ? AND length(embedding) = length(?)", (packed, str(guild_id), model, packed))
        scored = [(1.0 - row["distance"], row) for row in rows]
        best = max((s for s, _ in scored), default=0.0)
        kept = sorted(((s, row) for s, row in scored if s >= MIN_SIMILARITY and s >= best - NEAR_BEST),
                      key=lambda item: item[0], reverse=True)
        return [Episode(row["id"], row["guild_name"], row["channel_name"], date.fromisoformat(row["day"]), row["summary"])
                for _, row in kept[:limit]]

    def prune(self, today: date, keep_days: int) -> int:
        """Delete the raw messages older than keep_days. Their days' summaries stay."""
        cutoff = (today - timedelta(days=keep_days)).isoformat()
        with self.db.transaction() as connection:
            return connection.execute("DELETE FROM channel_messages WHERE day < ?", (cutoff,)).rowcount

    def forget_user(self, user_id: str) -> int:
        """Delete everything they said, and every summary of a day they spoke on, since it holds what they said."""
        user_id = str(user_id)
        with self.db.transaction() as connection:
            removed = connection.execute("DELETE FROM channel_messages WHERE author_id = ?", (user_id,)).rowcount
            connection.execute("DELETE FROM episodes WHERE people LIKE ?", (f'%"{user_id}"%',))
        return removed
