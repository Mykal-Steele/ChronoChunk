"""Unit tests for the AI budget guard: cost estimates, caps, cycle rollover and the guarded client."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.usage_guard import (
    BudgetExceededError, GuardedClient, UsageGuard, cycle_start_for, next_cycle_start,
)


def usage(prompt=0, completion=0, cached=0):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion,
                           prompt_tokens_details=SimpleNamespace(cached_tokens=cached))


@pytest.fixture
def guard(database):
    return UsageGuard(database, monthly_budget=1.0, daily_budget=0.5, reset_day=14)


def set_today(monkeypatch, day: date):
    monkeypatch.setattr(UsageGuard, "_today", staticmethod(lambda: day))


# ── billing cycle dates ───────────────────────────────────────────────────────

def test_cycle_starts_on_the_reset_day_of_this_month():
    assert cycle_start_for(date(2026, 10, 20), 14) == date(2026, 10, 14)
    assert cycle_start_for(date(2026, 10, 14), 14) == date(2026, 10, 14)


def test_cycle_started_last_month_before_the_reset_day():
    assert cycle_start_for(date(2026, 10, 5), 14) == date(2026, 9, 14)
    assert cycle_start_for(date(2027, 1, 3), 14) == date(2026, 12, 14)


def test_next_cycle_start_rolls_over_the_year():
    assert next_cycle_start(date(2026, 10, 5), 14) == date(2026, 10, 14)
    assert next_cycle_start(date(2026, 12, 20), 14) == date(2027, 1, 14)


# ── cost estimate ─────────────────────────────────────────────────────────────

def test_cost_uses_input_cached_and_output_prices():
    # 1M fresh input at $0.25, 1M cached input at $0.025, 1M output at $2.00
    cost = UsageGuard.cost_of(usage(prompt=2_000_000, completion=1_000_000, cached=1_000_000))
    assert cost == pytest.approx(0.25 + 0.025 + 2.00)


def test_cost_of_a_typical_chat_reply_is_a_fraction_of_a_cent():
    assert UsageGuard.cost_of(usage(prompt=2764, completion=498)) == pytest.approx(0.001687, abs=1e-6)


def test_cost_ignores_usage_that_is_not_numbers():
    assert UsageGuard.cost_of(MagicMock()) == 0.0
    assert UsageGuard.cost_of(None) == 0.0


# ── caps ──────────────────────────────────────────────────────────────────────

def test_check_passes_under_budget(guard):
    guard.record(usage(completion=100_000))  # $0.20
    guard.check()


def test_daily_cap_blocks_and_names_tomorrow(guard, monkeypatch):
    set_today(monkeypatch, date(2026, 10, 5))
    guard.record(usage(completion=250_000))  # $0.50, the daily cap
    with pytest.raises(BudgetExceededError) as blocked:
        guard.check()
    assert blocked.value.scope == "daily"
    assert blocked.value.resets_on == date(2026, 10, 6)


def test_daily_cap_clears_the_next_day(guard, monkeypatch):
    set_today(monkeypatch, date(2026, 10, 5))
    guard.record(usage(completion=250_000))
    set_today(monkeypatch, date(2026, 10, 6))
    guard.check()
    assert guard.summary()["day_cost"] == 0
    assert guard.summary()["cycle_cost"] == pytest.approx(0.5)


def test_monthly_cap_blocks_until_the_reset_day(guard, monkeypatch):
    set_today(monkeypatch, date(2026, 10, 5))
    guard.record(usage(completion=250_000))
    set_today(monkeypatch, date(2026, 10, 6))
    guard.record(usage(completion=250_000))  # cycle total is now $1.00
    set_today(monkeypatch, date(2026, 10, 7))
    with pytest.raises(BudgetExceededError) as blocked:
        guard.check()
    assert blocked.value.scope == "monthly"
    assert blocked.value.resets_on == date(2026, 10, 14)


def test_monthly_cap_clears_when_the_new_cycle_starts(guard, monkeypatch):
    set_today(monkeypatch, date(2026, 10, 5))
    guard.record(usage(completion=250_000))
    set_today(monkeypatch, date(2026, 10, 6))
    guard.record(usage(completion=250_000))
    set_today(monkeypatch, date(2026, 10, 14))
    guard.check()
    assert guard.summary()["cycle_cost"] == 0


# ── the ledger in the database ─────────────────────────────────────────────────

def test_totals_survive_a_restart(database):
    first = UsageGuard(database, monthly_budget=1.0, daily_budget=0.5, reset_day=14)
    first.record(usage(prompt=1_000_000))  # $0.25
    second = UsageGuard(database, monthly_budget=1.0, daily_budget=0.5, reset_day=14)
    assert second.summary()["cycle_cost"] == pytest.approx(0.25)
    assert second.summary()["requests"] == 1


def test_blocked_state_survives_a_restart(database):
    UsageGuard(database, monthly_budget=1.0, daily_budget=0.5, reset_day=14).record(usage(completion=600_000))
    with pytest.raises(BudgetExceededError):
        UsageGuard(database, monthly_budget=1.0, daily_budget=0.5, reset_day=14).check()


def test_calls_without_token_counts_write_nothing(guard, database):
    assert guard.record(MagicMock()) == 0.0
    assert database.query("SELECT COUNT(*) AS n FROM usage_daily")[0]["n"] == 0


def test_each_model_gets_its_own_row_per_day(guard, database):
    guard.record(usage(prompt=1_000_000), "gpt-5-nano")
    guard.record(usage(prompt=1_000_000), "gpt-5-nano")
    guard.record(usage(prompt=1_000_000), "gpt-5-mini")
    rows = {r["model"]: (r["requests"], r["cost"]) for r in database.query("SELECT model, requests, cost FROM usage_daily")}
    assert rows == {"gpt-5-nano": (2, pytest.approx(0.10)), "gpt-5-mini": (1, pytest.approx(0.25))}


def test_spending_from_an_earlier_cycle_is_not_counted(database, monkeypatch):
    set_today(monkeypatch, date(2026, 10, 13))
    UsageGuard(database, monthly_budget=1.0, daily_budget=0.5, reset_day=14).record(usage(prompt=1_000_000))
    set_today(monkeypatch, date(2026, 10, 14))
    fresh = UsageGuard(database, monthly_budget=1.0, daily_budget=0.5, reset_day=14)
    assert fresh.summary()["cycle_cost"] == 0 and fresh.summary()["requests"] == 0


# ── guarded client ────────────────────────────────────────────────────────────

def _client(response=None):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    return client


async def test_guarded_client_records_what_a_call_cost(guard):
    response = SimpleNamespace(usage=usage(prompt=1_000_000))
    client = GuardedClient(_client(response), guard)
    assert await client.chat.completions.create(model="gpt-5-mini", messages=[]) is response
    assert guard.summary()["cycle_cost"] == pytest.approx(0.25)


async def test_guarded_client_does_not_call_the_model_over_budget(guard):
    guard.record(usage(completion=600_000))
    inner = _client()
    client = GuardedClient(inner, guard)
    with pytest.raises(BudgetExceededError):
        await client.chat.completions.create(model="gpt-5-mini", messages=[])
    inner.chat.completions.create.assert_not_called()


def test_a_cheaper_model_is_costed_at_its_own_prices():
    tokens = usage(prompt=1_000_000, completion=1_000_000)
    assert UsageGuard.cost_of(tokens, "gpt-5-nano-2025-08-07") == pytest.approx(0.45)
    assert UsageGuard.cost_of(tokens, "gpt-5-mini-2025-08-07") == pytest.approx(2.25)
    assert UsageGuard.cost_of(tokens) == pytest.approx(2.25)


def test_the_guard_records_the_price_of_the_model_that_answered(guard):
    assert guard.record(usage(prompt=1_000_000), "gpt-5-nano") == pytest.approx(0.05)
    assert guard.summary()["cycle_cost"] == pytest.approx(0.05)


def test_cache_writes_are_charged_at_their_own_price():
    from types import SimpleNamespace
    tokens = SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=0,
                             prompt_tokens_details=SimpleNamespace(cached_tokens=400_000, cache_write_tokens=500_000))
    # gpt-6-luna: 100k plain at $0.10, 400k cached at $0.01, 500k written at $0.125
    assert UsageGuard.cost_of(tokens, "gpt-6-luna-2026-09-22") == pytest.approx(0.01 + 0.004 + 0.0625)
    # a model with no cache-write price charges those tokens as plain input
    assert UsageGuard.cost_of(tokens, "gpt-5-mini") == pytest.approx(0.6 * 0.25 + 0.4 * 0.025)


# ── per-user limits ───────────────────────────────────────────────────────────

@pytest.fixture
def as_user():
    """Mark the calls that follow as one person's, the way an incoming message does."""
    from src import usage_context
    def switch(user_id, feature="chat"):
        usage_context._current.set((user_id, feature))
    yield switch
    usage_context._current.set(("", "chat"))


