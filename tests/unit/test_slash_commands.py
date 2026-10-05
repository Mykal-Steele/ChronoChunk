"""Unit tests for the Discord app commands that use the AI: /chat, /tldr and /usage."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.ai_response_handler import Reply
from src.document_builder import BuiltFile
from src.message_handler import MessageHandler
from src.rate_limiter import RateLimiter
from src.slash_commands import SlashCommandManager
from tests.fakes import fake_attachment, fake_author, fake_channel, fake_message, make_png


@pytest.fixture
async def commands(mock_bot_user):
    """Register the AI app commands on a fake bot and hand back the callbacks."""
    registered = {}

    def command(**kwargs):
        def register(func):
            registered[kwargs["name"]] = func
            return func
        return register

    bot = MagicMock()
    bot.user = mock_bot_user
    bot.tree.command = command
    bot.rate_limiter = RateLimiter()
    bot.message_processor.sync_channel_history = AsyncMock()
    bot.command_handler.summarize_channel = AsyncMock(return_value=Reply("alex wants lunch"))
    bot.command_handler.usage_summary = MagicMock(return_value="ai budget: $0.10 of $15.00 used this cycle")

    user_data_manager = MagicMock()
    user_data_manager.load_user_data = MagicMock(return_value={"username": "Kruskal", "facts": []})
    user_data_manager.add_conversation = AsyncMock()

    ai_handler = MagicMock()
    ai_handler.generate_reply = AsyncMock(return_value=Reply("yo what's good"))

    manager = SlashCommandManager(bot=bot, game_manager=MagicMock(), user_data_manager=user_data_manager,
                                  ai_handler=ai_handler, message_handler=MessageHandler(bot=None),
                                  music_manager=MagicMock())
    await manager._register_chat_command()
    await manager._register_tldr_command()
    await manager._register_usage_command()
    await manager._register_help_command()
    await manager._register_recent_posts_command()
    return registered, bot, ai_handler, user_data_manager


def _interaction():
    interaction = MagicMock()
    interaction.user = fake_author("Kruskal", 1)
    interaction.channel = fake_channel([])
    interaction.channel_id = interaction.channel.id
    interaction.guild = None
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def test_chat_answers_through_the_followup(commands):
    registered, bot, ai_handler, user_data_manager = commands
    interaction = _interaction()

    await registered["chat"](interaction, "hello", None)

    assert ai_handler.generate_reply.call_args[0][0] == "hello"
    assert interaction.followup.send.call_args[0][0] == "yo what's good"
    bot.message_processor.sync_channel_history.assert_awaited_once_with(interaction.channel)
    user_data_manager.add_conversation.assert_awaited_once()


async def test_chat_reads_an_attached_image(commands):
    registered, _, ai_handler, _ = commands
    image = fake_attachment("proof.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png")

    await registered["chat"](_interaction(), "what does this say", image)

    kwargs = ai_handler.generate_reply.call_args[1]
    assert "image: proof.png" in kwargs["attached_context"]
    assert len(kwargs["images"]) == 1
    assert kwargs["allow_files"] is True


async def test_chat_reads_an_attached_text_file(commands):
    registered, _, ai_handler, _ = commands
    notes = fake_attachment("notes.md", b"# Plan\nship friday", "text/markdown")

    await registered["chat"](_interaction(), "summarize", notes)

    assert "ship friday" in ai_handler.generate_reply.call_args[1]["attached_context"]


async def test_chat_uploads_files_the_ai_made(commands):
    registered, _, ai_handler, _ = commands
    ai_handler.generate_reply = AsyncMock(return_value=Reply("here u go", [BuiltFile("plan.docx", b"PK data")]))
    interaction = _interaction()

    await registered["chat"](interaction, "write a plan as docx", None)

    uploads = interaction.followup.send.call_args[1]["files"]
    assert [upload.filename for upload in uploads] == ["plan.docx"]


async def test_chat_is_rate_limited_per_user(commands):
    registered, bot, ai_handler, _ = commands
    bot.rate_limiter._limits = {"chat": (1, 600), "file": (1, 600), "default": (1, 600)}
    await registered["chat"](_interaction(), "one", None)
    blocked = _interaction()

    await registered["chat"](blocked, "two", None)

    assert ai_handler.generate_reply.call_count == 1
    assert "slow down" in blocked.response.send_message.call_args[0][0]
    blocked.response.defer.assert_not_called()


async def test_chat_answers_never_ping(commands):
    registered, _, _, _ = commands
    interaction = _interaction()
    await registered["chat"](interaction, "hello", None)
    allowed = interaction.followup.send.call_args[1]["allowed_mentions"]
    assert allowed.everyone is False and allowed.users is False


async def test_tldr_sums_up_the_channel_with_the_given_count(commands):
    registered, bot, _, _ = commands
    interaction = _interaction()

    await registered["tldr"](interaction, 80)

    bot.command_handler.summarize_channel.assert_awaited_once_with(interaction.channel, "Kruskal", "1", 80, prompt="")
    assert interaction.followup.send.call_args[0][0] == "alex wants lunch"


async def test_tldr_prompt_is_passed_on_and_its_file_is_attached(commands):
    registered, bot, _, _ = commands
    bot.command_handler.summarize_channel.return_value = Reply("here u go", [BuiltFile("design.pdf", b"%PDF")])
    interaction = _interaction()

    await registered["tldr"](interaction, 60, " make a pdf of the design ")

    bot.command_handler.summarize_channel.assert_awaited_once_with(
        interaction.channel, "Kruskal", "1", 60, prompt="make a pdf of the design")
    assert interaction.followup.send.call_args[0][0] == "here u go"
    assert [f.filename for f in interaction.followup.send.call_args[1]["files"]] == ["design.pdf"]


async def test_usage_shows_the_budget_line(commands):
    registered, _, _, _ = commands
    interaction = _interaction()

    await registered["usage"](interaction)

    assert "ai budget" in interaction.response.send_message.call_args[0][0]


# ── /help ─────────────────────────────────────────────────────────────────────

def _embed_text(embed) -> str:
    parts = [embed.title or "", embed.description or "", embed.footer.text or ""]
    for field in embed.fields:
        parts += [field.name, field.value]
    return "\n".join(parts)


async def test_help_states_the_real_limits(commands):
    from config.config import Config
    from src import message_context
    registered, _, _, _ = commands
    interaction = _interaction()

    await registered["help"](interaction)

    embed = interaction.response.send_message.call_args[1]["embed"]
    limits = next(field.value for field in embed.fields if "limits" in field.name)
    assert f"up to {message_context.MAX_IMAGES} images and {message_context.MAX_FILES} files per message" in limits
    assert f"up to {message_context.MAX_PDF_IMAGES} pictures from inside pdfs" in limits
    assert f"the first {message_context.MAX_PDF_PAGES} pages" in limits and "30,000 characters" in limits
    assert f"the newest {message_context.TLDR_MAX_IMAGES} images and {message_context.TLDR_MAX_FILES} files" in limits
    assert f"{Config.RATE_LIMITS['tldr'][0]} recaps an hour" in limits
    assert f"chat: {Config.RATE_LIMITS['chat'][0]} messages every 30 min" in limits
    assert f"files i make: {Config.RATE_LIMITS['file'][0]} a day, 2 per reply" in limits


async def test_help_has_no_swearing_and_fits_discords_limits(commands):
    import re
    registered, _, _, _ = commands
    interaction = _interaction()

    await registered["help"](interaction)

    embed = interaction.response.send_message.call_args[1]["embed"]
    text = _embed_text(embed)
    assert not re.search(r"\b(shit|fuck\w*|bitch|damn|ass|hell|crap|wtf|piss\w*)\b", text, re.IGNORECASE)
    assert "my g" in embed.description  # still sounds like the bot
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert len(text) <= 6000


# ── /recent-posts ─────────────────────────────────────────────────────────────

async def test_recent_posts_can_be_switched_off_by_someone_who_manages_the_channel(commands, channel_settings):
    registered, _, _, _ = commands
    interaction = _interaction()
    interaction.guild = MagicMock()
    interaction.permissions.manage_channels = True

    await registered["recent-posts"](interaction, False)

    assert channel_settings.reads_recent_posts(interaction.channel_id) is False
    assert "wont look at pics and files" in interaction.response.send_message.call_args[0][0]

    await registered["recent-posts"](interaction, True)
    assert channel_settings.reads_recent_posts(interaction.channel_id) is True


async def test_recent_posts_switch_is_refused_without_the_permission(commands, channel_settings):
    registered, _, _, _ = commands
    interaction = _interaction()
    interaction.guild = MagicMock()
    interaction.permissions.manage_channels = False

    await registered["recent-posts"](interaction, False)

    assert channel_settings.reads_recent_posts(interaction.channel_id) is True
    assert interaction.response.send_message.call_args[1]["ephemeral"] is True


async def test_chat_reads_a_just_posted_image_only_while_the_switch_is_on(commands, channel_settings):
    registered, bot, ai_handler, _ = commands
    posted = fake_message(fake_author("Alex", 777), "", attachments=[fake_attachment("shot.png", make_png(), "image/png")])
    bot.message_processor.sync_channel_history = AsyncMock(return_value=[posted])

    interaction = _interaction()
    await registered["chat"](interaction, "what is this", None)
    assert "POSTED IN THIS CHANNEL RIGHT BEFORE" in ai_handler.generate_reply.call_args[1]["attached_context"]
    assert len(ai_handler.generate_reply.call_args[1]["images"]) == 1

    channel_settings.set_reads_recent_posts(interaction.channel_id, False)
    await registered["chat"](interaction, "and now", None)
    assert ai_handler.generate_reply.call_args[1]["attached_context"] == ""
    assert ai_handler.generate_reply.call_args[1]["images"] == []


async def test_help_shows_the_version(commands):
    from src.version import __version__
    registered, _, _, _ = commands
    interaction = _interaction()

    await registered["help"](interaction)

    assert f"ChronoChunk v{__version__}" in interaction.response.send_message.call_args[1]["embed"].footer.text
