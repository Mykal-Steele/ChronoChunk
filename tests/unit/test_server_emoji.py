"""Unit tests for server emoji: which ones a reply may use, and that a reply never carries a broken one."""
import random
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.message_context import history_text
from src.message_handler import MessageHandler
from src.message_processor import MessageProcessor
from src.slash_commands import SlashCommandManager
from src.server_emoji import (
    MAX_NAMES_IN_PROMPT, NO_EMOJI, EmojiSet, demote, render, usable_emoji, used_custom_emoji,
)
from tests.fakes import fake_author, fake_message

KEKW = "<:kekw:123456789012345678>"
POG = "<a:pog:223456789012345678>"
EMOJI = EmojiSet({"kekw": KEKW, "pog": POG}, ["kekw", "pog"])


def fake_emoji(name, emoji_id, animated=False, usable=True):
    emoji = MagicMock()
    emoji.name = name
    emoji.is_usable.return_value = usable
    emoji.__str__.return_value = f"<{'a' if animated else ''}:{name}:{emoji_id}>"
    return emoji


def fake_guild(guild_id, emojis, unavailable=False):
    guild = MagicMock()
    guild.id = guild_id
    guild.emojis = emojis
    guild.unavailable = unavailable
    guild.me = MagicMock(name="me")
    guild.default_role = MagicMock(name="everyone")
    return guild


def fake_channel(guild, bot_external=True, everyone_external=True):
    channel = MagicMock()
    channel.guild = guild

    def permissions_for(who):
        allowed = bot_external if who is guild.me else everyone_external
        return MagicMock(use_external_emojis=allowed)
    channel.permissions_for.side_effect = permissions_for
    return channel


# What a reply turns into

def test_a_known_name_becomes_the_emoji():
    assert render("lol :kekw:", EMOJI).text == f"lol {KEKW}"


def test_the_name_is_matched_whatever_its_case():
    assert render("lol :KEKW: fr", EMOJI).text == f"lol {KEKW} fr"


def test_an_animated_emoji_keeps_its_animated_form():
    assert render("yo :pog:", EMOJI).text == f"yo {POG}"


def test_a_name_stuck_to_a_word_still_becomes_the_emoji():
    assert render("bruh:kekw:", EMOJI).text == f"bruh{KEKW}"


@pytest.mark.parametrize("text, expected", [
    (":notreal: bruh", "bruh"),
    ("bruh :notreal:", "bruh"),
    ("bruh :notreal:, ok", "bruh, ok"),
    ("wait :notreal: what", "wait what"),
    ("line one :bad:\n:bad: line two\nline :bad: three", "line one\nline two\nline three"),
])
def test_an_unknown_name_is_taken_out_without_leaving_a_gap(text, expected):
    assert render(text, EMOJI).text == expected


def test_a_made_up_id_is_never_sent():
    assert render("<:fake:999999999999999999> hi", EMOJI).text == "hi"


def test_a_wrong_id_for_a_real_name_is_replaced_by_the_right_one():
    assert render("<:kekw:999999999999999999> hi", EMOJI).text == f"{KEKW} hi"


def test_an_id_the_bot_may_not_use_here_is_taken_out():
    """The model can copy an id from the history of a channel where the emoji is no longer usable."""
    assert render(f"hi {KEKW}", NO_EMOJI).text == "hi"


@pytest.mark.parametrize("cut", ["<:kekw:12", "<:kekw:", "<a:pog", "<:kekw:123456789012345678"])
def test_a_cut_off_emoji_is_taken_out(cut):
    assert render(f"lol {cut} ok", EMOJI).text == "lol ok"


@pytest.mark.parametrize("text", [
    "at 10:30:45 ok", "note:todo:fix", "std::vector::iterator", "<:3 cute", "ratio 16:9", "a: b: c",
])
def test_ordinary_text_with_colons_is_left_alone(text):
    assert render(text, EMOJI).text == text


def test_code_and_links_are_not_touched():
    text = "run `:kekw:` or\n```\nprint(':kekw:')\n```\nsee https://example.com/a:kekw:b"
    assert render(text, EMOJI).text == text


def test_a_standard_emoji_written_by_name_becomes_the_real_one():
    assert render(":skull: dead", EMOJI).text == "💀 dead"


def test_without_a_server_every_custom_name_is_taken_out():
    assert render("no guild :kekw: here").text == "no guild here"


def test_a_reply_that_was_only_an_unusable_emoji_still_says_something():
    assert render(":notreal:", EMOJI).text == "notreal"


def test_the_history_keeps_the_short_form_of_what_was_sent():
    shown = render("lol :kekw: :notreal:", EMOJI)
    assert shown.text == f"lol {KEKW}"
    assert shown.plain == "lol :kekw:"


