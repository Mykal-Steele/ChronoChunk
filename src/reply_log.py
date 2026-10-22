"""A local, encrypted record of each reply: what was asked, what context was read, what came back, and what it cost.

The rotating text log is for quick tailing. This sqlite file is for looking up one bad reply later,
when the model's output cannot explain itself and the conversation has already scrolled away. Everything
that can identify a person or repeat what they wrote is encrypted before it reaches the database.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, Optional

from cryptography.fernet import Fernet, InvalidToken

from config.config import Config

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user_hash TEXT NOT NULL,
    image_count INTEGER NOT NULL DEFAULT 0,
    model_calls INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    payload BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS replies_created_at ON replies(created_at);
CREATE INDEX IF NOT EXISTS replies_user_hash ON replies(user_hash);
"""


class ReplyLog:
    """Writes one encrypted row per reply and drops rows older than the retention window."""

    def __init__(self, path: Optional[str] = None, keep_days: Optional[int] = None,
                 key: Optional[bytes] = None):
        self.path = path or Config.REPLY_LOG_PATH
        self.keep_days = Config.REPLY_LOG_KEEP_DAYS if keep_days is None else keep_days
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._key = key or self._load_key()
        self._fernet = Fernet(self._key)
        self._hmac_key = base64.urlsafe_b64decode(self._key)
        with closing(self._connect()) as db:
            self._migrate_plaintext(db)
            db.executescript(_SCHEMA)
            db.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _load_key(self) -> bytes:
        """The encryption key from the environment, or a generated key file with 0600 permissions."""
        configured = os.getenv("REPLY_LOG_KEY", "").strip()
        if configured:
            return configured.encode("ascii")

        key_path = Config.REPLY_LOG_KEY_PATH
        directory = os.path.dirname(key_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if os.path.exists(key_path):
            with open(key_path, "rb") as handle:
                key = handle.read().strip()
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
            return key

        key = Fernet.generate_key()
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key + b"\n")
        return key

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA secure_delete=ON")
        return db

    def _migrate_plaintext(self, db: sqlite3.Connection) -> None:
        """Move rows written before encryption into the encrypted payload column."""
        columns = {row["name"] for row in db.execute("PRAGMA table_info(replies)")}
        if not columns or "payload" in columns:
            return
        rows = [dict(row) for row in db.execute("SELECT * FROM replies")]
        db.execute("ALTER TABLE replies RENAME TO replies_plaintext")
        db.execute("DROP INDEX IF EXISTS replies_created_at")
        db.execute("DROP INDEX IF EXISTS replies_user_id")
        db.executescript(_SCHEMA)
        for row in rows:
            self._insert(db, self._payload_from_old(row), row.get("user_id"), row.get("image_count", 0),
                         row.get("model_calls", 0), row.get("cost", 0.0), row.get("latency_ms"),
                         row.get("created_at"))
        db.execute("DROP TABLE replies_plaintext")
        db.commit()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _payload_from_old(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "user_id": row.get("user_id"),
            "username": row.get("username"),
            "channel_id": row.get("channel_id"),
            "query": row.get("query"),
            "conversation_history": row.get("conversation_history"),
            "attached_context": row.get("attached_context"),
            "routing": _loads(row.get("routing")),
            "tool_calls": _loads(row.get("tool_calls")),
            "reply": row.get("reply"),
            "files": _loads(row.get("files")),
            "sources": _loads(row.get("sources")),
            "error": row.get("error"),
        }

    def _user_hash(self, user_id: Optional[str]) -> str:
        return hmac.new(self._hmac_key, (user_id or "").encode("utf-8"), hashlib.sha256).hexdigest()

    def _insert(self, db: sqlite3.Connection, payload: Dict[str, Any], user_id: Optional[str],
                image_count: int, model_calls: int, cost: float, latency_ms: Optional[int],
                created_at: Optional[str] = None) -> None:
        token = self._fernet.encrypt(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
        db.execute(
            """INSERT INTO replies
               (created_at, user_hash, image_count, model_calls, cost, latency_ms, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (created_at or datetime.now(timezone.utc).isoformat(), self._user_hash(user_id),
             image_count, model_calls, cost, latency_ms, token),
        )

    def record(self, **fields: Any) -> None:
        """Encrypt and store one reply. Logging must never be the reason a reply fails."""
        try:
            payload = {
                "user_id": fields.get("user_id"),
                "username": fields.get("username"),
                "channel_id": fields.get("channel_id"),
                "query": fields.get("query"),
                "conversation_history": fields.get("conversation_history"),
                "attached_context": fields.get("attached_context"),
                "routing": fields.get("routing"),
                "tool_calls": fields.get("tool_calls"),
                "reply": fields.get("reply"),
                "files": fields.get("files"),
                "sources": fields.get("sources"),
                "error": fields.get("error"),
            }
            with closing(self._connect()) as db:
                with db:
                    self._insert(
                        db, payload, fields.get("user_id"), fields.get("image_count", 0),
                        fields.get("model_calls", 0), fields.get("cost", 0.0), fields.get("latency_ms"),
                    )
                    if self.keep_days > 0:
                        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.keep_days)).isoformat()
                        db.execute("DELETE FROM replies WHERE created_at < ?", (cutoff,))
        except Exception as error:
            logger.warning(f"Could not write reply log: {error!r}")

    def entries(self) -> Iterator[Dict[str, Any]]:
        """Decrypt every kept row, oldest first. Used by tests and the local debug checks."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT id, created_at, image_count, model_calls, cost, latency_ms, payload "
                "FROM replies ORDER BY id"
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(self._fernet.decrypt(row["payload"]).decode("utf-8"))
            except (InvalidToken, ValueError, TypeError) as error:
                logger.warning(f"Could not decrypt reply log row {row['id']}: {error!r}")
                continue
            yield {
                "created_at": row["created_at"],
                "image_count": row["image_count"],
                "model_calls": row["model_calls"],
                "cost": row["cost"],
                "latency_ms": row["latency_ms"],
                **payload,
            }

    def delete_user(self, user_id: Optional[str]) -> int:
        """Drop every reply written for one person. Used by /forget."""
        try:
            with closing(self._connect()) as db:
                with db:
                    cursor = db.execute("DELETE FROM replies WHERE user_hash = ?", (self._user_hash(user_id),))
                    return cursor.rowcount
        except Exception as error:
            logger.warning(f"Could not delete reply log rows: {error!r}")
            return 0


def _loads(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value
