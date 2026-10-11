"""
The bot's one database: a SQLite file next to the other state that has to survive a
restart. SQLite runs inside the bot, so there is no server to run or pay for.
"""
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator, List, Optional

from config.config import Config

logger = logging.getLogger(__name__)

DATABASE_FILE = "chronochunk.db"

# Each entry takes the schema one version further. Add new entries at the end and never
# edit one that has shipped: a database remembers how many of them it has run.
MIGRATIONS = [
    """
    CREATE TABLE users (
        user_id          TEXT PRIMARY KEY,
        username         TEXT,
        created_at       TEXT NOT NULL,
        last_interaction TEXT NOT NULL
    );
    CREATE TABLE facts (
        id             INTEGER PRIMARY KEY,
        user_id        TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        content        TEXT NOT NULL,
        extracted_from TEXT,
        created_at     TEXT NOT NULL
    );
    CREATE INDEX facts_by_user ON facts(user_id);
    CREATE TABLE topics (
        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        topic   TEXT NOT NULL,
        PRIMARY KEY (user_id, topic)
    );
    CREATE TABLE conversations (
        id           INTEGER PRIMARY KEY,
        user_id      TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        user_message TEXT NOT NULL,
        bot_response TEXT NOT NULL,
        created_at   TEXT NOT NULL
    );
    CREATE INDEX conversations_by_user ON conversations(user_id, id);
    CREATE TABLE channel_settings (
        channel_id        TEXT PRIMARY KEY,
        read_recent_posts INTEGER NOT NULL DEFAULT 1
    );
    -- One row per day, user, feature and model, added to on every model call.
    -- user_id and feature are '' for calls made before those were recorded.
    CREATE TABLE usage_daily (
        day           TEXT NOT NULL,
        user_id       TEXT NOT NULL DEFAULT '',
        feature       TEXT NOT NULL DEFAULT '',
        model         TEXT NOT NULL DEFAULT '',
        requests      INTEGER NOT NULL DEFAULT 0,
        input_tokens  INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cost          REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (day, user_id, feature, model)
    );
    CREATE INDEX usage_by_user ON usage_daily(user_id, day);
    CREATE TABLE rate_events (
        user_id TEXT NOT NULL,
        action  TEXT NOT NULL,
        at      REAL NOT NULL
    );
    CREATE INDEX rate_events_lookup ON rate_events(user_id, action, at);
    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]


class Database:
    """
    One SQLite connection shared by the whole bot. Calls are short and run under a
    lock, so they are safe from the event loop and from worker threads alike.
    """

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.RLock()
        # isolation_level=None leaves transactions to us, see transaction()
        self._connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        # Readers do not block the writer, and a crash cannot leave a half-written file
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        for number in range(version, len(MIGRATIONS)):
            with self.transaction() as connection:
                for statement in _statements(MIGRATIONS[number]):
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {number + 1}")
            logger.info(f"Database at {self.path} moved to schema version {number + 1}")

    def query(self, sql: str, parameters: tuple = ()) -> List[sqlite3.Row]:
        """Run one statement and return its rows."""
        with self._lock:
            return self._connection.execute(sql, parameters).fetchall()

    def execute(self, sql: str, parameters: tuple = ()) -> int:
        """Run one statement that changes data. Returns the number of rows it changed."""
        with self._lock:
            return self._connection.execute(sql, parameters).rowcount

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Several statements that happen together or not at all."""
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            self._connection.execute("COMMIT")

    def get_meta(self, key: str) -> Optional[str]:
        rows = self.query("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def set_meta(self, key: str, value: str) -> None:
        self.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, value))

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _statements(script: str) -> List[str]:
    """Split a migration into its statements. Comments are dropped first, so a ';' in one cannot split it."""
    lines = [line.split("--")[0] for line in script.splitlines()]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


_shared: Optional[Database] = None


def get_database() -> Database:
    """The database the whole bot shares. Opened, and filled from the old JSON files, on first use."""
    global _shared
    if _shared is None:
        _shared = Database(os.path.join(Config.STATE_DIR, DATABASE_FILE))
        from src.legacy_import import import_legacy_data
        import_legacy_data(_shared)
    return _shared


def set_database(database: Optional[Database]) -> None:
    """Swap the shared database, for tests. None makes the next get_database() open the real one."""
    global _shared
    _shared = database
