"""Counts what each person does, so nobody can flood the bot. The counts survive a restart."""
import sqlite3
import time
from collections import defaultdict
from typing import DefaultDict, Dict, Optional

from config.config import Config
from src.database import Database, get_database
from src.exceptions import RateLimitError
from src.logger import logger


class RateLimiter:
    """
    Keeps the time of each counted action per user. The times live in memory, where
    every check reads them, and are also written to the database so a deploy does
    not hand everyone a fresh allowance.
    """

    def __init__(self, database: Optional[Database] = None):
        self._database = database or get_database()
        self._limits = Config.RATE_LIMITS
        # user_id -> action -> times, oldest first
        self._history: DefaultDict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
        self._last_cleanup = time.time()
        self._cleanup_interval = Config.RATE_LIMIT_CLEANUP_INTERVAL
        self._load()

    @property
    def _longest_window(self) -> float:
        return max(window for _, window in self._limits.values())

    def _load(self) -> None:
        """Read back what is still inside a window and drop the rest."""
        cutoff = time.time() - self._longest_window
        try:
            self._database.execute("DELETE FROM rate_events WHERE at < ?", (cutoff,))
            for row in self._database.query("SELECT user_id, action, at FROM rate_events ORDER BY at"):
                self._history[row["user_id"]][row["action"]].append(row["at"])
        except sqlite3.Error as e:
            logger.error(f"Could not load the rate limit history, starting empty: {e}")

    def check_rate_limit(self, user_id: str, action: str = "default") -> None:
        """
        Count one request for this user and action. Raises RateLimitError with the
        seconds to wait when they are over the limit for that action.
        """
        max_requests, window = self._limits.get(action, self._limits["default"])
        now = time.time()
        self._cleanup(now)

        # keep only the timestamps still inside the window
        recent = [t for t in self._history[user_id][action] if now - t < window]
        if len(recent) >= max_requests:
            self._history[user_id][action] = recent
            raise RateLimitError(retry_after=window - (now - recent[0]))

        recent.append(now)
        self._history[user_id][action] = recent
        try:
            self._database.execute("INSERT INTO rate_events(user_id, action, at) VALUES (?, ?, ?)", (str(user_id), action, now))
        except sqlite3.Error as e:
            # The count in memory still holds until the next restart
            logger.error(f"Could not save a rate limit event: {e}")

    def _cleanup(self, now: float) -> None:
        """Drop users with no recent activity so the history does not grow forever."""
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        for user_id in list(self._history):
            actions = self._history[user_id]
            for action in list(actions):
                actions[action] = [t for t in actions[action] if now - t < self._longest_window]
                if not actions[action]:
                    del actions[action]
            if not actions:
                del self._history[user_id]
        try:
            self._database.execute("DELETE FROM rate_events WHERE at < ?", (now - self._longest_window,))
        except sqlite3.Error as e:
            logger.error(f"Could not prune old rate limit events: {e}")
