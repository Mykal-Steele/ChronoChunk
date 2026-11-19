"""A reply that saved something ends with a small line saying so, so a "noted" without a save shows."""
import json
from types import SimpleNamespace

from src.lessons import LessonStore
from src.memory_store import MemoryStore, Place, Speaker
from tests.unit.test_memory_meaning import FakeEmbedder
from tests.unit.test_memory_sleep import _handler, _reply

MINT = Speaker("222", "Mint")
SLADE = Speaker("760", "Slade")
KMUTT = Place("1280", "cs-kmutt", "55", "general")


def _call(name, arguments, call_id="c1"):
    return SimpleNamespace(id=call_id, type="function",
                           function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def _remember(content, about="Mint", kind="self", **extra):
    return _call("remember", {"content": content, "about": about, "kind": kind, **extra})


async def _ask(mock_openai_client, *replies, text="/note this", user=MINT):
    mock_openai_client.chat.completions.create.side_effect = list(replies)
    handler = _handler(mock_openai_client, FakeEmbedder())
    return await handler.generate_reply(text, "", user.name, user.user_id, allow_files=True, place=KMUTT)


async def test_a_saved_note_ends_with_its_code(database, mock_openai_client):
    reply = await _ask(mock_openai_client,
                       _reply(tool_calls=[_remember("Mint's korean presentation is at https://docs.google.com/x")]),
                       _reply("gotchu, noted"))
    (memory,) = MemoryStore(database).recall("korean presentation")
    assert reply.text == f"gotchu, noted\n-# saved as m{memory.id}"


async def test_no_receipt_when_nothing_was_saved(database, mock_openai_client):
    # The model said "noted" and never called remember: no line, so the person can tell
    reply = await _ask(mock_openai_client, _reply("gotchu, noted"))
    assert reply.text == "gotchu, noted"


async def test_a_correction_says_what_it_replaced(database, mock_openai_client):
    old = MemoryStore(database).remember("Mint's slides are at https://a", "Mint", "self", MINT, KMUTT).memory_id
    reply = await _ask(mock_openai_client,
                       _reply(tool_calls=[_remember("Mint's slides are at https://b", replaces=old)]), _reply("updated"))
    assert reply.text.endswith(f"-# saved as m{old + 1}, replacing m{old}")


async def test_something_already_known_says_so(database, mock_openai_client):
    known = MemoryStore(database).remember("Mint's slides are at https://a", "Mint", "self", MINT, KMUTT).memory_id
    reply = await _ask(mock_openai_client, _reply(tool_calls=[_remember("Mint's slides are at https://a")]), _reply("yep"))
    assert reply.text.endswith(f"-# already had it as m{known}")


async def test_two_saves_in_one_reply_are_both_listed(database, mock_openai_client):
    reply = await _ask(mock_openai_client,
                       _reply(tool_calls=[_remember("Mint's slides are at https://a"),
                                          _call("remember", {"content": "the demo is friday", "about": "demo", "kind": "claim"}, "c2")]),
                       _reply("both noted"))
    assert reply.text.endswith("-# saved as m1, saved as m2")


async def test_a_learned_lesson_ends_with_its_code(database, mock_openai_client):
    reply = await _ask(mock_openai_client,
                       _reply(tool_calls=[_call("learn_lesson", {"lesson": "stop roasting Mint", "scope": "me"})]),
                       _reply("my bad"))
    (lesson,) = LessonStore(database).for_reply("222", "1280").person
    assert reply.text == f"my bad\n-# learned as l{lesson.id}"


async def test_a_refused_save_has_no_receipt(database, mock_openai_client):
    mine = MemoryStore(database).remember("Slade is vegetarian", "Slade", "self", SLADE, KMUTT).memory_id
    reply = await _ask(mock_openai_client,
                       _reply(tool_calls=[_remember("Slade eats meat", about="Slade", kind="claim", replaces=mine)]),
                       _reply("kept both"))
    assert "replacing" not in reply.text


# ── public or private ────────────────────────────────────────────────────────

LINK = "Mint's korean presentation is at https://docs.google.com/x"


def test_the_bot_is_told_to_ask_public_or_private_before_saving_a_note():
    from src.tools.memory import MEMORY_PROMPT, RememberMemory
    assert "public or private" in MEMORY_PROMPT
    assert RememberMemory.definition["function"]["parameters"]["properties"]["private"]["type"] == "boolean"


def test_a_private_note_comes_back_only_for_its_owner(database):
    store = MemoryStore(database)
    store.remember(LINK, "Mint", "self", MINT, KMUTT, private=True)
    assert store.recall("korean presentation link", viewer_id="222")
    assert store.recall("korean presentation link", viewer_id="760") == []
    assert store.recall("korean presentation link") == []


def test_a_public_note_comes_back_for_anyone(database):
    store = MemoryStore(database)
    store.remember(LINK, "Mint", "self", MINT, KMUTT)
    assert store.recall("korean presentation link", viewer_id="760")


async def test_a_private_save_says_so_in_the_receipt(database, mock_openai_client):
    reply = await _ask(mock_openai_client, _reply(tool_calls=[_remember(LINK, private=True)]), _reply("saved, only for u"))
    assert reply.text.endswith("-# saved as m1 (private)")


async def test_someone_else_never_gets_a_private_note_in_a_reply(database, mock_openai_client):
    MemoryStore(database).remember(LINK, "Mint", "self", MINT, KMUTT, private=True)
    await _ask(mock_openai_client, _reply("no clue"), text="/whats mint's korean presentation link", user=SLADE)
    sent = "\n".join(str(m["content"]) for m in mock_openai_client.chat.completions.create.call_args[1]["messages"])
    assert "docs.google.com/x" not in sent


async def test_the_owner_gets_their_private_note_in_any_server(database, mock_openai_client):
    MemoryStore(database).remember(LINK, "Mint", "self", MINT, KMUTT, private=True)
    mock_openai_client.chat.completions.create.side_effect = [_reply("here")]
    handler = _handler(mock_openai_client, FakeEmbedder())
    await handler.generate_reply("/whats my korean presentation link", "", "Mint", "222", allow_files=True,
                                 place=Place("1300", "XLRX", "66", "chat"))
    sent = "\n".join(str(m["content"]) for m in mock_openai_client.chat.completions.create.call_args[1]["messages"])
    assert "docs.google.com/x" in sent and "private" in sent


async def test_the_recall_tool_hides_other_peoples_private_notes(database):
    from src.tools.base import Turn
    from src.tools.memory import RecallMemory
    MemoryStore(database).remember(LINK, "Mint", "self", MINT, KMUTT, private=True)
    result = await RecallMemory(MemoryStore(database)).run({"query": "korean presentation"}, Turn("q", "Slade", "", "", [], user_id="760"))
    assert "docs.google.com" not in result.text


def test_a_private_note_never_confirms_or_merges_into_someone_elses(database):
    store = MemoryStore(database)
    public = store.remember("the demo is friday", "demo", "claim", SLADE, KMUTT).memory_id
    mine = store.remember("the demo is friday", "demo", "claim", MINT, KMUTT, private=True)
    assert mine.memory_id != public
    assert store.get(public).confirmed_by == []


def test_a_public_save_does_not_match_someone_elses_private_note(database):
    store = MemoryStore(database)
    private = store.remember("the demo is friday", "demo", "claim", MINT, KMUTT, private=True).memory_id
    public = store.remember("the demo is friday", "demo", "claim", SLADE, KMUTT).memory_id
    assert public != private


def test_private_notes_are_never_paired_at_night_with_anyone_elses(database):
    from tests.unit.test_memory_meaning import _vector
    store = MemoryStore(database)
    for speaker, private in ((MINT, True), (SLADE, False)):
        memory_id = store.remember(f"the demo is friday {speaker.name}", "demo", "claim", speaker, KMUTT, private=private).memory_id
        store.set_embedding(memory_id, _vector("exam room"), "fake-1")
    assert store.pairs_to_tidy("fake-1") == []


def test_memory_shows_your_private_notes_to_you_and_hides_them_from_the_server_view(database):
    from src.memory_control import about_me, about_server, delete_item
    memory_id = MemoryStore(database).remember(LINK, "Mint", "self", MINT, KMUTT, private=True).memory_id
    assert "docs.google.com/x" in about_me("222", "Mint") and "(private)" in about_me("222", "Mint")
    assert "docs.google.com/x" not in about_server("1280")
    assert "cant" in delete_item(f"m{memory_id}", "760", "1280", is_admin=True)
