"""Keeps the bot's AI spending under a hard budget and records what every call costs."""
import logging
import sqlite3
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional

from config.config import Config
from src import usage_context
from src.database import Database, get_database
from src.user_store import clean_user_id
from src.exceptions import BotError

logger = logging.getLogger(__name__)


class BudgetExceededError(BotError):
    """Raised instead of calling the model once a spending cap is reached."""

    def __init__(self, scope: str, resets_on: date):
        # "monthly" and "daily" are the bot's own caps. "user_daily" and "user_weekly" are one person's.
        self.scope = scope
        self.resets_on = resets_on  # first day the bot, or this person, may spend again
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
    once either cap is reached. Every call adds to a row in the database, so a
    restart does not reset the totals. The totals themselves are kept in memory,
    so checking the budget before a call costs no query.
    """

    def __init__(self, database: Optional[Database] = None,
                 monthly_budget: Optional[float] = None, daily_budget: Optional[float] = None,
                 reset_day: Optional[int] = None):
        self._database = database or get_database()
        self.monthly_budget = Config.AI_MONTHLY_BUDGET_USD if monthly_budget is None else monthly_budget
        self.daily_budget = Config.AI_DAILY_BUDGET_USD if daily_budget is None else daily_budget
        self.reset_day = Config.AI_BUDGET_RESET_DAY if reset_day is None else reset_day
        self.user_daily_budget = Config.AI_USER_DAILY_BUDGET_USD
        self.user_weekly_budget = Config.AI_USER_WEEKLY_BUDGET_USD
        self._state = self._load(self._today())
        # Per person, for the current day only: what they spent today, what they spent on the
        # six days before, and whether they have been told they are near the daily limit
        self._user_today: Dict[str, float] = {}
        self._user_earlier: Dict[str, float] = {}
        self._user_warned: set = set()

    @staticmethod
    def _today() -> date:
        return datetime.now(timezone.utc).date()

    def _load(self, today: date) -> Dict[str, Any]:
        """The totals for the billing cycle and the day that today falls in, summed from the database."""
        cycle_start = cycle_start_for(today, self.reset_day).isoformat()
        cycle = self._database.query(
            "SELECT COALESCE(SUM(cost), 0) AS cost, COALESCE(SUM(requests), 0) AS requests, "
            "COALESCE(SUM(input_tokens), 0) AS tokens_in, COALESCE(SUM(output_tokens), 0) AS tokens_out "
            "FROM usage_daily WHERE day >= ?", (cycle_start,))[0]
        day = self._database.query(
            "SELECT COALESCE(SUM(cost), 0) AS cost FROM usage_daily WHERE day = ?", (today.isoformat(),))[0]
        return {
            "cycle_start": cycle_start,
            "cycle_cost": float(cycle["cost"]),
            "day": today.isoformat(),
            "day_cost": float(day["cost"]),
            "requests": int(cycle["requests"]),
            "input_tokens": int(cycle["tokens_in"]),
            "output_tokens": int(cycle["tokens_out"]),
        }

    def _roll_over(self) -> None:
        """Start a fresh day or cycle when the date has moved past the stored one."""
        today = self._today()
        if self._state["day"] != today.isoformat():
            if self._state["cycle_start"] != cycle_start_for(today, self.reset_day).isoformat():
                logger.info(f"New AI budget cycle started, last cycle cost ${self._state['cycle_cost']:.4f}")
            self._state = self._load(today)
            self._user_today.clear()
            self._user_earlier.clear()
            self._user_warned.clear()

    def _user_costs(self, user_id: str) -> tuple:
        """(spent today, spent on the six days before) for one person. Read from the database once a day."""
        if user_id not in self._user_today:
            today = self._today()
            week_start = date.fromordinal(today.toordinal() - 6).isoformat()
            rows = self._database.query(
                "SELECT day, SUM(cost) AS cost FROM usage_daily WHERE user_id = ? AND day >= ? GROUP BY day",
                (user_id, week_start))
            by_day = {row["day"]: float(row["cost"]) for row in rows}
            self._user_today[user_id] = by_day.pop(today.isoformat(), 0.0)
            self._user_earlier[user_id] = sum(by_day.values())
        return self._user_today[user_id], self._user_earlier[user_id]

    @staticmethod
    def is_unlimited(user_id: str) -> bool:
        return user_id in Config.AI_UNLIMITED_USER_IDS

    def _check_user(self, user_id: str) -> None:
        today_cost, earlier = self._user_costs(user_id)
        today = self._today()
        if today_cost >= self.user_daily_budget:
            raise BudgetExceededError("user_daily", date.fromordinal(today.toordinal() + 1))
        if today_cost + earlier >= self.user_weekly_budget:
            # Days leave the 7-day window oldest first. They can spend again on the first
            # day when enough of them have left for the rest to fit under the limit.
            rows = self._database.query(
                "SELECT day, SUM(cost) AS cost FROM usage_daily WHERE user_id = ? AND day >= ? GROUP BY day ORDER BY day",
                (user_id, date.fromordinal(today.toordinal() - 6).isoformat()))
            still_counted = today_cost + earlier
            opens = date.fromordinal(today.toordinal() + 7)
            for row in rows:
                still_counted -= float(row["cost"])
                if still_counted < self.user_weekly_budget:
                    opens = date.fromordinal(date.fromisoformat(row["day"]).toordinal() + 7)
                    break
            raise BudgetExceededError("user_weekly", opens)

    def check(self) -> None:
        """Raise BudgetExceededError if a cap is reached. Call before every model request."""
        self._roll_over()
        today = self._today()
        if self._state["cycle_cost"] >= self.monthly_budget:
            raise BudgetExceededError("monthly", next_cycle_start(today, self.reset_day))
        if self._state["day_cost"] >= self.daily_budget:
            raise BudgetExceededError("daily", date.fromordinal(today.toordinal() + 1))
        user_id, _ = usage_context.current()
        if user_id and not self.is_unlimited(user_id):
            self._check_user(user_id)

    @staticmethod
    def prices_for(model: Optional[str]) -> tuple:
        """(input, cached input, output, cache write) prices per 1M tokens for the model that answered."""
        prices = (Config.AI_PRICE_INPUT_PER_M, Config.AI_PRICE_CACHED_INPUT_PER_M, Config.AI_PRICE_OUTPUT_PER_M)
        for name, listed in Config.AI_MODEL_PRICES.items():
            if model and name in model:
                prices = listed
                break
        # A model with no separate price for writing the cache charges those tokens as plain input
        return prices if len(prices) == 4 else (*prices, prices[0])

    @staticmethod
    def cost_of(usage: Any, model: Optional[str] = None) -> float:
        """Estimated USD cost of one response from its token usage and the model that answered."""
        prompt = _as_int(getattr(usage, "prompt_tokens", 0))
        completion = _as_int(getattr(usage, "completion_tokens", 0))
        cached = _as_int(getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0))
        cached = min(cached, prompt)
        written = _as_int(getattr(getattr(usage, "prompt_tokens_details", None), "cache_write_tokens", 0))
        written = min(written, prompt - cached)
        input_price, cached_price, output_price, write_price = UsageGuard.prices_for(model)
        return (
            (prompt - cached - written) * input_price + cached * cached_price + written * write_price
            + completion * output_price
        ) / 1_000_000

    def record(self, usage: Any, model: Optional[str] = None) -> float:
        """Add one response's cost to the totals. Returns the cost."""
        prompt = _as_int(getattr(usage, "prompt_tokens", 0))
        completion = _as_int(getattr(usage, "completion_tokens", 0))
        if prompt == 0 and completion == 0:
            return 0.0

        self._roll_over()
        cost = self.cost_of(usage, model)
        self._state["cycle_cost"] += cost
        self._state["day_cost"] += cost
        self._state["requests"] += 1
        self._state["input_tokens"] += prompt
        self._state["output_tokens"] += completion
        user_id, feature = usage_context.current()
        if user_id:
            self._user_costs(user_id)
            self._user_today[user_id] += cost
        try:
            self._database.execute(
                "INSERT INTO usage_daily(day, user_id, feature, model, requests, input_tokens, output_tokens, cost) "
                "VALUES (?, ?, ?, ?, 1, ?, ?, ?) "
                "ON CONFLICT(day, user_id, feature, model) DO UPDATE SET requests = requests + 1, "
                "input_tokens = input_tokens + excluded.input_tokens, "
                "output_tokens = output_tokens + excluded.output_tokens, cost = cost + excluded.cost",
                (self._state["day"], user_id, feature, model or "", prompt, completion, cost))
        except sqlite3.Error as e:
            # The totals stay in memory, so the caps still hold until the next restart
            logger.error(f"Could not record AI usage: {e}")
        return cost

    def user_summary(self, user_id: str) -> Dict[str, Any]:
        """One person's spending against their limits, for /usage."""
        self._roll_over()
        user_id = clean_user_id(user_id)
        today_cost, earlier = self._user_costs(user_id)
        now = datetime.now(timezone.utc)
        midnight = datetime.combine(date.fromordinal(self._today().toordinal() + 1), datetime.min.time(), timezone.utc)
        return {
            "unlimited": self.is_unlimited(user_id),
            "day_cost": today_cost,
            "daily_budget": self.user_daily_budget,
            "week_cost": today_cost + earlier,
            "weekly_budget": self.user_weekly_budget,
            "seconds_to_reset": max(0, int((midnight - now).total_seconds())),
        }

    def take_daily_warning(self, user_id: str) -> bool:
        """
        True once a day per person, the first time they are past 80% of their daily
        limit, so the reply can tell them before they hit it.
        """
        user_id = clean_user_id(user_id)
        if not user_id or self.is_unlimited(user_id) or user_id in self._user_warned:
            return False
        self._roll_over()
        today_cost, _ = self._user_costs(user_id)
        if today_cost < 0.8 * self.user_daily_budget or today_cost >= self.user_daily_budget:
            return False
        self._user_warned.add(user_id)
        return True

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


