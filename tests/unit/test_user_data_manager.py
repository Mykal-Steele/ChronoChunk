"""Unit tests for UserDataManager. The data lives in the test database and the model is mocked."""
import asyncio

import pytest
from unittest.mock import patch

from config.config import Config


@pytest.fixture
def udm(mock_openai_client):
    with patch("src.user_data_manager.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        from src.user_data_manager import UserDataManager
        manager = UserDataManager()
    manager.ai_client = mock_openai_client
    return manager


USER_ID = "1234567890"


def _facts(udm):
    return [fact["content"] for fact in udm.load_user_data(USER_ID)["facts"]]


def _model_says(fake_openai_response, text):
    fake_openai_response.choices[0].message.content = text


# ── loading ───────────────────────────────────────────────────────────────────

def test_a_new_user_starts_empty(udm):
    data = udm.load_user_data(USER_ID, "TestUser")
    assert data["user_id"] == USER_ID and data["username"] == "TestUser"
    assert data["facts"] == [] and data["topics_of_interest"] == [] and data["conversation_history"] == []


def test_a_changed_display_name_is_saved(udm):
    udm.load_user_data(USER_ID, "OldName")
    assert udm.load_user_data(USER_ID, "NewName")["username"] == "NewName"
    assert udm.load_user_data(USER_ID)["username"] == "NewName"


def test_user_ids_keep_only_their_digits(udm):
    udm.load_user_data("<@1234567890>", "TestUser")
    assert udm.store.user_count() == 1
    assert udm.load_user_data(USER_ID)["username"] == "TestUser"


# ── conversations ─────────────────────────────────────────────────────────────

async def test_add_conversation_stores_the_exchange(udm, fake_openai_response):
    _model_says(fake_openai_response, "[]")
    await udm.add_conversation(USER_ID, "hello there friend", "yo", "TestUser")
    history = udm.load_user_data(USER_ID)["conversation_history"]
    assert [(c["user_message"], c["bot_response"]) for c in history] == [("hello there friend", "yo")]


async def test_only_the_newest_conversations_are_kept(udm, fake_openai_response):
    _model_says(fake_openai_response, "[]")
    for number in range(Config.MAX_CONVERSATION_HISTORY + 5):
        await udm.add_conversation(USER_ID, f"message number {number}", "ok", "TestUser")
    history = udm.load_user_data(USER_ID)["conversation_history"]
    assert len(history) == Config.MAX_CONVERSATION_HISTORY
    assert history[0]["user_message"] == "message number 5"
    assert history[-1]["user_message"] == f"message number {Config.MAX_CONVERSATION_HISTORY + 4}"


# ── fact extraction ───────────────────────────────────────────────────────────

async def test_extracted_facts_are_stored(udm, fake_openai_response):
    _model_says(fake_openai_response, '["You like hiking", "You have a dog"]')
    assert await udm.extract_and_save_facts(USER_ID, "i like hiking and i have a dog", "TestUser") is True
    assert _facts(udm) == ["You like hiking", "You have a dog"]
    stored = udm.load_user_data(USER_ID)["facts"][0]
    assert stored["extracted_from"] == "i like hiking and i have a dog" and stored["timestamp"]


async def test_non_ascii_facts_survive(udm, fake_openai_response):
    _model_says(fake_openai_response, '["You live in กรุงเทพ"]')
    await udm.extract_and_save_facts(USER_ID, "ผมอยู่กรุงเทพ ครับ", "TestUser")
    assert _facts(udm) == ["You live in กรุงเทพ"]


async def test_json_inside_a_code_fence_is_read(udm, fake_openai_response):
    _model_says(fake_openai_response, '```json\n["You like tea"]\n```')
    await udm.extract_and_save_facts(USER_ID, "i like tea a lot", "TestUser")
    assert _facts(udm) == ["You like tea"]


async def test_a_fact_already_stored_is_not_added_twice(udm, fake_openai_response):
    _model_says(fake_openai_response, '["You like tea"]')
    await udm.extract_and_save_facts(USER_ID, "i like tea a lot", "TestUser")
    assert await udm.extract_and_save_facts(USER_ID, "did i say i like tea", "TestUser") is False
    assert _facts(udm) == ["You like tea"]


async def test_a_new_age_or_home_replaces_the_old_one(udm, fake_openai_response):
    _model_says(fake_openai_response, '["You are 20 years old", "You live in Bangkok", "You like tea"]')
    await udm.extract_and_save_facts(USER_ID, "im 20, live in bangkok, like tea", "TestUser")
    _model_says(fake_openai_response, '["You are 21 years old", "You moved to Chiang Mai"]')
    await udm.extract_and_save_facts(USER_ID, "im 21 now and moved to chiang mai", "TestUser")
    assert sorted(_facts(udm)) == ["You are 21 years old", "You like tea", "You moved to Chiang Mai"]


async def test_a_new_like_does_not_delete_an_older_one(udm, fake_openai_response):
    _model_says(fake_openai_response, '["You like tea"]')
    await udm.extract_and_save_facts(USER_ID, "i like tea a lot", "TestUser")
    _model_says(fake_openai_response, '["You like hiking", "You are a backend developer"]')
    await udm.extract_and_save_facts(USER_ID, "i like hiking and im a backend dev", "TestUser")
    assert sorted(_facts(udm)) == ["You are a backend developer", "You like hiking", "You like tea"]


@pytest.mark.parametrize("message", ["ok", "hi", "", "/x", "help me out please", "/chat about my day"])
async def test_short_messages_and_command_words_are_skipped(udm, mock_openai_client, message):
    assert await udm.extract_and_save_facts(USER_ID, message, "TestUser") is False
    mock_openai_client.chat.completions.create.assert_not_called()


async def test_an_answer_that_is_not_json_changes_nothing(udm, fake_openai_response):
    _model_says(fake_openai_response, "sorry, no facts here")
    assert await udm.extract_and_save_facts(USER_ID, "this is a long message", "TestUser") is False
    assert _facts(udm) == []


async def test_a_rate_limit_from_the_model_is_swallowed(udm, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = Exception("429 quota exceeded")
    assert await udm.extract_and_save_facts(USER_ID, "this is a long message", "TestUser") is False


async def test_facts_are_extracted_with_the_main_model_by_default(udm, mock_openai_client, fake_openai_response):
    _model_says(fake_openai_response, "[]")
    await udm.extract_and_save_facts(USER_ID, "i really like hiking a lot", "TestUser")
    call = mock_openai_client.chat.completions.create.call_args[1]
    assert call["model"] == "gpt-5-mini" and call["reasoning_effort"] == "low"


async def test_facts_use_the_background_model_when_one_is_set(mock_openai_client, fake_openai_response, monkeypatch):
    monkeypatch.setattr(Config, "AI_BACKGROUND_DEPLOYMENT", "gpt-5-nano")
    with patch("src.user_data_manager.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        from src.user_data_manager import UserDataManager
        manager = UserDataManager()
    manager.ai_client = mock_openai_client
    _model_says(fake_openai_response, '["You like hiking"]')
    assert await manager.extract_and_save_facts(USER_ID, "i really like hiking a lot", "TestUser")
    call = mock_openai_client.chat.completions.create.call_args[1]
    assert call["model"] == "gpt-5-nano" and call["reasoning_effort"] == "minimal"


# ── removing and showing ──────────────────────────────────────────────────────

async def test_remove_fact_deletes_what_matches(udm, fake_openai_response):
    _model_says(fake_openai_response, '["You like tea", "You have a dog"]')
    await udm.extract_and_save_facts(USER_ID, "i like tea and have a dog", "TestUser")
    assert udm.remove_fact(USER_ID, "TEA") is True
    assert _facts(udm) == ["You have a dog"]
    assert udm.remove_fact(USER_ID, "spaceship") is False
    assert udm.remove_fact(USER_ID, "") is False


async def test_summary_lists_facts_and_says_so_when_there_are_none(udm, fake_openai_response):
    assert "don't have any information" in udm.get_user_summary(USER_ID, "TestUser")
    _model_says(fake_openai_response, '["You like tea"]')
    await udm.extract_and_save_facts(USER_ID, "i like tea a lot", "TestUser")
    assert udm.get_user_summary(USER_ID) == "Things I know about you:\n- You like tea"


# ── forgetting ────────────────────────────────────────────────────────────────

async def test_forget_user_deletes_everything_stored(udm, fake_openai_response, database):
    _model_says(fake_openai_response, '["You like cats"]')
    await udm.add_conversation(USER_ID, "i like cats very much", "nice", "TestUser")
    udm.forget_user(USER_ID)
    for table in ("users", "facts", "topics", "conversations"):
        assert database.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 0
    udm.forget_user("999")  # someone the bot never saw


def test_forget_user_also_deletes_their_reply_log(udm):
    from unittest.mock import MagicMock
    udm.reply_log = MagicMock()
    udm.forget_user(USER_ID)
    udm.reply_log.delete_user.assert_called_once_with(USER_ID)


async def test_a_forget_during_fact_extraction_is_not_undone(udm, mock_openai_client, fake_openai_response):
    # Extraction reads the user, waits for the model, then saves. A /forget in between must win.
    model_called, let_model_answer = asyncio.Event(), asyncio.Event()

    async def slow_model(**kwargs):
        model_called.set()
        await let_model_answer.wait()
        return fake_openai_response
    _model_says(fake_openai_response, '["You like cats"]')
    mock_openai_client.chat.completions.create.side_effect = slow_model
    extraction = asyncio.ensure_future(udm.extract_and_save_facts(USER_ID, "i like cats very much", "TestUser"))
    await model_called.wait()
    udm.forget_user(USER_ID)
    let_model_answer.set()
    assert await extraction is False
    assert udm.store.user_count() == 0


async def test_a_forget_then_a_new_message_starts_clean(udm, mock_openai_client, fake_openai_response):
    model_called, let_model_answer = asyncio.Event(), asyncio.Event()

    async def slow_model(**kwargs):
        model_called.set()
        await let_model_answer.wait()
        return fake_openai_response
    _model_says(fake_openai_response, '["You like cats"]')
    mock_openai_client.chat.completions.create.side_effect = slow_model
    extraction = asyncio.ensure_future(udm.extract_and_save_facts(USER_ID, "i like cats very much", "TestUser"))
    await model_called.wait()
    udm.forget_user(USER_ID)
    udm.load_user_data(USER_ID, "TestUser")  # they talk to the bot again before the old extraction ends
    let_model_answer.set()
    assert await extraction is False
    assert _facts(udm) == []
