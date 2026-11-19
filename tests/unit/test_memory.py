"""Layer 1 of the memory plan: remember what people teach, with who said it, when and where."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.memory_store import MemoryStore, Place, Speaker
from src.tools.base import Turn
from src.tools.memory import RecallMemory, RememberMemory

SLADE = Speaker("760", "Slade")
KRUSKAL = Speaker("111", "Kruskal")
MINT = Speaker("222", "Mint")
KMUTT = Place("1280", "cs-kmutt", "55", "general")
XLRX = Place("1300", "XLRX", "66", "chat")


@pytest.fixture
def store(database):
    return MemoryStore(database)


def test_a_memory_keeps_who_said_it_when_and_where(store):
    saved = store.remember("the csc340 proposal is due friday 4pm", "csc340", "claim", KRUSKAL, KMUTT)
    found = store.recall("when is the csc340 proposal due")
    assert [m.id for m in found] == [saved.memory_id]
    memory = found[0]
    assert (memory.source_name, memory.guild_name, memory.channel_name) == ("Kruskal", "cs-kmutt", "general")
    assert datetime.now(timezone.utc) - memory.created_at < timedelta(minutes=1)


def test_memories_are_shared_across_servers(store):
    store.remember("slade is vegetarian", "Slade", "self", SLADE, KMUTT)
    assert store.recall("what does slade eat, is he vegetarian")


def test_a_correction_by_the_same_person_replaces_the_old_memory(store):
    old = store.remember("the proposal is due friday", "csc340", "claim", KRUSKAL, KMUTT)
    new = store.remember("the proposal is due monday", "csc340", "claim", KRUSKAL, KMUTT, replaces=old.memory_id)
    assert new.replaced == old.memory_id
    assert [m.content for m in store.recall("proposal due")] == ["the proposal is due monday"]


def test_only_the_person_can_change_what_they_said_about_themselves(store):
    mine = store.remember("i am vegetarian", "Slade", "self", SLADE, KMUTT)
    attempt = store.remember("slade eats meat", "Slade", "claim", MINT, XLRX, replaces=mine.memory_id)
    assert attempt.replaced is None
    assert "only Slade" in attempt.note
    assert {m.content for m in store.recall("slade vegetarian meat")} == {"i am vegetarian", "slade eats meat"}


def test_one_person_cannot_overwrite_what_others_confirmed(store):
    claim = store.remember("the exam is in room 410", "exam", "claim", KRUSKAL, KMUTT)
    store.remember("the exam is in room 410", "exam", "claim", SLADE, KMUTT)  # confirms it
    attempt = store.remember("the exam is in room 302", "exam", "claim", MINT, KMUTT, replaces=claim.memory_id)
    assert attempt.replaced is None
    assert "confirmed" in attempt.note
    assert len(store.recall("exam room")) == 2


def test_an_unconfirmed_claim_can_be_corrected_by_someone_else(store):
    claim = store.remember("the exam is in room 410", "exam", "claim", KRUSKAL, KMUTT)
    fix = store.remember("the exam is in room 302", "exam", "claim", MINT, KMUTT, replaces=claim.memory_id)
    assert fix.replaced == claim.memory_id
    assert [m.content for m in store.recall("exam room")] == ["the exam is in room 302"]


def test_the_same_thing_from_someone_else_counts_as_a_confirmation(store):
    first = store.remember("The exam is in room 410", "exam", "claim", KRUSKAL, KMUTT)
    again = store.remember("the exam is in room 410 ", "exam", "claim", SLADE, XLRX)
    assert again.memory_id == first.memory_id and "confirmed" in again.note
    assert store.recall("exam room")[0].confirmed_by == ["Slade"]


def test_replacing_a_memory_that_does_not_exist_saves_a_new_one(store):
    saved = store.remember("go is fast", "go", "claim", KRUSKAL, KMUTT, replaces=999)
    assert saved.replaced is None and saved.memory_id
    assert "no memory #999" in saved.note


def test_recall_ranks_by_matching_words_then_newest(store):
    store.remember("slade likes rust", "Slade", "self", SLADE, KMUTT)
    store.remember("slade likes go and rust a lot", "Slade", "self", SLADE, KMUTT)
    store.remember("mint likes python", "Mint", "self", MINT, KMUTT)
    found = [m.content for m in store.recall("does slade like rust or go")]
    assert found[:2] == ["slade likes go and rust a lot", "slade likes rust"]
    assert "mint likes python" not in found


def test_recall_returns_at_most_ten(store):
    for n in range(14):
        store.remember(f"fact number {n} about the club", "club", "claim", KRUSKAL, KMUTT)
    assert len(store.recall("club fact")) == 10


def test_forget_removes_everything_a_person_taught(store):
    store.remember("i am vegetarian", "Slade", "self", SLADE, KMUTT)
    store.remember("the exam is in room 410", "exam", "claim", KRUSKAL, KMUTT)
    store.forget_user("760")
    assert [m.content for m in store.recall("vegetarian exam room")] == ["the exam is in room 410"]


def test_slash_forget_also_forgets_memories(database):
    from src.user_data_manager import UserDataManager
    store = MemoryStore(database)
    store.remember("i am vegetarian", "Slade", "self", SLADE, KMUTT)
    UserDataManager().forget_user("760")
    assert store.recall("vegetarian") == []


# ── the tools ────────────────────────────────────────────────────────────────

def _turn(user=SLADE, place=KMUTT):
    turn = Turn("q", user.name, "", "", [], user_id=user.user_id)
    turn.place = place
    return turn


async def test_remember_saves_under_the_person_and_place_of_the_reply(store):
    result = await RememberMemory(store).run({"content": "slade has a korean exam on friday", "about": "Slade",
                                              "kind": "self"}, _turn())
    assert "#" in result.text
    memory = store.recall("korean exam")[0]
    assert (memory.source_name, memory.guild_name) == ("Slade", "cs-kmutt")


async def test_remember_with_no_person_saves_nothing(store):
    turn = Turn("q", "", "", "", [])
    result = await RememberMemory(store).run({"content": "x", "about": "y", "kind": "claim"}, turn)
    assert result.text.startswith("error:")
    assert store.recall("x") == []


async def test_recall_says_who_told_it_when_and_where(store):
    store.remember("slade has the flu", "Slade", "self", SLADE, KMUTT)
    result = await RecallMemory(store).run({"query": "is slade sick, flu"}, _turn(user=MINT, place=XLRX))
    line = next(line for line in result.text.splitlines() if "flu" in line)
    assert "Slade told me" in line and "today" in line and "cs-kmutt #general" in line
    assert line.lstrip().startswith("#")


async def test_recall_with_nothing_found_says_so(store):
    result = await RecallMemory(store).run({"query": "pizza"}, _turn())
    assert "nothing" in result.text.lower()


def test_the_memory_tools_are_offered_with_their_rules():
    from src.tools import default_tools
    tools = {tool.name: tool for tool in default_tools()}
    assert {"remember", "recall"} <= set(tools)
    rules = tools["remember"].prompt() + tools["recall"].prompt()
    assert "web_search" not in rules
    assert "who told u" in rules and "replaces" in rules and "check it on the web" in rules
    # It must not name web_search: without a search key that tool is not offered


# ── through a real reply ─────────────────────────────────────────────────────

def _message(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _call(name, arguments):
    return SimpleNamespace(id="call_1", type="function",
                           function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


async def test_a_reply_saves_a_memory_with_the_server_and_channel(database, mock_openai_client):
    from src.ai_response_handler import AIResponseHandler
    with patch("src.ai_provider.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        handler = AIResponseHandler(endpoint="https://fake.openai.azure.com/openai/v1",
                                    api_key="fake_key", deployment="gpt-5-mini")
    handler.ai_client = mock_openai_client
    mock_openai_client.chat.completions.create.side_effect = [
        _message(tool_calls=[_call("remember", {"content": "the exam is in room 410", "about": "exam",
                                                "kind": "claim"})]),
        _message(content="got it, room 410"),
    ]
    reply = await handler.generate_reply("/remember the exam is in room 410", "", "Kruskal", "111",
                                         allow_files=True, session_key="55:111", place=KMUTT)
    assert reply.text == "got it, room 410\n-# saved as m1"
    memory = MemoryStore(database).recall("exam room")[0]
    assert (memory.source_name, memory.guild_name, memory.channel_name) == ("Kruskal", "cs-kmutt", "general")


def test_a_place_is_read_from_a_discord_channel():
    from src.memory_store import place_of
    channel = SimpleNamespace(id=55, name="general", guild=SimpleNamespace(id=1280, name="cs-kmutt"))
    assert place_of(channel) == Place("1280", "cs-kmutt", "55", "general")
    dm = SimpleNamespace(id=9, guild=None)
    assert place_of(dm) == Place("", "", "9", "")


async def test_a_message_reply_passes_where_it_was_said(mock_bot_user):
    # The real message path hands generate_reply the server and channel
    import inspect
    from src import message_processor, slash_commands
    for module in (message_processor, slash_commands):
        assert "place=place_of(" in inspect.getsource(module), module.__name__


def _handler(mock_openai_client):
    from src.ai_response_handler import AIResponseHandler
    with patch("src.ai_provider.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        handler = AIResponseHandler(endpoint="https://fake.openai.azure.com/openai/v1",
                                    api_key="fake_key", deployment="gpt-5-mini")
    handler.ai_client = mock_openai_client
    return handler


async def test_matching_memories_reach_the_reply_without_a_tool_call(database, mock_openai_client):
    # Live, flash-lite never called recall for "when is the csc340 proposal due" and made up a syllabus
    MemoryStore(database).remember("the csc340 proposal is due tuesday 4pm", "csc340", "claim", KRUSKAL, KMUTT)
    mock_openai_client.chat.completions.create.side_effect = [_message(content="tuesday 4pm, kruskal told me")]
    handler = _handler(mock_openai_client)
    await handler.generate_reply("/when is the csc340 proposal due", "", "Mint", "222",
                                 allow_files=True, session_key="66:222", place=XLRX)
    sent = mock_openai_client.chat.completions.create.call_args[1]["messages"]
    joined = "\n".join(str(m["content"]) for m in sent)
    assert "Kruskal told me" in joined and "the csc340 proposal is due tuesday 4pm" in joined
    assert "cs-kmutt #general" in joined


async def test_no_memory_block_when_nothing_matches(database, mock_openai_client):
    MemoryStore(database).remember("slade is allergic to peanuts", "Slade", "self", SLADE, KMUTT)
    mock_openai_client.chat.completions.create.side_effect = [_message(content="yo")]
    handler = _handler(mock_openai_client)
    await handler.generate_reply("/hows the weather", "", "Mint", "222", allow_files=True, place=XLRX)
    joined = "\n".join(str(m["content"]) for m in mock_openai_client.chat.completions.create.call_args[1]["messages"])
    assert "=== MEMORIES" not in joined


# ── review on #54 ────────────────────────────────────────────────────────────

def test_two_people_with_the_same_name_keep_separate_self_reports(store):
    alex_one, alex_two = Speaker("1", "Alex"), Speaker("2", "Alex")
    first = store.remember("i am vegetarian", "Alex", "self", alex_one, KMUTT)
    second = store.remember("i am vegetarian", "Alex", "self", alex_two, XLRX)
    assert second.memory_id != first.memory_id


def test_a_correction_to_text_that_already_exists_still_replaces(store):
    friday = store.remember("the proposal is due friday", "csc340", "claim", KRUSKAL, KMUTT)
    monday = store.remember("the proposal is due monday", "csc340", "claim", SLADE, KMUTT)
    fix = store.remember("the proposal is due monday", "csc340", "claim", KRUSKAL, KMUTT, replaces=friday.memory_id)
    assert fix.replaced == friday.memory_id and fix.memory_id == monday.memory_id
    assert [m.content for m in store.recall("proposal due")] == ["the proposal is due monday"]


def test_the_owner_can_correct_a_self_report_someone_confirmed(store):
    mine = store.remember("i am vegetarian", "Slade", "self", SLADE, KMUTT)
    store.remember("i am vegetarian", "Slade", "claim", MINT, KMUTT)
    fix = store.remember("i eat fish now", "Slade", "self", SLADE, KMUTT, replaces=mine.memory_id)
    assert fix.replaced == mine.memory_id


def test_the_confirmer_alone_cannot_overwrite_a_confirmed_claim(store):
    claim = store.remember("the exam is in room 410", "exam", "claim", KRUSKAL, KMUTT)
    store.remember("the exam is in room 410", "exam", "claim", SLADE, KMUTT)
    attempt = store.remember("the exam is in room 302", "exam", "claim", SLADE, KMUTT, replaces=claim.memory_id)
    assert attempt.replaced is None


def test_forget_also_removes_their_confirmations(store):
    store.remember("the exam is in room 410", "exam", "claim", KRUSKAL, KMUTT)
    store.remember("the exam is in room 410", "exam", "claim", SLADE, KMUTT)
    store.forget_user("760")
    assert store.recall("exam room")[0].confirmed_by == []


def test_a_memory_from_a_reply_that_started_before_forget_is_refused(store):
    started = datetime.now(timezone.utc) - timedelta(seconds=5)
    store.forget_user("760")
    saved = store.remember("i am vegetarian", "Slade", "self", SLADE, KMUTT, started_at=started)
    assert saved.memory_id is None and "forget" in saved.note
    assert store.recall("vegetarian") == []
    later = store.remember("i am vegetarian", "Slade", "self", SLADE, KMUTT, started_at=datetime.now(timezone.utc))
    assert later.memory_id


def test_every_memory_change_moves_the_revision(store):
    seen = {store.revision()}
    saved = store.remember("the exam is in room 410", "exam", "claim", KRUSKAL, KMUTT)
    seen.add(store.revision())
    store.remember("the exam is in room 410", "exam", "claim", SLADE, KMUTT)
    seen.add(store.revision())
    store.remember("room 302", "exam", "claim", MINT, KMUTT, replaces=saved.memory_id)
    seen.add(store.revision())
    store.forget_user("111")
    seen.add(store.revision())
    assert len(seen) == 5


async def test_a_cached_reply_is_not_reused_after_a_memory_changes(database, mock_openai_client):
    mock_openai_client.chat.completions.create.side_effect = [_message(content="friday"), _message(content="monday")]
    handler = _handler(mock_openai_client)
    ask = dict(allow_files=True, session_key="66:222", place=XLRX)
    assert (await handler.generate_reply("/when is the csc340 proposal due", "", "Mint", "222", **ask)).text == "friday"
    MemoryStore(database).remember("the csc340 proposal is due monday", "csc340", "claim", KRUSKAL, KMUTT)
    assert (await handler.generate_reply("/when is the csc340 proposal due", "", "Mint", "222", **ask)).text == "monday"


# ── review on #56 ────────────────────────────────────────────────────────────

def test_a_report_about_yourself_is_kept_as_yours_when_someone_said_it_first(store):
    store.remember("Slade is vegetarian", "Slade", "claim", MINT, KMUTT)
    store.remember("Slade is vegetarian", "Slade", "self", SLADE, KMUTT)
    (memory,) = store.recall("slade vegetarian")
    assert memory.kind == "self" and memory.source_name == "Slade" and memory.confirmed_by == ["Mint"]
    fix = store.remember("Slade eats fish now", "Slade", "self", SLADE, KMUTT, replaces=memory.id)
    assert fix.replaced == memory.id
