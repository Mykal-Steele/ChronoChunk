import asyncio
import time
import logging
from collections import deque

logger = logging.getLogger(__name__)

class APIThrottler:
    """Controls API request rate to prevent 429 errors"""
    
    def __init__(self, requests_per_minute=50, burst_limit=5):
        self.requests_per_minute = requests_per_minute
        self.burst_limit = burst_limit
        self.request_times = deque(maxlen=requests_per_minute)
        self.active_requests = 0
        self.lock = asyncio.Lock()
    
    async def acquire(self, priority=0):
        """Acquire permission to make an API request"""
        async with self.lock:
            # Check if we've exceeded our burst limit
            while self.active_requests >= self.burst_limit:
                await asyncio.sleep(0.1)
                
            # Check rate limit (requests per minute)
            now = time.time()
            while len(self.request_times) >= self.requests_per_minute:
                oldest = self.request_times[0]
                time_passed = now - oldest
                if time_passed < 60:  # Less than a minute has passed
                    await asyncio.sleep(60 - time_passed + 0.1)
                    now = time.time()
                else:
                    break
                    
            # Add current request
            self.request_times.append(now)
            self.active_requests += 1
    
    async def release(self):
        """Release the request slot"""
        async with self.lock:
            self.active_requests = max(0, self.active_requests - 1)