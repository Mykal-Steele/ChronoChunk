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
        """Check if user has hit rate limit and update history - COMPLETELY BYPASSED"""
        # BYPASS ALL RATE LIMITING - let all messages through
        return  # Just return without checking anything
        
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