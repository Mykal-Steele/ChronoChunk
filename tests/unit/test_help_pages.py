"""/help is a short start page plus one page per topic, and no command is left out of all of them."""
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.command_handler import CommandHandler
from src.help_menu import TOPICS, build_help_embed, build_topic_help
from src.rate_limiter import RateLimiter

PAGES = ("memory", "files", "courses", "music", "limits", "uniloom")


def _text(embed) -> str:
    parts = [embed.title or "", embed.description or "", embed.footer.text or ""]
    for field in embed.fields:
        parts += [field.name, field.value]
    return "\n".join(parts)


def _all_help() -> str:
    return "\n".join([_text(build_help_embed())] + [_text(build_topic_help(topic)) for topic in PAGES])


def test_every_slash_command_is_on_a_help_page():
    source = open("src/slash_commands.py").read()
    commands = set(re.findall(r'tree\.command\(name="([\w-]+)"', source))
    assert len(commands) > 20
    text = _all_help()
    # The whole name, so `/stop` is not satisfied by `/stopwatch`
    missing = sorted(name for name in commands if not re.search(rf"`/{re.escape(name)}[` ]", text))
    assert missing == []


def test_the_start_page_points_to_every_topic():
    text = _text(build_help_embed())
    for topic in PAGES:
        assert f"`/help {topic}`" in text
    assert set(TOPICS) == set(PAGES)


def test_the_start_page_is_short():
    # It used to hold everything and ran close to Discord's 6000 character limit
    assert len(_text(build_help_embed())) <= 3000


@pytest.mark.parametrize("topic", PAGES)
def test_each_page_fits_discord_and_stays_clean(topic):
    embed = build_topic_help(topic)
    text = _text(embed)
    assert not re.search(r"\b(shit|fuck\w*|bitch|damn|ass|hell|crap|wtf|piss\w*)\b", text, re.IGNORECASE)
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert len(text) <= 6000
    assert "—" not in text


def test_the_memory_page_explains_notes_the_saved_line_and_deleting():
    text = _text(build_topic_help("memory"))
    for needed in ("public", "private", "DM", "saved as m12", "`/memory delete:m12`", "`/memory view:server`",
                   "`/forget`", "👎", "stop roasting me"):
        assert needed in text, needed


def test_the_memory_page_states_the_configured_keep_time(monkeypatch):
    from config.config import Config
    monkeypatch.setattr(Config, "CHANNEL_LOG_KEEP_DAYS", 365)
    text = _text(build_topic_help("memory"))
    assert "365 days" in text and "30 days" not in text


@pytest.mark.parametrize("topic", PAGES)
async def test_typed_help_with_a_topic_shows_that_page(topic):
    handler = CommandHandler(bot=None, rate_limiter=RateLimiter())
    embed = await handler._handle_help([topic], MagicMock(), "1")
    assert embed is not None and embed.title == build_topic_help(topic).title


async def test_typed_help_with_other_words_is_still_chat():
    handler = CommandHandler(bot=None, rate_limiter=RateLimiter())
    assert await handler._handle_help(["me", "write", "this"], MagicMock(), "1") is None


async def test_slash_help_offers_every_topic(mock_bot_user):
    from src.slash_commands import SlashCommandManager
    registered = {}

    def command(**kwargs):
        def register(func):
            registered[kwargs["name"]] = func
            return func
        return register

    bot = MagicMock()
    bot.user = mock_bot_user
    bot.tree.command = command
    manager = SlashCommandManager(bot=bot, game_manager=MagicMock(), user_data_manager=MagicMock(),
                                  ai_handler=MagicMock(), message_handler=MagicMock(), music_manager=MagicMock())
    await manager._register_help_command()
    for topic in PAGES:
        interaction = MagicMock()
        interaction.response.send_message = AsyncMock()
        await registered["help"](interaction, topic)
        assert interaction.response.send_message.call_args[1]["embed"].title == build_topic_help(topic).title


# ── no code blocks for things that are not code ──────────────────────────────

def test_usage_setup_and_stats_are_not_code_blocks(database):
    from src import setup_info, stats
    handler = CommandHandler(bot=None, rate_limiter=RateLimiter())
    for text in (handler.usage_summary("42"), setup_info.setup_summary("gpt-6-luna"), stats.stats_summary()):
        assert "```" not in text


@pytest.mark.parametrize("topic", (None,) + PAGES)
def test_no_footer_has_backticks_since_footers_show_no_formatting(topic):
    embed = build_help_embed() if topic is None else build_topic_help(topic)
    assert "`" not in (embed.footer.text or "")



def test_the_start_page_still_says_what_is_kept_and_shared():
    text = _text(build_help_embed())
    assert "shared with every server" in text and "private" in text


def test_the_memory_page_does_not_promise_more_control_than_there_is():
    text = _text(build_topic_help("memory"))
    assert "only u can change what u said about urself" not in text
    assert "server manager" in text


def test_every_page_has_the_same_footer():
    footers = {build_topic_help(topic).footer.text for topic in PAGES}
    assert len(footers) == 1


# ── labelled rows ────────────────────────────────────────────────────────────

def test_labelled_rows_read_as_plain_discord_text():
    from src.stats import labelled
    rows = [("version", "3.30.0"), ("", ""), ("by job", ""), ("  chat", "5 calls"), ("  pending", "")]
    assert labelled(rows) == "**version**: 3.30.0\n\n**by job**\n- chat: 5 calls\n- pending"


def test_labelled_values_cannot_turn_into_italics_or_strikes():
    from src.stats import labelled
    assert labelled([("host", "Standard_D2_v3 ~~x~~ *y*")]) == "**host**: Standard\\_D2\\_v3 \\~\\~x\\~\\~ \\*y\\*"


def test_the_uniloom_page_says_how_to_connect_and_that_changes_wait():
    text = _text(build_topic_help("uniloom"))
    assert "`/uniloom connect`" in text and "`/uniloom disconnect`" in text
    assert "do it" in text
    # Whose account it uses, and that nobody else can use it
    assert "only u" in text
