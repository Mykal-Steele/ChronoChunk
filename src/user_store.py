"""What the bot remembers about each person: who they are, facts they stated and their last conversations."""
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from config.config import Config
from src.database import Database, get_database


def clean_user_id(user_id) -> str:
    """Discord ids are numbers. Anything else in the string is dropped."""
    return re.sub(r"[^0-9]", "", str(user_id))


def _now() -> str:
    return datetime.now().isoformat()


class UserStore:
    """
    Reads and writes one person's data. Every change is one small transaction, so two
    things happening at once (a /forget while facts are being extracted) cannot
    overwrite each other.
    """

    def __init__(self, database: Optional[Database] = None):
        self._database = database or get_database()

    def get_profile(self, user_id: str, username: Optional[str] = None) -> Dict[str, Any]:
        """
        Everything stored about a user. Someone new gets a row first, and a changed
        display name is saved. created_at identifies this version of the user: it
        changes when they are forgotten and come back.
        """
        user_id = clean_user_id(user_id)
        now = _now()
        with self._database.transaction() as db:
            row = db.execute("SELECT username, created_at, last_interaction FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row is None:
                db.execute("INSERT INTO users(user_id, username, created_at, last_interaction) VALUES (?, ?, ?, ?)",
                           (user_id, username, now, now))
                row = {"username": username, "created_at": now, "last_interaction": now}
            elif username and row["username"] != username:
                db.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
                row = {"username": username, "created_at": row["created_at"], "last_interaction": row["last_interaction"]}
            facts = db.execute("SELECT id, content, extracted_from, created_at FROM facts WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
            topics = db.execute("SELECT topic FROM topics WHERE user_id = ? ORDER BY rowid", (user_id,)).fetchall()
            history = db.execute("SELECT user_message, bot_response, created_at FROM conversations WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
        return {
            "user_id": user_id,
            "username": row["username"],
            "created_at": row["created_at"],
            "last_interaction": row["last_interaction"],
            "facts": [{"id": f["id"], "content": f["content"], "extracted_from": f["extracted_from"], "timestamp": f["created_at"]} for f in facts],
            "topics_of_interest": [t["topic"] for t in topics],
            "conversation_history": [{"timestamp": c["created_at"], "user_message": c["user_message"], "bot_response": c["bot_response"]} for c in history],
        }

    def add_conversation(self, user_id: str, user_message: str, bot_response: str, username: Optional[str] = None) -> None:
        """Save one exchange and keep only the newest MAX_CONVERSATION_HISTORY of them."""
        user_id = clean_user_id(user_id)
        self.get_profile(user_id, username)  # makes sure the user exists
        now = _now()
        with self._database.transaction() as db:
            db.execute("INSERT INTO conversations(user_id, user_message, bot_response, created_at) VALUES (?, ?, ?, ?)",
                       (user_id, user_message, bot_response, now))
            db.execute(
                "DELETE FROM conversations WHERE user_id = ? AND id NOT IN "
                "(SELECT id FROM conversations WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
                (user_id, user_id, Config.MAX_CONVERSATION_HISTORY))
            db.execute("UPDATE users SET last_interaction = ? WHERE user_id = ?", (now, user_id))

    def change_facts(self, user_id: str, add: Iterable[str] = (), remove_ids: Iterable[int] = (),
                     extracted_from: str = "", if_created_at: Optional[str] = None) -> bool:
        """
        Add facts and delete others in one step. With if_created_at, nothing happens
        unless the user is still the one that was read: a person who ran /forget in
        the meantime does not get facts from an older message written back.
        Returns whether anything changed.
        """
        user_id = clean_user_id(user_id)
        add, remove_ids = list(add), list(remove_ids)
        changed = False
        with self._database.transaction() as db:
            row = db.execute("SELECT created_at FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row is None or (if_created_at is not None and row["created_at"] != if_created_at):
                return False
            now = _now()
            for fact_id in remove_ids:
                changed |= db.execute("DELETE FROM facts WHERE id = ? AND user_id = ?", (fact_id, user_id)).rowcount > 0
            for content in add:
                db.execute("INSERT INTO facts(user_id, content, extracted_from, created_at) VALUES (?, ?, ?, ?)",
                           (user_id, content, extracted_from, now))
                changed = True
            if changed:
                db.execute("UPDATE users SET last_interaction = ? WHERE user_id = ?", (now, user_id))
        return changed

    def forget(self, user_id: str) -> None:
        """Delete everything stored about a user. Their facts, topics and conversations go with the row."""
        self._database.execute("DELETE FROM users WHERE user_id = ?", (clean_user_id(user_id),))

    def user_count(self) -> int:
        return self._database.query("SELECT COUNT(*) AS n FROM users")[0]["n"]

    def import_user(self, data: Dict[str, Any]) -> bool:
        """Copy one user from the old JSON format. A user already in the database is left alone."""
        user_id = clean_user_id(data.get("user_id", ""))
        if not user_id:
            return False
        now = _now()
        with self._database.transaction() as db:
            if db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone():
                return False
            db.execute("INSERT INTO users(user_id, username, created_at, last_interaction) VALUES (?, ?, ?, ?)",
                       (user_id, data.get("username"), data.get("created_at") or now, data.get("last_interaction") or now))
            for fact in data.get("facts") or []:
                # Very old files hold a fact as a bare string
                content = fact.get("content") if isinstance(fact, dict) else str(fact)
                if content:
                    db.execute("INSERT INTO facts(user_id, content, extracted_from, created_at) VALUES (?, ?, ?, ?)",
                               (user_id, content, fact.get("extracted_from") if isinstance(fact, dict) else None,
                                (fact.get("timestamp") if isinstance(fact, dict) else None) or now))
            for topic in data.get("topics_of_interest") or []:
                if isinstance(topic, str) and topic:
                    db.execute("INSERT OR IGNORE INTO topics(user_id, topic) VALUES (?, ?)", (user_id, topic))
            history: List[dict] = [c for c in (data.get("conversation_history") or []) if isinstance(c, dict)]
            for entry in history[-Config.MAX_CONVERSATION_HISTORY:]:
                db.execute("INSERT INTO conversations(user_id, user_message, bot_response, created_at) VALUES (?, ?, ?, ?)",
                           (user_id, str(entry.get("user_message", "")), str(entry.get("bot_response", "")), entry.get("timestamp") or now))
        return True
