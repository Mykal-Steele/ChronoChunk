"""A local, encrypted record of each reply: what was asked, what context was read, what came back, and what it cost.

The rotating text log is for quick tailing. This sqlite file is for looking up one bad reply later,
when the model's output cannot explain itself and the conversation has already scrolled away. Everything
that can identify a person or repeat what they wrote is encrypted before it reaches the database.
"""
import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

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
        self._key_source = "provided"
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
            self._key_source = "REPLY_LOG_KEY"
            return configured.encode("ascii")

        key_path = Config.REPLY_LOG_KEY_PATH
        self._key_source = key_path
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

        # Never replace a lost key while encrypted rows are still there: a new key would
        # quietly make every earlier reply unreadable.
        if self._rows_need_a_key():
            raise RuntimeError(
                f"reply log has encrypted rows at {self.path} but its key file {key_path} is missing; "
                "restore the key or move the log aside")

        key = Fernet.generate_key()
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key + b"\n")
        return key

    def _rows_need_a_key(self) -> bool:
        """True when the file already has encrypted rows, so generating a new key would lose them."""
        if not os.path.exists(self.path):
            return False
        try:
            with closing(sqlite3.connect(self.path, timeout=1)) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(replies)")}
                if "payload" not in columns:
                    return False
                return db.execute("SELECT COUNT(*) FROM replies").fetchone()[0] > 0
        except sqlite3.Error as error:
            # A locked or corrupt file may still hold rows. Refuse to replace the key
            # rather than guess that there is nothing worth keeping.
            raise RuntimeError(f"could not check the reply log at {self.path}: {error}") from error

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
                "model": fields.get("model"),
                "reasoning_effort": fields.get("reasoning_effort"),
                "needs_web": fields.get("needs_web"),
                "version": fields.get("version"),
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

    def describe(self) -> Dict[str, Any]:
        """Where the log is, how long rows live, and how many are there now. Never includes the key."""
        with closing(self._connect()) as db:
            rows = db.execute("SELECT COUNT(*) FROM replies").fetchone()[0]
        return {"path": self.path, "keep_days": self.keep_days, "rows": rows, "key_source": self._key_source}

    def entries(self, user_id: Optional[str] = None, limit: Optional[int] = None) -> Iterator[Dict[str, Any]]:
        """Decrypt rows oldest first. Use user_id to read one person's rows, limit for the newest N."""
        sql = ("SELECT id, created_at, image_count, model_calls, cost, latency_ms, payload "
               "FROM replies")
        params: tuple = ()
        if user_id is not None:
            sql += " WHERE user_hash = ?"
            params = (self._user_hash(user_id),)
        if limit is None:
            sql += " ORDER BY id"
        else:
            sql += " ORDER BY id DESC LIMIT ?"
            params += (limit,)
        with closing(self._connect()) as db:
            rows = db.execute(sql, params).fetchall()
        if limit is not None:
            rows.reverse()
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
        """Drop every reply written for one person. Used by /forget.

        A failure is raised, not hidden: the command must not tell someone their data is gone
        when the encrypted rows are still there.
        """
        with closing(self._connect()) as db:
            with db:
                cursor = db.execute("DELETE FROM replies WHERE user_hash = ?", (self._user_hash(user_id),))
                return cursor.rowcount


def _loads(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _show(entry: Dict[str, Any]) -> str:
    """One reply as readable text, for the command line."""
    lines = [f"[{entry.get('created_at', '?')}] {entry.get('username') or '?'} "
             f"({entry.get('user_id') or 'no id'})"]
    if entry.get("error"):
        lines.append(f"error: {entry['error']}")
    if entry.get("query"):
        lines.append(f"asked: {entry['query']}")
    if entry.get("reply"):
        lines.append(f"replied: {entry['reply']}")
    if entry.get("tool_calls"):
        names = ", ".join(str(call.get("name", "?")) for call in entry["tool_calls"])
        lines.append(f"tools: {names}")
    if entry.get("model"):
        parts = [str(entry["model"])]
        if entry.get("reasoning_effort"):
            parts.append(f"effort {entry['reasoning_effort']}")
        if entry.get("needs_web"):
            parts.append("routing wanted web")
        if entry.get("version"):
            parts.append(f"v{entry['version']}")
        lines.append("model: " + ", ".join(parts))
    if entry.get("model_calls") or entry.get("cost"):
        lines.append(f"model calls: {entry.get('model_calls', 0)}, "
                     f"cost: ${float(entry.get('cost') or 0):.4f}, "
                     f"latency: {entry.get('latency_ms', 0)} ms")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Read the encrypted reply log: python -m src.reply_log --tail 20 [--json] [--user ID]."""
    parser = argparse.ArgumentParser(description="Read ChronoChunk's encrypted reply log.")
    parser.add_argument("--path", default=Config.REPLY_LOG_PATH, help="SQLite file (default: %(default)s)")
    parser.add_argument("--tail", type=int, default=20, help="show the newest N rows (default: 20; 0 for all)")
    parser.add_argument("--user", help="only rows for this Discord user id")
    parser.add_argument("--json", action="store_true", help="print the full decrypted row as JSON")
    args = parser.parse_args(argv)
    if not os.path.exists(args.path):
        print(f"no reply log at {args.path}")
        return 1
    log = ReplyLog(path=args.path)
    total = log.describe()["rows"]
    entries = list(log.entries(user_id=args.user, limit=args.tail if args.tail > 0 else None))
    if not entries:
        if total and not args.user:
            print(f"could not decrypt any of the {total} rows at {log.path}; is the key the one that wrote it?")
            return 1
        print(f"no replies logged at {log.path}")
        return 0
    for entry in entries:
        print(_show(entry) if not args.json else json.dumps(entry, ensure_ascii=False, indent=2))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
