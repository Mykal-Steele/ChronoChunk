"""Unit tests for the AI budget guard: cost estimates, caps, cycle rollover and the guarded client."""
import json
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
def guard(tmp_path):
    return UsageGuard(ledger_path=str(tmp_path / "ai_usage.json"), monthly_budget=1.0, daily_budget=0.5, reset_day=14)


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


# ── ledger file ───────────────────────────────────────────────────────────────

def test_totals_survive_a_restart(tmp_path):
    path = str(tmp_path / "ai_usage.json")
    first = UsageGuard(ledger_path=path, monthly_budget=1.0, daily_budget=0.5, reset_day=14)
    first.record(usage(prompt=1_000_000))  # $0.25
    second = UsageGuard(ledger_path=path, monthly_budget=1.0, daily_budget=0.5, reset_day=14)
    assert second.summary()["cycle_cost"] == pytest.approx(0.25)
    assert second.summary()["requests"] == 1


def test_blocked_state_survives_a_restart(tmp_path):
    path = str(tmp_path / "ai_usage.json")
    UsageGuard(ledger_path=path, monthly_budget=1.0, daily_budget=0.5, reset_day=14).record(usage(completion=600_000))
    with pytest.raises(BudgetExceededError):
        UsageGuard(ledger_path=path, monthly_budget=1.0, daily_budget=0.5, reset_day=14).check()


def test_calls_without_token_counts_write_nothing(guard, tmp_path):
    assert guard.record(MagicMock()) == 0.0
    assert not (tmp_path / "ai_usage.json").exists()


def test_a_corrupt_ledger_does_not_crash(tmp_path):
    path = tmp_path / "ai_usage.json"
    path.write_text("{not json", encoding="utf-8")
    guard = UsageGuard(ledger_path=str(path), monthly_budget=1.0, daily_budget=0.5, reset_day=14)
    guard.record(usage(prompt=1_000_000))
    assert json.loads(path.read_text(encoding="utf-8"))["requests"] == 1


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
