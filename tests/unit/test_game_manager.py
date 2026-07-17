"""Unit tests for GameManager — no external dependencies."""
import pytest
from src.game_manager import GameManager, GameState
from config.config import Config

USER_A = 1001
USER_B = 2002


@pytest.fixture
def gm():
    return GameManager()


# ── start_game ────────────────────────────────────────────────────────────────

def test_start_game_success(gm):
    ok, msg = gm.start_game(USER_A, 100)
    assert ok is True
    assert "1 and 100" in msg
    assert gm.get_active_game(USER_A) is not None


def test_start_game_stores_correct_range(gm):
    gm.start_game(USER_A, 50)
    game = gm.get_active_game(USER_A)
    assert game.max_range == 50
    assert 1 <= game.secret_number <= 50


def test_start_game_full_attempts(gm):
    gm.start_game(USER_A, 100)
    game = gm.get_active_game(USER_A)
    assert game.attempts_left == Config.MAX_GAME_ATTEMPTS


def test_start_game_while_active_returns_error(gm):
    gm.start_game(USER_A, 100)
    ok, msg = gm.start_game(USER_A, 100)
    assert ok is False
    assert "already" in msg.lower()


def test_start_game_range_zero_returns_error(gm):
    ok, msg = gm.start_game(USER_A, 0)
    assert ok is False
    assert gm.get_active_game(USER_A) is None


def test_start_game_negative_range_returns_error(gm):
    ok, msg = gm.start_game(USER_A, -10)
    assert ok is False
    assert gm.get_active_game(USER_A) is None


def test_start_game_range_one_is_valid(gm):
    # max_range < 1 check — range of 1 is valid (secret will always be 1, but allowed)
    ok, _ = gm.start_game(USER_A, 1)
    assert ok is True


# ── make_guess ────────────────────────────────────────────────────────────────

def test_make_guess_no_active_game(gm):
    ok, msg = gm.make_guess(USER_A, 5)
    assert ok is False
    assert "don't have a game" in msg.lower()


def test_make_guess_correct(gm):
    gm.start_game(USER_A, 100)
    secret = gm.active_games[USER_A].secret_number
    ok, msg = gm.make_guess(USER_A, secret)
    assert ok is True
    assert "got it" in msg.lower() or "yooo" in msg.lower()
    assert gm.get_active_game(USER_A) is None


def test_make_guess_too_low_gives_higher_hint(gm):
    gm.start_game(USER_A, 100)
    gm.active_games[USER_A].secret_number = 80
    ok, msg = gm.make_guess(USER_A, 10)
    assert ok is False
    assert "higher" in msg.lower()


def test_make_guess_too_high_gives_lower_hint(gm):
    gm.start_game(USER_A, 100)
    gm.active_games[USER_A].secret_number = 10
    ok, msg = gm.make_guess(USER_A, 80)
    assert ok is False
    assert "lower" in msg.lower()


def test_make_guess_decrements_attempts(gm):
    gm.start_game(USER_A, 100)
    gm.active_games[USER_A].secret_number = 50
    before = gm.active_games[USER_A].attempts_left
    gm.make_guess(USER_A, 1)
    assert gm.active_games[USER_A].attempts_left == before - 1


def test_make_guess_game_over_on_last_wrong_guess(gm):
    gm.start_game(USER_A, 100)
    gm.active_games[USER_A].secret_number = 50
    gm.active_games[USER_A].attempts_left = 1
    ok, msg = gm.make_guess(USER_A, 1)
    assert ok is False
    assert "game over" in msg.lower()
    assert gm.get_active_game(USER_A) is None


def test_make_guess_reveals_secret_on_game_over(gm):
    gm.start_game(USER_A, 100)
    gm.active_games[USER_A].secret_number = 42
    gm.active_games[USER_A].attempts_left = 1
    _, msg = gm.make_guess(USER_A, 1)
    assert "42" in msg


# ── end_game ──────────────────────────────────────────────────────────────────

def test_end_game_active(gm):
    gm.start_game(USER_A, 100)
    ok, msg = gm.end_game(USER_A)
    assert ok is True
    assert "gg" in msg.lower()
    assert gm.get_active_game(USER_A) is None


def test_end_game_no_active_game(gm):
    ok, msg = gm.end_game(USER_A)
    assert ok is False
    assert "don't" in msg.lower() or "no game" in msg.lower()


# ── multi-user isolation ───────────────────────────────────────────────────────

def test_two_users_isolated(gm):
    gm.start_game(USER_A, 100)
    gm.start_game(USER_B, 50)
    gm.active_games[USER_A].secret_number = 10
    gm.active_games[USER_B].secret_number = 25

    ok_a, _ = gm.make_guess(USER_A, 10)  # User A wins
    assert ok_a is True
    assert gm.get_active_game(USER_A) is None
    assert gm.get_active_game(USER_B) is not None  # User B game intact


def test_end_one_user_does_not_affect_other(gm):
    gm.start_game(USER_A, 100)
    gm.start_game(USER_B, 100)
    gm.end_game(USER_A)
    assert gm.get_active_game(USER_B) is not None
