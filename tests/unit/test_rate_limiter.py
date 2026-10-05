"""Unit tests for the per-user rate limiter."""
import pytest

from src import rate_limiter as rate_limiter_module
from src.exceptions import RateLimitError
from src.rate_limiter import RateLimiter


@pytest.fixture
def clock(monkeypatch):
    """A clock the test moves by hand."""
    now = {"t": 1_000_000.0}
    monkeypatch.setattr(rate_limiter_module.time, "time", lambda: now["t"])
    return now


@pytest.fixture
def limiter(clock):
    limiter = RateLimiter()
    limiter._limits = {"chat": (3, 60), "file": (1, 3600), "default": (2, 60)}
    return limiter


def test_requests_under_the_limit_pass(limiter):
    for _ in range(3):
        limiter.check_rate_limit("1", "chat")


def test_request_over_the_limit_is_refused_with_the_wait_time(limiter, clock):
    for _ in range(3):
        limiter.check_rate_limit("1", "chat")
    clock["t"] += 20
    with pytest.raises(RateLimitError) as refused:
        limiter.check_rate_limit("1", "chat")
    assert refused.value.retry_after == pytest.approx(40)


def test_refused_requests_do_not_extend_the_wait(limiter, clock):
    for _ in range(3):
        limiter.check_rate_limit("1", "chat")
    for _ in range(5):
        with pytest.raises(RateLimitError):
            limiter.check_rate_limit("1", "chat")
    clock["t"] += 61
    limiter.check_rate_limit("1", "chat")


def test_limit_clears_after_the_window(limiter, clock):
    for _ in range(3):
        limiter.check_rate_limit("1", "chat")
    clock["t"] += 61
    limiter.check_rate_limit("1", "chat")


def test_users_are_counted_separately(limiter):
    for _ in range(3):
        limiter.check_rate_limit("1", "chat")
    limiter.check_rate_limit("2", "chat")


def test_actions_are_counted_separately(limiter):
    for _ in range(3):
        limiter.check_rate_limit("1", "chat")
    limiter.check_rate_limit("1", "file")
    with pytest.raises(RateLimitError):
        limiter.check_rate_limit("1", "file")


def test_unknown_action_uses_the_default_limit(limiter):
    limiter.check_rate_limit("1", "something-new")
    limiter.check_rate_limit("1", "something-new")
    with pytest.raises(RateLimitError):
        limiter.check_rate_limit("1", "something-new")


def test_idle_users_are_dropped_from_memory(limiter, clock):
    limiter.check_rate_limit("1", "chat")
    clock["t"] += limiter._cleanup_interval + 3601
    limiter.check_rate_limit("2", "chat")
    assert "1" not in limiter._history


def test_real_config_has_limits_for_chat_and_files():
    limits = RateLimiter()._limits
    assert {"chat", "file", "tldr", "default"} <= set(limits)