# Not too many

def test_one_reply_shows_one_custom_emoji_at_most():
    assert render(":kekw: yo :pog: and :kekw:", EMOJI).text == f"{KEKW} yo and"


def test_one_reply_shows_two_emoji_at_most_with_standard_ones_counted():
    assert render("ok :kekw: 💀 🔥 😂", EMOJI).text == f"ok {KEKW} 💀"


def test_a_row_of_the_same_emoji_becomes_one():
    assert render(":kekw: :kekw: :kekw:", EMOJI).text == KEKW


def test_custom_emoji_can_be_switched_off_for_a_reply():
    assert render("lol :kekw: 💀", EMOJI, custom_allowed=False).text == "lol 💀"


def test_a_past_reply_with_a_custom_emoji_is_noticed():
    assert used_custom_emoji("lol :kekw:")
    assert used_custom_emoji(f"lol {KEKW}")
    assert not used_custom_emoji("at 10:30:45 💀 :skull:")
    assert not used_custom_emoji("plain words")


def test_custom_emoji_wait_until_the_bot_has_sent_a_few_replies_without_one():
    handler = MessageHandler()
    processor = MessageProcessor(bot=MagicMock(), message_handler=handler, ai_response_handler=MagicMock(),
                                 user_data_manager=MagicMock())

    def bot_says(text):
        handler.update_channel_history("c", "bot", "ChronoChunk", text, is_bot=True)

    assert processor.custom_emoji_due("c")
    bot_says("lol :kekw:")
    assert not processor.custom_emoji_due("c")
    handler.update_channel_history("c", "u", "Alex", "someone else :kekw:", is_bot=False)
    bot_says("one")
    bot_says("two")
    assert not processor.custom_emoji_due("c")
    bot_says("three")
    assert processor.custom_emoji_due("c")


# Which emoji the bot may use

def test_this_servers_emoji_are_usable():
    guild = fake_guild(1, [fake_emoji("kekw", 123456789012345678), fake_emoji("pog", 223456789012345678, animated=True)])
    found = usable_emoji(fake_channel(guild), [guild])
    assert found.by_name == {"kekw": KEKW, "pog": POG}
    assert found.names == ["kekw", "pog"]


def test_an_emoji_the_bot_cannot_use_is_left_out():
    """Switched off by Discord, or limited to a role the bot does not have."""
    guild = fake_guild(1, [fake_emoji("kekw", 123456789012345678, usable=False)])
    assert not usable_emoji(fake_channel(guild), [guild])


def test_another_servers_emoji_need_the_external_emoji_permission():
    here = fake_guild(1, [fake_emoji("kekw", 123456789012345678)])
    there = fake_guild(2, [fake_emoji("sadge", 323456789012345678)])
    allowed = usable_emoji(fake_channel(here, bot_external=True), [here, there])
    refused = usable_emoji(fake_channel(here, bot_external=False), [here, there])
    assert allowed.names == ["kekw", "sadge"]
    assert refused.names == ["kekw"]


def test_a_slash_command_answer_also_needs_everyone_to_have_the_permission():
    here = fake_guild(1, [])
    there = fake_guild(2, [fake_emoji("sadge", 323456789012345678)])
    channel = fake_channel(here, bot_external=True, everyone_external=False)
    assert usable_emoji(channel, [here, there]).names == ["sadge"]
    assert not usable_emoji(channel, [here, there], via_interaction=True)


def test_this_servers_emoji_wins_over_another_with_the_same_name():
    here = fake_guild(1, [fake_emoji("kekw", 123456789012345678)])
    there = fake_guild(2, [fake_emoji("KEKW", 923456789012345678)])
    assert usable_emoji(fake_channel(here), [here, there]).by_name == {"kekw": KEKW}


def test_a_server_that_is_down_lends_no_emoji():
    here = fake_guild(1, [])
    there = fake_guild(2, [fake_emoji("sadge", 323456789012345678)], unavailable=True)
    assert not usable_emoji(fake_channel(here), [here, there])


def test_a_dm_has_no_custom_emoji():
    channel = MagicMock()
    channel.guild = None
    assert not usable_emoji(channel, [fake_guild(2, [fake_emoji("sadge", 323456789012345678)])])


def test_anything_unexpected_means_no_emoji_instead_of_an_error():
    guild = fake_guild(1, [fake_emoji("kekw", 123456789012345678)])
    channel = fake_channel(guild)
    channel.permissions_for.side_effect = RuntimeError("no such channel")
    assert usable_emoji(channel, [guild]) is NO_EMOJI


def test_an_emoji_whose_text_discord_would_not_render_is_left_out():
    odd = fake_emoji("kekw", 123456789012345678)
    odd.__str__.return_value = ":kekw:"
    guild = fake_guild(1, [odd])
    assert not usable_emoji(fake_channel(guild), [guild])


