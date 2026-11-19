"""Layer 5 of the memory plan: people see what the bot knows about them, and can delete it."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.channel_log import ChannelLog
from src.lessons import LessonStore
from src.memory_control import about_me, about_server, delete_item
from src.memory_store import MemoryStore, Place, Speaker
from tests.unit.test_memory_sleep import TODAY, _message

SLADE = Speaker("760", "Slade")
MINT = Speaker("222", "Mint")
KRUSKAL = Speaker("111", "Kruskal")
KMUTT = Place("1280", "cs-kmutt", "55", "general")
XLRX = Place("1300", "XLRX", "66", "chat")


@pytest.fixture
def stores(database):
    return MemoryStore(database), LessonStore(database), ChannelLog(database)


# ── what it knows about you ──────────────────────────────────────────────────

def test_it_shows_what_you_taught_and_what_it_knows_about_you(stores):
    memories, lessons, log = stores
    taught = memories.remember("the csc340 proposal is due tuesday", "csc340", "claim", MINT, KMUTT).memory_id
    about = memories.remember("Mint is allergic to peanuts", "Mint", "claim", SLADE, KMUTT).memory_id
    memories.remember("Slade is vegetarian", "Slade", "self", SLADE, KMUTT)
    lessons.add("stop roasting Mint", "person", MINT, KMUTT)
    log.add(_message("hi", MINT))
    text = about_me("222", "Mint")
    assert f"m{taught}" in text and "the csc340 proposal is due tuesday" in text
    assert f"m{about}" in text and "Mint is allergic to peanuts" in text and "Slade told me" in text
    assert "Slade is vegetarian" not in text
    assert "stop roasting Mint" in text
    assert "1 message" in text


def test_it_says_so_when_it_knows_nothing(stores):
    assert "nothing" in about_me("222", "Mint")


def test_a_corrected_memory_is_not_shown(stores):
    memories, _, _ = stores
    old = memories.remember("proposal due monday", "csc340", "claim", MINT, KMUTT).memory_id
    memories.remember("proposal due tuesday", "csc340", "claim", MINT, KMUTT, replaces=old)
    text = about_me("222", "Mint")
    assert "proposal due tuesday" in text and "proposal due monday" not in text


def test_a_long_list_fits_one_discord_message(stores):
    memories, _, _ = stores
    for n in range(80):
        memories.remember(f"club fact number {n} " + "x" * 60, "club", "claim", MINT, KMUTT)
    text = about_me("222", "Mint")
    assert len(text) <= 2000
    assert "more" in text


# ── what it learned in a server ──────────────────────────────────────────────

def test_admins_see_what_was_taught_in_their_server_only(stores):
    memories, lessons, log = stores
    memories.remember("the exam is in room 410", "exam", "claim", KRUSKAL, KMUTT)
    memories.remember("xlrx meets on sunday", "xlrx", "claim", MINT, XLRX)
    lessons.add("keep answers short", "server", SLADE, KMUTT)
    log.save_episode(KMUTT, TODAY - timedelta(days=1), "- they planned the proposal", ["760"])
    text = about_server("1280")
    assert "the exam is in room 410" in text and "Kruskal" in text
    assert "xlrx meets on sunday" not in text
    assert "keep answers short" in text
    assert "they planned the proposal" in text and "d1" in text


# ── deleting one thing ───────────────────────────────────────────────────────

def test_you_can_delete_a_memory_you_taught(stores):
    memories, _, _ = stores
    memory_id = memories.remember("the exam is in room 410", "exam", "claim", MINT, KMUTT).memory_id
    assert "deleted" in delete_item(f"m{memory_id}", "222", "1300", is_admin=False)
    assert memories.recall("exam room") == []


def test_you_can_delete_a_memory_about_yourself_from_someone_else(stores):
    memories, _, log = stores
    log.add(_message("hi", MINT))  # so the bot knows which user Mint is
    memory_id = memories.remember("Mint is allergic to peanuts", "Mint", "claim", SLADE, KMUTT).memory_id
    assert "deleted" in delete_item(f"m{memory_id}", "222", "", is_admin=False, username="Mint")


def test_you_cannot_delete_someone_elses_memory(stores):
    memories, _, _ = stores
    memory_id = memories.remember("the exam is in room 410", "exam", "claim", KRUSKAL, KMUTT).memory_id
    assert "cant" in delete_item(f"m{memory_id}", "222", "1280", is_admin=False)
    assert memories.recall("exam room")


def test_an_admin_can_delete_what_was_taught_in_their_server(stores):
    memories, _, _ = stores
    memory_id = memories.remember("the exam is in room 410", "exam", "claim", KRUSKAL, KMUTT).memory_id
    assert "deleted" in delete_item(f"m{memory_id}", "222", "1280", is_admin=True)


def test_an_admin_cannot_delete_what_was_taught_in_another_server(stores):
    memories, _, _ = stores
    memory_id = memories.remember("xlrx meets on sunday", "xlrx", "claim", MINT, XLRX).memory_id
    assert "cant" in delete_item(f"m{memory_id}", "760", "1280", is_admin=True)


def test_you_can_delete_a_lesson_about_you(stores):
    _, lessons, _ = stores
    lessons.add("stop roasting Mint", "person", MINT, KMUTT)
    (lesson,) = lessons.for_reply("222", "").person
    assert "deleted" in delete_item(f"l{lesson.id}", "222", "", is_admin=False)
    assert lessons.for_reply("222", "").person == []


def test_an_admin_can_delete_a_server_lesson(stores):
    _, lessons, _ = stores
    lessons.add("keep answers short", "server", SLADE, KMUTT)
    (lesson,) = lessons.for_reply("", "1280").server
    assert "cant" in delete_item(f"l{lesson.id}", "222", "1280", is_admin=False)
    assert "deleted" in delete_item(f"l{lesson.id}", "222", "1280", is_admin=True)


def test_someone_in_a_day_or_an_admin_can_delete_its_summary(stores):
    _, _, log = stores
    first = log.save_episode(KMUTT, TODAY - timedelta(days=1), "- they planned", ["760"])
    second = log.save_episode(KMUTT, TODAY - timedelta(days=2), "- they gamed", ["760"])
    assert "cant" in delete_item(f"d{first}", "222", "1280", is_admin=False)
    assert "deleted" in delete_item(f"d{first}", "760", "", is_admin=False)
    assert "deleted" in delete_item(f"d{second}", "222", "1280", is_admin=True)
    assert log.episode_count() == 0


def test_deleting_moves_the_revisions_so_no_cached_reply_uses_it(stores):
    memories, lessons, _ = stores
    memory_id = memories.remember("the exam is in room 410", "exam", "claim", MINT, KMUTT).memory_id
    before = memories.revision()
    delete_item(f"m{memory_id}", "222", "", is_admin=False)
    assert memories.revision() > before


@pytest.mark.parametrize("code", ["", "x12", "m", "m-1", "m999"])
def test_a_bad_or_unknown_number_says_so(stores, code):
    assert "no " in delete_item(code, "222", "1280", is_admin=True) or "number" in delete_item(code, "222", "1280", is_admin=True)


# ── the command ──────────────────────────────────────────────────────────────

@pytest.fixture
async def memory_command(mock_bot_user, database):
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
    await manager._register_memory_command()
    return registered["memory"]


def _interaction(user=MINT, guild_id=1280, admin=False):
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=int(user.user_id), display_name=user.name,
                                       guild_permissions=SimpleNamespace(manage_guild=admin))
    interaction.guild_id = guild_id
    interaction.guild = SimpleNamespace(id=guild_id) if guild_id else None
    interaction.response.send_message = AsyncMock()
    return interaction


async def test_slash_memory_answers_privately(memory_command, database):
    MemoryStore(database).remember("the exam is in room 410", "exam", "claim", MINT, KMUTT)
    interaction = _interaction()
    await memory_command(interaction)
    args, kwargs = interaction.response.send_message.call_args
    assert "the exam is in room 410" in args[0] and kwargs["ephemeral"] is True


async def test_slash_memory_for_the_server_needs_manage_server(memory_command, database):
    interaction = _interaction(admin=False)
    await memory_command(interaction, view="server")
    assert "manage server" in interaction.response.send_message.call_args[0][0]


async def test_slash_memory_delete_uses_the_permission_of_the_person(memory_command, database):
    memory_id = MemoryStore(database).remember("the exam is in room 410", "exam", "claim", KRUSKAL, KMUTT).memory_id
    interaction = _interaction(admin=True)
    await memory_command(interaction, delete=f"m{memory_id}")
    assert "deleted" in interaction.response.send_message.call_args[0][0]


def test_deleting_a_memory_others_were_merged_into_brings_theirs_back(stores):
    from tests.unit.test_memory_sleep import _vector
    memories, _, _ = stores
    kept = memories.remember("the exam is in room 410", "exam", "claim", KRUSKAL, KMUTT).memory_id
    merged = memories.remember("exam: room 410", "exam", "claim", MINT, KMUTT).memory_id
    for memory_id in (kept, merged):
        memories.set_embedding(memory_id, _vector("exam room"), "fake-1")
    assert memories.merge(memories.get(kept), memories.get(merged))
    delete_item(f"m{kept}", "111", "", is_admin=False)
    assert [m.id for m in memories.recall("exam room")] == [merged]
