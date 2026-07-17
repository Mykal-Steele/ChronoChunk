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
        """Rate limiting is currently disabled — all messages pass through."""
        return