class _GuardedCalls:
    """One kind of model call, chat completions or embeddings. Both are made with create and report their usage."""

    def __init__(self, calls, guard: UsageGuard):
        self._calls = calls
        self._guard = guard

    async def create(self, **kwargs):
        self._guard.check()
        response = await self._calls.create(**kwargs)
        model = getattr(response, "model", None)
        self._guard.record(getattr(response, "usage", None), model if isinstance(model, str) else kwargs.get("model"))
        return response


class GuardedClient:
    """
    Stands in for AsyncOpenAI. chat.completions.create and embeddings.create check the
    budget first and record the cost after.
    """

    def __init__(self, client, guard: UsageGuard):
        self.chat = SimpleNamespace(completions=_GuardedCalls(client.chat.completions, guard))
        self.embeddings = _GuardedCalls(client.embeddings, guard)


_shared_guard: Optional[UsageGuard] = None


def get_usage_guard() -> UsageGuard:
    """The one guard every AI call in the process goes through."""
    global _shared_guard
    if _shared_guard is None:
        _shared_guard = UsageGuard()
    return _shared_guard


def guard_client(client) -> GuardedClient:
    """Wrap an AsyncOpenAI client so all its chat completions and embeddings obey the shared budget."""
    return GuardedClient(client, get_usage_guard())