@pytest.fixture
def user_guard(database):
    guard = UsageGuard(database, monthly_budget=100.0, daily_budget=50.0, reset_day=14)
    guard.user_daily_budget, guard.user_weekly_budget = 1.0, 2.5
    return guard


def test_spending_is_recorded_against_the_person_and_the_feature(user_guard, as_user, database):
    as_user("111", "chat")
    user_guard.record(usage(prompt=1_000_000), "gpt-5-mini")
    as_user("111", "facts")
    user_guard.record(usage(prompt=1_000_000), "gpt-5-nano")
    as_user("222", "chat")
    user_guard.record(usage(prompt=1_000_000), "gpt-5-mini")
    rows = {(r["user_id"], r["feature"]): r["cost"] for r in database.query("SELECT user_id, feature, cost FROM usage_daily")}
    assert rows == {("111", "chat"): pytest.approx(0.25), ("111", "facts"): pytest.approx(0.05), ("222", "chat"): pytest.approx(0.25)}
    assert user_guard.user_summary("111")["day_cost"] == pytest.approx(0.30)
    assert user_guard.summary()["cycle_cost"] == pytest.approx(0.55)


def test_a_person_over_their_daily_limit_is_stopped_and_others_are_not(user_guard, as_user):
    as_user("111")
    user_guard.record(usage(completion=500_000))  # $1.00, their whole day
    with pytest.raises(BudgetExceededError) as stopped:
        user_guard.check()
    assert stopped.value.scope == "user_daily"
    as_user("222")
    user_guard.check()
    as_user("")  # a call that belongs to nobody
    user_guard.check()


