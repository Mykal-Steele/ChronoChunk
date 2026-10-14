"""Shared fixtures for the integration tests."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture
def pipeline(tmp_path, mock_genai_client, mock_bot_user, monkeypatch):
    """Build a fully wired MessageProcessor with mocked externals."""
    from config.config import Config
    # Joining in uncalled is a coin flip. Here the coin always lands on joining in.
    monkeypatch.setattr(Config, "JOIN_IN_CHANCE", 1.0)
    from src.message_handler import MessageHandler
    from src.game_manager import GameManager
    from src.message_processor import MessageProcessor

    with patch("src.user_data_manager.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_genai_client
        from src.user_data_manager import UserDataManager
        udm = UserDataManager()
    udm.ai_client = mock_genai_client
    udm.deployment = "gpt-5-mini"

    mh = MessageHandler(bot=None)
    gm = GameManager()

    from src.ai_response_handler import Reply

    ai_handler = MagicMock()
    ai_handler.generate_reply = AsyncMock(return_value=Reply("yo what's good"))
    ai_handler.generate_response = AsyncMock(return_value="yo what's good")

    from src.command_handler import CommandHandler
    ch = CommandHandler(bot=None)

    bot = MagicMock()
    bot.user = mock_bot_user

    processor = MessageProcessor(
        bot=bot,
        message_handler=mh,
        ai_response_handler=ai_handler,
        user_data_manager=udm,
        game_manager=gm,
        command_handler=ch,
    )

    return processor, mh, gm, udm, ai_handler, bot
