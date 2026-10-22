"""The request gate keeps one bad burst from opening unbounded model calls."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.exceptions import RequestBusy
from src.request_governor import RequestGovernor


@pytest.fixture
def handler(mock_openai_client):
    with patch("src.ai_response_handler.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        from src.ai_response_handler import AIResponseHandler
        instance = AIResponseHandler(
            endpoint="https://fake.openai.azure.com/openai/v1",
            api_key="fake_key",
            deployment="gpt-5-mini",
        )
    instance.ai_client = mock_openai_client
    return instance


async def test_a_slot_is_released_after_the_work_finishes():
    governor = RequestGovernor(max_concurrent=1, max_queued=1, queue_timeout=1)
    async with governor.slot():
        assert governor.waiting == 0
    assert governor.waiting == 0
    async with governor.slot():
        pass


async def test_a_full_queue_is_refused_immediately():
    governor = RequestGovernor(max_concurrent=1, max_queued=0, queue_timeout=1)
    async with governor.slot():
        with pytest.raises(RequestBusy):
            async with governor.slot():
                pass


async def test_waiting_too_long_is_refused():
    governor = RequestGovernor(max_concurrent=1, max_queued=1, queue_timeout=0.02)
    async with governor.slot():
        with pytest.raises(RequestBusy):
            async with governor.slot():
                pass


async def test_the_handler_returns_a_clean_timeout_reply(handler, monkeypatch):
    from config.config import Config

    monkeypatch.setattr(Config, "AI_REQUEST_TIMEOUT_SECONDS", 0.02)

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    handler._generate_reply = AsyncMock(side_effect=slow)
    handler.reply_log = MagicMock()
    reply = await handler.generate_reply("slow question", "", "Kruskal", "1")
    assert "took too long" in reply.text
    assert handler.reply_log.record.call_args.kwargs["error"].startswith("timeout")


async def test_the_handler_turns_a_full_queue_into_a_retry_message(handler, monkeypatch):
    from config.config import Config
    from src.request_governor import RequestGovernor

    monkeypatch.setattr(Config, "AI_REQUEST_TIMEOUT_SECONDS", 5)
    handler.governor = RequestGovernor(max_concurrent=1, max_queued=0, queue_timeout=1)
    handler.reply_log = MagicMock()
    handler._generate_reply = AsyncMock()
    async with handler.governor.slot():
        reply = await handler.generate_reply("busy question", "", "Kruskal", "1")
    assert "juggling too many" in reply.text
    assert handler.reply_log.record.call_args.kwargs["error"].startswith("busy")
    handler._generate_reply.assert_not_called()


async def test_one_reply_cannot_make_unlimited_model_calls(handler, monkeypatch):
    from config.config import Config

    monkeypatch.setattr(Config, "AI_MAX_MODEL_CALLS_PER_REPLY", 0)
    handler.reply_log = MagicMock()
    reply = await handler.generate_reply("a normal question", "", "Kruskal", "1")
    assert "hit the limit" in reply.text
    assert handler.reply_log.record.call_args.kwargs["error"].startswith("reply limit")
