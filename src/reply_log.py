"""A local record of each reply: what was asked, what context was read, what came back, and what it cost.

The rotating text log is for quick tailing. This sqlite file is for looking up one bad reply later,
when the model's output cannot explain itself and the conversation has already scrolled away.
"""
import json
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from config.config import Config

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user_id TEXT,
    username TEXT,
    channel_id TEXT,
    query TEXT,
    conversation_history TEXT,
    attached_context TEXT,
    image_count INTEGER NOT NULL DEFAULT 0,
    routing TEXT,
    tool_calls TEXT,
    reply TEXT,
    files TEXT,
    sources TEXT,
    model_calls INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS replies_created_at ON replies(created_at);
CREATE INDEX IF NOT EXISTS replies_user_id ON replies(user_id);
"""


class ReplyLog:
    """Writes one row per reply and drops rows older than the retention window."""

    def __init__(self, path: Optional[str] = None, keep_days: Optional[int] = None):
        self.path = path or Config.REPLY_LOG_PATH
        self.keep_days = Config.REPLY_LOG_KEEP_DAYS if keep_days is None else keep_days
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with closing(self._connect()) as db:
            db.executescript(_SCHEMA)
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def record(self, **fields: Any) -> None:
        """Store one reply. Logging must never be the reason a reply fails."""
        try:
            row = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "user_id": fields.get("user_id"),
                "username": fields.get("username"),
                "channel_id": fields.get("channel_id"),
                "query": fields.get("query"),
                "conversation_history": fields.get("conversation_history"),
                "attached_context": fields.get("attached_context"),
                "image_count": fields.get("image_count", 0),
                "routing": _json(fields.get("routing")),
                "tool_calls": _json(fields.get("tool_calls")),
                "reply": fields.get("reply"),
                "files": _json(fields.get("files")),
                "sources": _json(fields.get("sources")),
                "model_calls": fields.get("model_calls", 0),
                "cost": fields.get("cost", 0.0),
                "latency_ms": fields.get("latency_ms"),
                "error": fields.get("error"),
            }
            with closing(self._connect()) as db:
                with db:
                    db.execute(
                        f"INSERT INTO replies ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                        tuple(row.values()),
                    )
                    if self.keep_days > 0:
                        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.keep_days)).isoformat()
                        db.execute("DELETE FROM replies WHERE created_at < ?", (cutoff,))
        except Exception as error:
            logger.warning(f"Could not write reply log: {error!r}")


def _json(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    return json.dumps(value, ensure_ascii=False, default=str)
