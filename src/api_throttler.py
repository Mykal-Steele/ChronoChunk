import asyncio
import time
import logging
from collections import deque

logger = logging.getLogger(__name__)

class APIThrottler:
    """Controls API request rate to prevent 429 errors"""
    
    def __init__(self, requests_per_minute=90, burst_limit=15):
        """
        Initialize the API throttler with more generous limits
        
        Args:
            requests_per_minute: Maximum number of requests allowed per minute
            burst_limit: Maximum number of concurrent requests
        """
        self.requests_per_minute = requests_per_minute
        self.burst_limit = burst_limit
        self.request_times = deque(maxlen=requests_per_minute)
        self.active_requests = 0
        self.lock = asyncio.Lock()
        logger.info(f"APIThrottler initialized with {requests_per_minute} RPM and {burst_limit} burst limit")
    
    async def acquire(self, priority=0):
        """Acquire permission to make an API request"""
        async with self.lock:
            # Check if we've exceeded our burst limit
            if self.active_requests >= self.burst_limit:
                # Don't wait too long - just a small delay
                await asyncio.sleep(0.05)
                
            # Check rate limit (requests per minute) - more lenient approach
            now = time.time()
            if len(self.request_times) >= self.requests_per_minute * 0.9:  # Only throttle at 90% capacity
                oldest = self.request_times[0]
                time_passed = now - oldest
                if time_passed < 60:  # Less than a minute has passed
                    # Much more lenient wait time - very brief pause
                    wait_time = max(0.05, min(2, (60 - time_passed) / 10))
                    await asyncio.sleep(wait_time)
                    now = time.time()
                
            # Add current request
            self.request_times.append(now)
            self.active_requests += 1
            logger.debug(f"Request acquired. Active: {self.active_requests}, Queue: {len(self.request_times)}")
    
    async def release(self):
        """Release the request slot"""
        async with self.lock:
            self.active_requests = max(0, self.active_requests - 1)
            logger.debug(f"Request released. Active: {self.active_requests}")