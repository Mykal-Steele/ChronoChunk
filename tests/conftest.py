"""Shared fixtures for all ChronoChunk tests."""
import os
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Guarantee env vars exist before any module import
os.environ.setdefault("DISCORD_TOKEN", "fake_discord_token_for_tests")
os.environ.setdefault("AZURE_OPENAI_KEY", "fake_azure_key_for_tests")
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com/openai/v1")
os.environ.setdefault("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
os.environ.setdefault("GEMINI_API_KEY", "fake_gemini_key_for_tests")

import discord


class AsyncIterator:
    """Minimal async iterator for mocking discord channel.history()."""
    def __init__(self, items=None):
        self._items = list(items or [])

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._items:
            return self._items.pop(0)
        raise StopAsyncIteration


@pytest.fixture
def fake_openai_response():
    """Simulates an OpenAI ChatCompletion response."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = MagicMock()
    resp.choices[0].message.content = "test ai response"
    return resp


@pytest.fixture
def mock_openai_client(fake_openai_response):
    """AsyncOpenAI client mock — chat.completions.create is an AsyncMock."""
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=fake_openai_response)
    return client


# Keep legacy names so tests that already import these still work
@pytest.fixture
def fake_genai_response(fake_openai_response):
    return fake_openai_response


@pytest.fixture
def mock_genai_client(mock_openai_client):
    return mock_openai_client


@pytest.fixture
def mock_discord_guild():
    guild = MagicMock(spec=discord.Guild)
    guild.id = 11111
    guild.emojis = []
    return guild


@pytest.fixture
def mock_discord_channel(mock_discord_guild):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 99999
    channel.guild = mock_discord_guild
    channel.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    typing_cm = MagicMock()
    typing_cm.__aenter__ = AsyncMock(return_value=None)
    typing_cm.__aexit__ = AsyncMock(return_value=None)
    channel.typing = MagicMock(return_value=typing_cm)
    channel.history = MagicMock(return_value=AsyncIterator([]))
    return channel


@pytest.fixture
def mock_discord_message(mock_discord_channel, mock_discord_guild):
    msg = MagicMock(spec=discord.Message)
    msg.author = MagicMock(spec=discord.Member)
    msg.author.id = 12345
    msg.author.name = "TestUser"
    msg.author.display_name = "TestUser"
    msg.author.bot = False
    msg.channel = mock_discord_channel
    msg.guild = mock_discord_guild
    msg.content = "test message"
    msg.reference = None
    msg.id = 54321
    return msg


@pytest.fixture
def mock_bot_user():
    user = MagicMock(spec=discord.ClientUser)
    user.id = 99001
    user.name = "ChronoChunk"
    user.bot = True
    return user