# What the model is told

def test_the_prompt_lists_the_names_and_is_empty_without_any():
    assert ":kekw: :pog:" in EMOJI.prompt()
    assert NO_EMOJI.prompt() == ""


def test_the_prompt_stops_at_a_fixed_number_of_names():
    names = [f"emoji{i}" for i in range(MAX_NAMES_IN_PROMPT + 20)]
    prompt = EmojiSet({name: f"<:{name}:{123456789012345678 + i}>" for i, name in enumerate(names)}, names).prompt()
    assert f":emoji{MAX_NAMES_IN_PROMPT - 1}:" in prompt
    assert f":emoji{MAX_NAMES_IN_PROMPT}:" not in prompt


def test_the_channel_history_shows_custom_emoji_in_the_short_form():
    message = fake_message(fake_author(), f"nice {KEKW} {POG}")
    message.clean_content = f"nice {KEKW} {POG}"
    assert history_text(message) == "nice :kekw: :pog:"
    assert demote("no emoji here") == "no emoji here"


# The two ways a reply is sent

async def test_a_typed_reply_is_sent_with_real_emoji_and_remembered_in_the_short_form():
    guild = fake_guild(1, [fake_emoji("kekw", 123456789012345678)])
    bot = MagicMock()
    bot.guilds = [guild]
    processor = MessageProcessor(bot=bot, message_handler=MessageHandler(), ai_response_handler=MagicMock(),
                                 user_data_manager=MagicMock())
    message = MagicMock()
    message.channel = fake_channel(guild)
    message.reply = AsyncMock()

    kept = await processor._send_reply(message, "lol :kekw: :notreal: <:fake:999999999999999999>")

    assert message.reply.await_args.args[0] == f"lol {KEKW}"
    assert kept == "lol :kekw:"


async def test_a_slash_command_answer_checks_everyones_permission_for_other_servers_emoji():
    here = fake_guild(1, [fake_emoji("kekw", 123456789012345678)])
    there = fake_guild(2, [fake_emoji("sadge", 323456789012345678)])
    bot = MagicMock()
    bot.guilds = [here, there]
    manager = SlashCommandManager(bot, game_manager=MagicMock(), user_data_manager=MagicMock(),
                                  message_handler=MessageHandler(), ai_handler=MagicMock())
    interaction = MagicMock()
    interaction.channel = fake_channel(here, bot_external=True, everyone_external=False)
    interaction.followup.send = AsyncMock()

    await manager._send_followup(interaction, "ok :kekw: and :sadge:")

    assert interaction.followup.send.await_args.args[0] == f"ok {KEKW} and"


# Long replies

def test_a_split_never_cuts_an_emoji_in_half():
    text = "x" * 1890 + KEKW + "y" * 50
    chunks = MessageProcessor._split_text(text)
    assert "".join(chunks) == text
    assert sum(chunk.count(KEKW) for chunk in chunks) == 1
    assert all(len(chunk) <= 2000 for chunk in chunks)


# Whatever the model writes, nothing broken goes out

BITS = [
    "lol", "bruh", "ok", " ", " ", "\n", ",", ".", "!", "?", ":", "::", "<", ">", "<:", "<a:", "10:30", "💀", "🔥",
    ":kekw:", ":pog:", ":KEKW:", ":fake:", ":x:", ":skull:", KEKW, POG, "<:kekw:1>", "<:fake:999999999999999999>",
    "<a:kekw:123456789012345678>", "<:pog:223456789012345678>", "<:kekw:12345678901234567", "<:kekw", "kekw:",
]
ANY_MARKUP = re.compile(r"<a?:[A-Za-z0-9_]{2,32}(?::\d*)?>?")
ALLOWED = {KEKW, POG}


@pytest.mark.parametrize("seed", range(300))
def test_random_replies_never_carry_a_broken_emoji(seed):
    chooser = random.Random(seed)
    text = "".join(chooser.choice(BITS) for _ in range(chooser.randint(1, 25)))
    for emoji, allowed in ((EMOJI, True), (EMOJI, False), (NO_EMOJI, True)):
        shown = render(text, emoji, custom_allowed=allowed).text
        usable = ALLOWED if (emoji and allowed) else set()
        found = [m.group(0) for m in ANY_MARKUP.finditer(shown)]
        assert all(markup in usable for markup in found), (text, shown)
        assert sum(shown.count(markup) for markup in ALLOWED) <= 1, (text, shown)
        assert not re.search(r"(?<![\w:]):(?:kekw|pog|fake):(?![\w:])", shown, re.I), (text, shown)
        assert "\x00" not in shown
