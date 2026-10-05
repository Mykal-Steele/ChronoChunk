from collections import defaultdict
import time
from typing import Dict, DefaultDict, List
from datetime import datetime, timedelta
from config.config import Config
from src.logger import logger
from src.exceptions import RateLimitError

class RateLimiter:
    """keeps track of how many messages users send"""
    
    def __init__(self):
        """set up the rate limiter"""
        # store when people do stuff so we can check if they're spamming
        # this creates a nested defaultdict - basically a tree structure of user_id -> action -> timestamps
        # the lambda: defaultdict(list) creates a new defaultdict for each new user
        self._history: DefaultDict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
        
        # Use rate limits from configuration
        self._limits = Config.RATE_LIMITS
        
        # clean up old data every hour so we don't use too much memory
        self._last_cleanup = time.time()
        self._cleanup_interval = Config.RATE_LIMIT_CLEANUP_INTERVAL  # Use configured cleanup interval
        
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

    def _cleanup(self, now: float) -> None:
        """Drop users with no recent activity so the history does not grow forever."""
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        longest_window = max(window for _, window in self._limits.values())
        for user_id in list(self._history):
            actions = self._history[user_id]
            for action in list(actions):
                actions[action] = [t for t in actions[action] if now - t < longest_window]
                if not actions[action]:
                    del actions[action]
            if not actions:
                del self._history[user_id]