"""Unit tests for Config — validates structure, directories, and env var checks."""
import os
import pytest
from config.config import Config


def test_important_topics_is_nonempty():
    assert len(Config.IMPORTANT_TOPICS) > 0


def test_important_topics_contains_mental_health():
    assert "depression" in Config.IMPORTANT_TOPICS
    assert "suicide" in Config.IMPORTANT_TOPICS


def test_important_topics_contains_violence():
    assert "violence" in Config.IMPORTANT_TOPICS


def test_rate_limits_has_required_keys():
    required = {"chat", "game", "info", "forget", "mydata"}
    assert required.issubset(set(Config.RATE_LIMITS.keys()))


def test_rate_limits_values_are_tuples_of_two():
    for action, limit in Config.RATE_LIMITS.items():
        assert isinstance(limit, tuple) and len(limit) == 2, f"Bad rate limit for {action}"


def test_max_game_attempts_is_positive():
    assert Config.MAX_GAME_ATTEMPTS > 0


def test_memory_size_is_positive():
    assert Config.MEMORY_SIZE > 0


def test_channel_history_size_is_positive():
    assert Config.CHANNEL_HISTORY_SIZE > 0


def test_max_conversation_history_is_positive():
    assert Config.MAX_CONVERSATION_HISTORY > 0


def test_ai_model_is_set():
    assert Config.AI_MODEL and "gemini" in Config.AI_MODEL.lower()


def test_ensure_directories_creates_dirs(tmp_path, monkeypatch):
    test_user_dir = str(tmp_path / "user_data")
    test_log_dir = str(tmp_path / "logs")
    monkeypatch.setattr(Config, "USER_DATA_DIR", test_user_dir)
    monkeypatch.setattr(Config, "LOG_DIR", test_log_dir)

    Config.ensure_directories()

    assert os.path.isdir(test_user_dir)
    assert os.path.isdir(test_log_dir)


def test_validate_raises_on_missing_discord_token(monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setattr(Config, "DISCORD_TOKEN", None)
    with pytest.raises(ValueError, match="DISCORD_TOKEN"):
        Config.validate()


def test_validate_returns_dict_on_success():
    # DISCORD_TOKEN is set from .env, so validate should succeed
    if not Config.DISCORD_TOKEN:
        pytest.skip("DISCORD_TOKEN not set — cannot test success path")
    result = Config.validate()
    assert isinstance(result, dict)
    assert result.get("discord_token") is True


def test_web_port_is_integer():
    assert isinstance(Config.WEB_PORT, int)
    assert Config.WEB_PORT > 0
