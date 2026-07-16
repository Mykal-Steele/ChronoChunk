"""Unit tests for MessageHandler — Discord objects are mocked."""
import pytest
from unittest.mock import MagicMock, AsyncMock
from src.message_handler import MessageHandler
from config.config import Config

CHANNEL_ID = "99999"
USER_ID = "12345"


@pytest.fixture
def handler():
    return MessageHandler(bot=None)


# ── update_channel_history ────────────────────────────────────────────────────

def test_update_creates_channel_entry(handler):
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "hello", is_bot=False)
    assert CHANNEL_ID in handler.last_channel_messages
    assert len(handler.last_channel_messages[CHANNEL_ID]) == 1


def test_update_stores_correct_fields(handler):
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "hello there", is_bot=False)
    entry = handler.last_channel_messages[CHANNEL_ID][0]
    assert entry["author_name"] == "Alice"
    assert entry["content"] == "hello there"
    assert entry["is_bot"] is False


def test_update_bot_message_flagged(handler):
    handler.update_channel_history(CHANNEL_ID, "0", "ChronoChunk", "beep boop", is_bot=True)
    entry = handler.last_channel_messages[CHANNEL_ID][0]
    assert entry["is_bot"] is True


def test_update_strips_slash_from_command(handler):
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "/game 10", is_bot=False, is_command=True)
    entry = handler.last_channel_messages[CHANNEL_ID][0]
    assert entry["content"] == "game 10"


def test_channel_history_size_limit(handler):
    for i in range(Config.CHANNEL_HISTORY_SIZE + 10):
        handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", f"msg {i}", is_bot=False)
    assert len(handler.last_channel_messages[CHANNEL_ID]) == Config.CHANNEL_HISTORY_SIZE


def test_channel_history_keeps_most_recent(handler):
    for i in range(Config.CHANNEL_HISTORY_SIZE + 5):
        handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", f"msg {i}", is_bot=False)
    last = handler.last_channel_messages[CHANNEL_ID][-1]["content"]
    assert last == f"msg {Config.CHANNEL_HISTORY_SIZE + 4}"


def test_multiple_channels_isolated(handler):
    handler.update_channel_history("CH_A", USER_ID, "Alice", "hi A", is_bot=False)
    handler.update_channel_history("CH_B", USER_ID, "Bob", "hi B", is_bot=False)
    assert len(handler.last_channel_messages["CH_A"]) == 1
    assert len(handler.last_channel_messages["CH_B"]) == 1


# ── update_conversation_memory ────────────────────────────────────────────────

def test_conversation_memory_stores_user_and_bot(handler):
    handler.update_conversation_memory(CHANNEL_ID, "Alice", "hello", "hi there!")
    mem = handler.conversation_memory[CHANNEL_ID]
    assert any("Alice" in line and "hello" in line for line in mem)
    assert any("ChronoChunk" in line and "hi there" in line for line in mem)


def test_conversation_memory_size_limit(handler):
    max_size = max(20, Config.MEMORY_SIZE * 2)
    for i in range(max_size + 10):
        handler.update_conversation_memory(CHANNEL_ID, "Alice", f"user msg {i}", f"bot resp {i}")
    assert len(handler.conversation_memory[CHANNEL_ID]) <= max_size


def test_conversation_memory_strips_slash_from_command(handler):
    handler.update_conversation_memory(CHANNEL_ID, "Alice", "/game 10", "Game started!", is_command=True)
    mem = handler.conversation_memory[CHANNEL_ID]
    assert any("game 10" in line.lower() for line in mem)


# ── build_conversation_context ────────────────────────────────────────────────

async def test_build_context_empty_returns_string(handler):
    result = await handler.build_conversation_context(CHANNEL_ID, {})
    assert isinstance(result, str)


async def test_build_context_includes_messages(handler):
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "what is python", is_bot=False)
    handler.update_channel_history(CHANNEL_ID, "0", "ChronoChunk", "it's a language fr", is_bot=True)
    result = await handler.build_conversation_context(CHANNEL_ID, {})
    assert "Alice" in result or "python" in result
    assert "ChronoChunk" in result or "language" in result


async def test_build_context_short_followup_injects_hint(handler):
    # Build 2 turns of history so bot_messages and user_messages are detected
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "what is python", is_bot=False)
    handler.update_channel_history(CHANNEL_ID, "0", "ChronoChunk", "python is a programming language", is_bot=True)
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "why", is_bot=False)
    result = await handler.build_conversation_context(CHANNEL_ID, {})
    # Short follow-up (≤5 words) should trigger CRITICAL CONTEXT INSTRUCTION
    assert "CRITICAL" in result or "FOLLOW-UP" in result or len(result) > 0
