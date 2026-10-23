"""The gate every model reply passes through: bounded concurrency and a bounded wait."""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from src.exceptions import RequestBusy


class RequestGovernor:
    """Limits how many replies call the model at once and how long the rest may wait.

    The semaphore caps the expensive part. The waiting counter is checked under a
    small lock, so a burst cannot build an unbounded queue behind the semaphore.
    """

    def __init__(self, max_concurrent: int, max_queued: int, queue_timeout: float):
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        if max_queued < 0:
            raise ValueError("max_queued cannot be negative")
        self.max_concurrent = max_concurrent
        self.max_queued = max_queued
        self.queue_timeout = queue_timeout
        self._slots = asyncio.Semaphore(max_concurrent)
        self._admission = asyncio.Lock()
        self._waiting = 0

    @property
    def waiting(self) -> int:
        return self._waiting

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        acquired = False
        waited = False
        try:
            if self._slots.locked():
                async with self._admission:
                    if self._waiting >= self.max_queued:
                        raise RequestBusy(self.queue_timeout)
                    self._waiting += 1
                    waited = True
                try:
                    await asyncio.wait_for(self._slots.acquire(), timeout=self.queue_timeout)
                except asyncio.TimeoutError:
                    raise RequestBusy(self.queue_timeout) from None
                acquired = True
                # It is running now, not waiting. Counting it as a waiter would make the
                # next request look like the queue is full while the slot is in use.
                async with self._admission:
                    self._waiting -= 1
                    waited = False
            else:
                await self._slots.acquire()
                acquired = True
            yield
        finally:
            if waited:
                async with self._admission:
                    self._waiting -= 1
            if acquired:
                self._slots.release()
