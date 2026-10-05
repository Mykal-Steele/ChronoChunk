"""Keeps the bot's AI spending under a hard budget and records what every call costs."""
import json
import logging
import os
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional

from config.config import Config
from src.exceptions import BotError

logger = logging.getLogger(__name__)


class BudgetExceededError(BotError):
    """Raised instead of calling the model once a spending cap is reached."""

    def __init__(self, scope: str, resets_on: date):
        self.scope = scope          # "monthly" or "daily"
        self.resets_on = resets_on  # first day the bot may spend again
        super().__init__(f"AI {scope} budget reached, resets on {resets_on.isoformat()}")


def cycle_start_for(today: date, reset_day: int) -> date:
    """First day of the billing cycle that today falls in. Cycles start on reset_day each month."""
    if today.day >= reset_day:
        return today.replace(day=reset_day)
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return date(year, month, reset_day)


def next_cycle_start(today: date, reset_day: int) -> date:
    """First day of the billing cycle after the one today falls in."""
    start = cycle_start_for(today, reset_day)
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return date(year, month, reset_day)


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class UsageGuard:
    """
    Tracks estimated AI cost per billing cycle and per day, and refuses new calls
    once either cap is reached. The totals live in a small JSON file so a restart
    does not reset them.
    """

    def __init__(self, ledger_path: Optional[str] = None,
                 monthly_budget: Optional[float] = None, daily_budget: Optional[float] = None,
                 reset_day: Optional[int] = None):
        self.ledger_path = ledger_path or os.path.join(Config.STATE_DIR, "ai_usage.json")
        self.monthly_budget = Config.AI_MONTHLY_BUDGET_USD if monthly_budget is None else monthly_budget
        self.daily_budget = Config.AI_DAILY_BUDGET_USD if daily_budget is None else daily_budget
        self.reset_day = Config.AI_BUDGET_RESET_DAY if reset_day is None else reset_day
        self._state = self._load()

    @staticmethod
    def _today() -> date:
        return datetime.now(timezone.utc).date()

    def _empty_state(self, today: date) -> Dict[str, Any]:
        return {
            "cycle_start": cycle_start_for(today, self.reset_day).isoformat(),
            "cycle_cost": 0.0,
            "day": today.isoformat(),
            "day_cost": 0.0,
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }

    def _load(self) -> Dict[str, Any]:
        state = self._empty_state(self._today())
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                state.update(json.load(f))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            logger.error(f"Could not read the AI usage ledger at {self.ledger_path}: {e}")
        return state

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)
            tmp_path = f"{self.ledger_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2)
            os.replace(tmp_path, self.ledger_path)
        except OSError as e:
            # The totals stay in memory, so the caps still hold until the next restart
            logger.error(f"Could not write the AI usage ledger at {self.ledger_path}: {e}")

    def _roll_over(self) -> None:
        """Start a fresh day or cycle when the date has moved past the stored one."""
        today = self._today()
        cycle_start = cycle_start_for(today, self.reset_day).isoformat()
        if self._state["cycle_start"] != cycle_start:
            logger.info(f"New AI budget cycle started on {cycle_start}, last cycle cost ${self._state['cycle_cost']:.4f}")
            self._state = self._empty_state(today)
        elif self._state["day"] != today.isoformat():
            self._state["day"] = today.isoformat()
            self._state["day_cost"] = 0.0

    def check(self) -> None:
        """Raise BudgetExceededError if a cap is reached. Call before every model request."""
        self._roll_over()
        today = self._today()
        if self._state["cycle_cost"] >= self.monthly_budget:
            raise BudgetExceededError("monthly", next_cycle_start(today, self.reset_day))
        if self._state["day_cost"] >= self.daily_budget:
            raise BudgetExceededError("daily", date.fromordinal(today.toordinal() + 1))

    @staticmethod
    def cost_of(usage: Any) -> float:
        """Estimated USD cost of one response from its token usage."""
        prompt = _as_int(getattr(usage, "prompt_tokens", 0))
        completion = _as_int(getattr(usage, "completion_tokens", 0))
        cached = _as_int(getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0))
        cached = min(cached, prompt)
        return (
            (prompt - cached) * Config.AI_PRICE_INPUT_PER_M
            + cached * Config.AI_PRICE_CACHED_INPUT_PER_M
            + completion * Config.AI_PRICE_OUTPUT_PER_M
        ) / 1_000_000

    def record(self, usage: Any) -> float:
        """Add one response's cost to the totals. Returns the cost."""
        prompt = _as_int(getattr(usage, "prompt_tokens", 0))
        completion = _as_int(getattr(usage, "completion_tokens", 0))
        if prompt == 0 and completion == 0:
            return 0.0

        self._roll_over()
        cost = self.cost_of(usage)
        self._state["cycle_cost"] += cost
        self._state["day_cost"] += cost
        self._state["requests"] += 1
        self._state["input_tokens"] += prompt
        self._state["output_tokens"] += completion
        self._save()
        return cost

    def summary(self) -> Dict[str, Any]:
        """Current totals and limits, for the /usage command and the status page."""
        self._roll_over()
        today = self._today()
        return {
            "cycle_cost": round(self._state["cycle_cost"], 4),
            "monthly_budget": self.monthly_budget,
            "day_cost": round(self._state["day_cost"], 4),
            "daily_budget": self.daily_budget,
            "requests": self._state["requests"],
            "resets_on": next_cycle_start(today, self.reset_day).isoformat(),
        }


class _GuardedCompletions:
    def __init__(self, completions, guard: UsageGuard):
        self._completions = completions
        self._guard = guard

    async def create(self, **kwargs):
        self._guard.check()
        response = await self._completions.create(**kwargs)
        self._guard.record(getattr(response, "usage", None))
        return response


class GuardedClient:
    """Stands in for AsyncOpenAI. chat.completions.create checks the budget first and records the cost after."""

    def __init__(self, client, guard: UsageGuard):
        self.chat = SimpleNamespace(completions=_GuardedCompletions(client.chat.completions, guard))


_shared_guard: Optional[UsageGuard] = None


def get_usage_guard() -> UsageGuard:
    """The one guard every AI call in the process goes through."""
    global _shared_guard
    if _shared_guard is None:
        _shared_guard = UsageGuard()
    return _shared_guard


def guard_client(client) -> GuardedClient:
    """Wrap an AsyncOpenAI client so all its chat completions obey the shared budget."""
    return GuardedClient(client, get_usage_guard())
