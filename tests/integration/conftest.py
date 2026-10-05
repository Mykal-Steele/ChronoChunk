"""Shared fixtures for the integration tests."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture
def pipeline(tmp_path, mock_genai_client, mock_bot_user):
    """Build a fully wired MessageProcessor with mocked externals."""
    from src.message_handler import MessageHandler
    from src.game_manager import GameManager
    from src.message_processor import MessageProcessor

    with patch("src.user_data_manager.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_genai_client
        from src.user_data_manager import UserDataManager
        udm = UserDataManager(data_dir=str(tmp_path))
    udm.ai_client = mock_genai_client
    udm.deployment = "gpt-5-mini"

    mh = MessageHandler(bot=None)
    gm = GameManager()

    ai_handler = MagicMock()
    ai_handler.generate_response = AsyncMock(return_value="yo what's good")
    ai_handler.extract_important_topics = MagicMock(return_value=[])

    mock_music = MagicMock()
    mock_music.skip = AsyncMock(return_value=(False, "Nothing is playing"))
    mock_music.pause = AsyncMock(return_value=(False, "Nothing is playing"))
    mock_music.resume = AsyncMock(return_value=(False, "Not paused"))
    mock_music.leave_voice_channel = AsyncMock(return_value=False)
    mock_music.clear_queue = MagicMock()

    from src.command_handler import CommandHandler
    ch = CommandHandler(
        bot=None,
        game_manager=gm,
        user_data_manager=udm,
        music_manager=mock_music,
    )

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
