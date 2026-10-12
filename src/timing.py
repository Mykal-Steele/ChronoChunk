"""Times the steps of one answer, so a slow reply can be traced to the step that was slow."""
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Iterator, Optional


class Stopwatch:
    """Started when the bot picks a message up. Each step adds its seconds under a name."""

    def __init__(self, sent_at: object = None):
        self.started = time.monotonic()
        self.steps: Dict[str, float] = {}
        # How long the message took to reach the bot, by Discord's clock and this machine's
        self.pickup: Optional[float] = None
        if isinstance(sent_at, datetime):
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)
            self.pickup = max(0.0, (datetime.now(timezone.utc) - sent_at).total_seconds())

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        """Time the block. A step used twice adds up."""
        began = time.monotonic()
        try:
            yield
        finally:
            self.steps[name] = self.steps.get(name, 0.0) + time.monotonic() - began

    def summary(self) -> str:
        """Such as "1.84s: history 0.24s, context 0.02s, model and tools 1.21s, send 0.31s, other 0.06s"."""
        total = time.monotonic() - self.started
        parts = [f"{name} {seconds:.2f}s" for name, seconds in self.steps.items()]
        # Whatever no step covers: the rate limit check, starting the typing indicator, and so on
        parts.append(f"other {max(0.0, total - sum(self.steps.values())):.2f}s")
        text = f"{total:.2f}s: " + ", ".join(parts)
        if self.pickup is not None:
            text += f". picked up {self.pickup:.2f}s after it was sent"
        return text