def test_the_daily_limit_clears_the_next_day_and_the_weekly_one_then_applies(user_guard, as_user, monkeypatch):
    as_user("111")
    for day in (5, 6, 7):
        set_today(monkeypatch, date(2026, 10, day))
        user_guard.check()
        user_guard.record(usage(completion=450_000))  # $0.90 a day, $2.70 in three days
    set_today(monkeypatch, date(2026, 10, 8))
    with pytest.raises(BudgetExceededError) as stopped:
        user_guard.check()
    assert stopped.value.scope == "user_weekly"
    assert stopped.value.resets_on == date(2026, 10, 12)  # the 5th leaves the window
    set_today(monkeypatch, date(2026, 10, 12))
    user_guard.check()


def test_listed_people_have_no_limit(user_guard, as_user, monkeypatch):
    from config.config import Config
    monkeypatch.setattr(Config, "AI_UNLIMITED_USER_IDS", {"111"})
    as_user("111")
    user_guard.record(usage(completion=5_000_000))
    user_guard.check()
    assert user_guard.user_summary("111")["unlimited"] is True
    assert user_guard.take_daily_warning("111") is False


def test_a_persons_spending_survives_a_restart(database, as_user):
    as_user("111")
    first = UsageGuard(database, monthly_budget=100.0, daily_budget=50.0, reset_day=14)
    first.record(usage(completion=100_000))  # $0.20, the default daily limit
    second = UsageGuard(database, monthly_budget=100.0, daily_budget=50.0, reset_day=14)
    assert second.user_summary("111")["day_cost"] == pytest.approx(0.20)
    with pytest.raises(BudgetExceededError):
        second.check()


def test_the_heads_up_comes_once_past_80_percent(user_guard, as_user):
    as_user("111")
    user_guard.record(usage(completion=350_000))  # 70%
    assert user_guard.take_daily_warning("111") is False
    user_guard.record(usage(completion=75_000))   # 85%
    assert user_guard.take_daily_warning("111") is True
    assert user_guard.take_daily_warning("111") is False
    assert user_guard.take_daily_warning("222") is False


def test_no_heads_up_once_the_limit_itself_is_reached(user_guard, as_user):
    as_user("111")
    user_guard.record(usage(completion=600_000))  # 120%, the refusal says it instead
    assert user_guard.take_daily_warning("111") is False


async def test_a_background_task_is_billed_to_whoever_started_it(user_guard, as_user, database):
    import asyncio
    from src import usage_context

    async def background_job():
        usage_context.label("pdf_notes")
        await asyncio.sleep(0)
        user_guard.record(usage(prompt=1_000_000))
    as_user("111")
    task = asyncio.ensure_future(background_job())
    as_user("222")  # the next request arrives while the job is still running
    await task
    rows = [(r["user_id"], r["feature"]) for r in database.query("SELECT user_id, feature FROM usage_daily")]
    assert rows == [("111", "pdf_notes")]
    assert usage_context.current() == ("222", "chat")


def test_a_feature_label_ends_with_its_block(as_user):
    from src import usage_context
    as_user("111")
    with usage_context.feature("facts"):
        assert usage_context.current() == ("111", "facts")
    assert usage_context.current() == ("111", "chat")


def test_the_weekly_reset_waits_for_enough_days_to_leave(user_guard, as_user, monkeypatch):
    as_user("111")
    for day in (5, 6, 7, 8):  # $0.90 on four days is $3.60 against a $2.50 week
        set_today(monkeypatch, date(2026, 10, day))
        try:
            user_guard.check()
        except BudgetExceededError:
            pass
        user_guard.record(usage(completion=450_000))
    set_today(monkeypatch, date(2026, 10, 9))
    with pytest.raises(BudgetExceededError) as stopped:
        user_guard.check()
    # the 5th leaving still leaves $2.70. the 6th has to go too, which happens on the 13th.
    assert stopped.value.resets_on == date(2026, 10, 13)
    set_today(monkeypatch, date(2026, 10, 12))
    with pytest.raises(BudgetExceededError):
        user_guard.check()
    set_today(monkeypatch, date(2026, 10, 13))
    user_guard.check()

