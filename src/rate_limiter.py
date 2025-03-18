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
        """Check if user has hit rate limit and update history"""
        now = time.time()
        
        # Clean up old data periodically (not on every request)
        if now - self._last_cleanup > self._cleanup_interval:
            self._cleanup_old_history()
            self._last_cleanup = now
            
        # Standardize user ID
        user_id = str(user_id)
        
        # Default to "default" if action is None or empty
        if not action:
            action = "default"
        
        # Get rate limit parameters, defaulting to "default" if action not found
        max_requests, window = self._limits.get(action, self._limits.get("default", (30, 1800)))
        cutoff = now - window
        
        # Get history for this user and action
        history = self._history[user_id][action]
        
        # using a sliding window approach here - way better than fixed time buckets
        # we only care about actions within the window time period
        # so if window is 30 mins, we filter to keep only timestamps from last 30 mins
        if history:
            # Remove outdated timestamps in place
            history[:] = [t for t in history if t > cutoff]
            
            # Check if they're over the limit
            if len(history) >= max_requests:
                # wait_time is how long until their oldest action "expires" from the window
                wait_time = history[0] + window - now
                raise RateLimitError(wait_time)
        
        # Record this action
        history.append(now)
        
    def _cleanup_old_history(self) -> None:
        """Clean up old rate limit history entries in one pass"""
        now = time.time()
        
        # Find the oldest window we care about
        max_window = max(window for _, window in self._limits.values())
        cutoff = now - max_window
        
        # Clean up old timestamps in a single pass
        for user_id, user_history in list(self._history.items()):
            # Process each action type
            empty_actions = []
            for action, timestamps in list(user_history.items()):
                # Filter timestamps in place
                user_history[action] = [t for t in timestamps if t > cutoff]
                # Track empty actions for removal
                if not user_history[action]:
                    empty_actions.append(action)
            
            # Remove empty action types to save memory
            for action in empty_actions:
                del user_history[action]
                
            # Remove user entry if all actions are empty
            if not user_history:
                del self._history[user_id] 