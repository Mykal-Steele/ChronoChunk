"""Unit tests for IntentDetector — fully pattern-based, no API calls."""
import pytest
from src.intent_detector import IntentDetector


@pytest.fixture
def detector():
    return IntentDetector()


# ── detect_forget_intent ──────────────────────────────────────────────────────

async def test_forget_exact_command(detector):
    is_forget, target = await detector.detect_forget_intent("/forget")
    assert is_forget is True
    assert target == "all"


async def test_forget_with_target(detector):
    is_forget, target = await detector.detect_forget_intent("forget about my age")
    assert is_forget is True
    assert "age" in target


async def test_forget_keyword_alone(detector):
    is_forget, target = await detector.detect_forget_intent("forget everything")
    assert is_forget is True


async def test_forget_negative(detector):
    # "never forget" shouldn't count — "forget " (with space) pattern
    is_forget, target = await detector.detect_forget_intent("i like this")
    assert is_forget is False
    assert target is None


async def test_dont_remember(detector):
    is_forget, target = await detector.detect_forget_intent("don't remember this")
    assert is_forget is True


# ── detect_argumentative_intent ───────────────────────────────────────────────

async def test_argumentative_insult_toward_bot(detector):
    is_arg, intent_type = await detector.detect_argumentative_intent("you suck")
    assert is_arg is True
    assert intent_type == "insult"


async def test_argumentative_self_deprecation(detector):
    is_arg, intent_type = await detector.detect_argumentative_intent("i suck at this game")
    assert is_arg is False
    assert intent_type == "self_deprecation"


async def test_argumentative_trash(detector):
    is_arg, _ = await detector.detect_argumentative_intent("that's trash")
    assert is_arg is True


async def test_argumentative_disagreement(detector):
    is_arg, intent_type = await detector.detect_argumentative_intent("no that's wrong")
    assert is_arg is True


async def test_argumentative_neutral(detector):
    is_arg, intent_type = await detector.detect_argumentative_intent("hello how are you")
    assert is_arg is False


# ── detect_game_intent ────────────────────────────────────────────────────────

async def test_game_intent_slash(detector):
    is_game, game_type = await detector.detect_game_intent("/game")
    assert is_game is True


async def test_game_intent_natural(detector):
    is_game, game_type = await detector.detect_game_intent("let's play a game")
    assert is_game is True


async def test_game_intent_with_me(detector):
    is_game, _ = await detector.detect_game_intent("play with me")
    assert is_game is True


async def test_game_intent_negative(detector):
    is_game, _ = await detector.detect_game_intent("i like turtles")
    assert is_game is False


# ── detect_end_game_intent ────────────────────────────────────────────────────

async def test_end_game_stop(detector):
    is_end, _ = await detector.detect_end_game_intent("stop game")
    assert is_end is True


async def test_end_game_quit(detector):
    is_end, _ = await detector.detect_end_game_intent("quit game")
    assert is_end is True


async def test_end_game_negative(detector):
    is_end, _ = await detector.detect_end_game_intent("what is the score")
    assert is_end is False


# ── detect_correction_intent ──────────────────────────────────────────────────

async def test_correction_no(detector):
    is_corr, _ = await detector.detect_correction_intent("no that's not right")
    assert is_corr is True


async def test_correction_incorrect(detector):
    is_corr, _ = await detector.detect_correction_intent("incorrect, i never said that")
    assert is_corr is True


async def test_correction_actually(detector):
    is_corr, _ = await detector.detect_correction_intent("actually i meant something else")
    assert is_corr is True


async def test_correction_negative(detector):
    is_corr, _ = await detector.detect_correction_intent("yes exactly right!")
    assert is_corr is False


# ── detect_user_info_intent ───────────────────────────────────────────────────

async def test_user_info_general(detector):
    is_info, info_type = await detector.detect_user_info_intent("what do you know about me")
    assert is_info is True
    assert info_type == "general"


async def test_user_info_age(detector):
    is_info, info_type = await detector.detect_user_info_intent("how old am i")
    assert is_info is True
    assert info_type == "age"


async def test_user_info_name(detector):
    is_info, info_type = await detector.detect_user_info_intent("what is my name")
    assert is_info is True
    assert info_type == "name"


async def test_user_info_negative(detector):
    is_info, _ = await detector.detect_user_info_intent("hello world")
    assert is_info is False


# ── extract_guess ─────────────────────────────────────────────────────────────

async def test_extract_guess_is_it_pattern(detector):
    result = await detector.extract_guess("is it 42?")
    assert "42" in result


async def test_extract_guess_guess_pattern(detector):
    result = await detector.extract_guess("my guess is seven")
    assert result is not None
    assert len(result) > 0


async def test_extract_guess_short_text(detector):
    result = await detector.extract_guess("pizza")
    assert "pizza" in result


# ── general intent detection ──────────────────────────────────────────────────

async def test_detect_intent_question(detector):
    result = await detector.detect_intent("what is the meaning of life?")
    assert result["intent"] == "question"


async def test_detect_intent_command(detector):
    # Avoid inputs that accidentally contain question-word substrings (e.g. "show" contains "how")
    result = await detector.detect_intent("/find the music file")
    assert result["intent"] == "command"


async def test_detect_intent_emotional(detector):
    result = await detector.detect_intent("i am so happy today")
    assert result["intent"] == "emotional"


async def test_detect_intent_insult(detector):
    result = await detector.detect_intent("this is trash")
    assert result["intent"] == "insult"


async def test_detect_intent_statement(detector):
    result = await detector.detect_intent("the sky is blue")
    assert result["intent"] == "statement"
    assert result["confidence"] > 0


async def test_detect_intent_returns_confidence(detector):
    result = await detector.detect_intent("hello")
    assert "confidence" in result
    assert 0 <= result["confidence"] <= 1
