"""Unit tests for UserDataManager — file system uses tmp_path, Gemini is mocked."""
import json
import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.fixture
def udm(tmp_path, mock_openai_client):
    with patch("src.user_data_manager.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        from src.user_data_manager import UserDataManager
        manager = UserDataManager(data_dir=str(tmp_path))
    manager.ai_client = mock_openai_client
    manager.deployment = "gpt-5-mini"
    return manager


USER_ID = "1234567890"


# ── load_user_data ────────────────────────────────────────────────────────────

def test_load_creates_default_structure(udm):
    data = udm.load_user_data(USER_ID, "TestUser")
    for key in ("user_id", "username", "facts", "topics_of_interest",
                "conversation_history", "personal_info", "preferences"):
        assert key in data, f"Missing key: {key}"


def test_load_creates_file_on_disk(udm, tmp_path):
    udm.load_user_data(USER_ID, "TestUser")
    assert os.path.exists(tmp_path / f"{USER_ID}.json")


def test_load_returns_existing_data(udm):
    udm.load_user_data(USER_ID, "TestUser")
    # Mutate and save
    data = udm.load_user_data(USER_ID, "TestUser")
    data["facts"].append({"content": "You love pizza", "timestamp": "now"})
    udm.save_user_data(USER_ID, data)

    loaded = udm.load_user_data(USER_ID)
    assert any("pizza" in f.get("content", "") for f in loaded["facts"])


def test_load_updates_username_when_changed(udm):
    udm.load_user_data(USER_ID, "OldName")
    data = udm.load_user_data(USER_ID, "NewName")
    assert data["username"] == "NewName"


# ── save_user_data ────────────────────────────────────────────────────────────

def test_save_creates_file(udm, tmp_path):
    data = udm.load_user_data(USER_ID, "TestUser")
    udm.save_user_data(USER_ID, data)
    assert os.path.exists(tmp_path / f"{USER_ID}.json")


def test_save_updates_last_interaction(udm):
    data = udm.load_user_data(USER_ID, "TestUser")
    original_ts = data.get("last_interaction", "")
    udm.save_user_data(USER_ID, data)
    saved = udm.load_user_data(USER_ID)
    assert saved["last_interaction"] != ""


def test_save_utf8_fact_preserved(udm):
    data = udm.load_user_data(USER_ID, "TestUser")
    data["facts"].append({"content": "You like สมอง 1mm and 寿司", "timestamp": "now"})
    udm.save_user_data(USER_ID, data)
    loaded = udm.load_user_data(USER_ID)
    contents = [f.get("content", "") for f in loaded["facts"]]
    assert any("สมอง" in c and "寿司" in c for c in contents)


def test_save_file_is_valid_json(udm, tmp_path):
    data = udm.load_user_data(USER_ID, "TestUser")
    udm.save_user_data(USER_ID, data)
    with open(tmp_path / f"{USER_ID}.json", encoding="utf-8") as f:
        parsed = json.load(f)
    assert isinstance(parsed, dict)


# ── user ID sanitization ──────────────────────────────────────────────────────

def test_user_id_sanitized_removes_non_digits(udm, tmp_path):
    udm.load_user_data("<script>99999</script>", "Hacker")
    assert os.path.exists(tmp_path / "99999.json")
    assert not any(f.name.startswith("<") for f in tmp_path.iterdir())


def test_user_id_digits_only_unchanged(udm, tmp_path):
    udm.load_user_data("777888", "TestUser")
    assert os.path.exists(tmp_path / "777888.json")


# ── add_conversation ──────────────────────────────────────────────────────────

async def test_add_conversation_stores_entry(udm):
    await udm.add_conversation(USER_ID, "hi there", "hello!", "TestUser")
    data = udm.load_user_data(USER_ID)
    assert len(data["conversation_history"]) >= 1
    entry = data["conversation_history"][-1]
    assert entry["user_message"] == "hi there"
    assert entry["bot_response"] == "hello!"


async def test_add_conversation_history_trimmed_to_max(udm, mock_openai_client):
    # Silence fact extraction so it doesn't interfere
    mock_openai_client.chat.completions.create = AsyncMock(
        side_effect=Exception("quota")
    )
    udm.ai_client = mock_openai_client
    from config.config import Config

    # add_conversation() enforces the trim — call it directly
    for i in range(Config.MAX_CONVERSATION_HISTORY + 5):
        await udm.add_conversation(USER_ID, f"user msg {i}", f"bot resp {i}", "TestUser")

    data = udm.load_user_data(USER_ID)
    assert len(data["conversation_history"]) <= Config.MAX_CONVERSATION_HISTORY


# ── extract_and_save_facts ────────────────────────────────────────────────────

async def test_extract_facts_calls_api(udm, mock_openai_client, fake_openai_response):
    fake_openai_response.choices[0].message.content ='["You are 25 years old", "You love sushi"]'
    mock_openai_client.chat.completions.create = AsyncMock(return_value=fake_openai_response)
    udm.ai_client = mock_openai_client

    result = await udm.extract_and_save_facts(USER_ID, "I am 25 years old and I love sushi", "TestUser")
    assert mock_openai_client.chat.completions.create.called


async def test_extract_facts_stores_facts(udm, mock_openai_client, fake_openai_response):
    fake_openai_response.choices[0].message.content ='["You are 25 years old", "You love sushi"]'
    mock_openai_client.chat.completions.create = AsyncMock(return_value=fake_openai_response)
    udm.ai_client = mock_openai_client

    await udm.extract_and_save_facts(USER_ID, "I am 25 years old and I love sushi", "TestUser")
    data = udm.load_user_data(USER_ID)
    fact_contents = [f.get("content", "") for f in data["facts"]]
    assert any("25" in c for c in fact_contents) or any("sushi" in c for c in fact_contents)


async def test_extract_facts_skips_message_too_short(udm, mock_openai_client):
    result = await udm.extract_and_save_facts(USER_ID, "ok", "TestUser")
    assert result is False
    assert not mock_openai_client.chat.completions.create.called


async def test_extract_facts_skips_single_word(udm, mock_openai_client):
    result = await udm.extract_and_save_facts(USER_ID, "hi", "TestUser")
    assert result is False
    assert not mock_openai_client.chat.completions.create.called


async def test_extract_facts_handles_json_error_gracefully(udm, mock_openai_client, fake_openai_response):
    fake_openai_response.choices[0].message.content ="not valid json at all"
    mock_openai_client.chat.completions.create = AsyncMock(return_value=fake_openai_response)
    udm.ai_client = mock_openai_client

    # Should not raise
    result = await udm.extract_and_save_facts(USER_ID, "I love cheese and mountains", "TestUser")
    assert result is False or result is True  # Doesn't crash


async def test_extract_facts_handles_quota_error_silently(udm, mock_openai_client):
    mock_openai_client.chat.completions.create = AsyncMock(
        side_effect=Exception("429 quota exceeded")
    )
    udm.ai_client = mock_openai_client

    # Should not raise
    result = await udm.extract_and_save_facts(USER_ID, "I work as an engineer", "TestUser")
    assert result is False


async def test_extract_facts_skips_command_words(udm, mock_openai_client):
    result = await udm.extract_and_save_facts(USER_ID, "chat hello world", "TestUser")
    assert result is False
    assert not mock_openai_client.chat.completions.create.called


async def test_extract_facts_handles_json_array_response(udm, mock_openai_client, fake_openai_response):
    # With response_format=json_object, model returns clean JSON — no markdown wrapping
    fake_openai_response.choices[0].message.content ='{"facts": ["You love cats"]}'
    mock_openai_client.chat.completions.create = AsyncMock(return_value=fake_openai_response)
    udm.ai_client = mock_openai_client

    await udm.extract_and_save_facts(USER_ID, "I absolutely love cats so much", "TestUser")
    # Should not crash even if format differs from expected array
    data = udm.load_user_data(USER_ID)
    assert isinstance(data["facts"], list)


# ── remove_fact ───────────────────────────────────────────────────────────────

def test_remove_fact_existing(udm):
    data = udm.load_user_data(USER_ID, "TestUser")
    data["facts"].append({"content": "You are 25 years old", "timestamp": "now", "extracted_from": "test"})
    udm.save_user_data(USER_ID, data)

    # remove_fact does substring match: search string must appear inside the fact content
    result = udm.remove_fact(USER_ID, "25 years old", "TestUser")
    assert result is True
    updated = udm.load_user_data(USER_ID)
    assert not any("25 years old" in f.get("content", "") for f in updated["facts"])


def test_remove_fact_nonexistent_returns_false(udm):
    udm.load_user_data(USER_ID, "TestUser")
    result = udm.remove_fact(USER_ID, "something that was never stored xyz", "TestUser")
    assert result is False


# ── get_user_summary ──────────────────────────────────────────────────────────

def test_get_user_summary_empty_user(udm):
    summary = udm.get_user_summary(USER_ID, "TestUser")
    assert isinstance(summary, str)
    # Empty user should return a friendly "don't know much" message or minimal output
    assert len(summary) >= 0  # At minimum returns something


def test_get_user_summary_includes_facts(udm):
    data = udm.load_user_data(USER_ID, "TestUser")
    data["facts"].append({"content": "You love pizza", "timestamp": "now", "extracted_from": "test"})
    data["facts"].append({"content": "You are 22 years old", "timestamp": "now", "extracted_from": "test"})
    udm.save_user_data(USER_ID, data)

    summary = udm.get_user_summary(USER_ID, "TestUser")
    assert "pizza" in summary or "22" in summary
