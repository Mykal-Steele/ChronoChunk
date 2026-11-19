"""Layer 3 of the memory plan: every channel message is kept for a while, each day becomes a summary, and memory is tidied at night."""
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.channel_log import ChannelLog
from src.clock import BANGKOK
from src.memory_store import MemoryStore, Place, Speaker
from src.tools.base import Turn
from src.tools.memory import RememberMemory
from tests.unit.test_memory_meaning import FakeEmbedder, _vector

SLADE = Speaker("760", "Slade")
KRUSKAL = Speaker("111", "Kruskal")
MINT = Speaker("222", "Mint")
KMUTT = Place("1280", "cs-kmutt", "55", "general")
TODAY = date(2026, 11, 19)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    """A time on a Bangkok day, in UTC like Discord gives it."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=BANGKOK).astimezone(timezone.utc)


_ids = iter(range(1000, 100000))


def _message(text, author=SLADE, when=None, guild=("1280", "cs-kmutt"), channel=("55", "general"), bot=False, message_id=None):
    return SimpleNamespace(
        id=message_id or next(_ids), content=text, created_at=when or _at(TODAY, 12),
        author=SimpleNamespace(id=int(author.user_id), display_name=author.name, bot=bot),
        channel=SimpleNamespace(id=int(channel[0]), name=channel[1],
                                guild=SimpleNamespace(id=int(guild[0]), name=guild[1]) if guild else None),
    )


@pytest.fixture
def log(database):
    return ChannelLog(database)


@pytest.fixture
def store(database):
    return MemoryStore(database)


def _chat_day(log, day=TODAY - timedelta(days=1), count=6, channel=("55", "general")):
    people = [SLADE, KRUSKAL, MINT]
    for n in range(count):
        log.add(_message(f"message {n} about the csc340 proposal", people[n % 3], _at(day, 10, n), channel=channel))


# ── the channel log ──────────────────────────────────────────────────────────

def test_every_server_message_is_kept_with_who_where_and_when(log):
    log.add(_message("ugh i got the flu", SLADE, _at(TODAY, 9, 30)))
    (row,) = log.messages_on("55", TODAY)
    assert (row["author_name"], row["content"], row["guild_name"], row["channel_name"]) == ("Slade", "ugh i got the flu", "cs-kmutt", "general")


def test_a_dm_is_not_kept(log):
    assert not log.add(_message("secret", guild=None))


def test_an_empty_message_is_not_kept(log):
    assert not log.add(_message("   "))


def test_the_bots_own_messages_are_kept_and_marked(log):
    log.add(_message("get well soon", Speaker("1342", "ChronoChunk"), bot=True), is_bot=True)
    assert log.messages_on("55", TODAY)[0]["is_bot"] == 1


def test_a_message_belongs_to_its_bangkok_day(log):
    # 23:30 on the 18th in Bangkok is 16:30 UTC on the 18th, and 00:30 on the 19th is 17:30 UTC on the 18th
    log.add(_message("late", when=_at(TODAY - timedelta(days=1), 23, 30)))
    log.add(_message("early", when=_at(TODAY, 0, 30)))
    assert [r["content"] for r in log.messages_on("55", TODAY)] == ["early"]


def test_an_edit_changes_the_kept_text(log):
    log.add(_message("exam is in room 410", message_id=5))
    log.edit(5, "exam is in room 302")
    assert log.messages_on("55", TODAY)[0]["content"] == "exam is in room 302"


def test_a_deleted_message_is_deleted_from_the_log(log):
    log.add(_message("oops wrong channel", message_id=6))
    log.delete(6)
    assert log.messages_on("55", TODAY) == []


def test_the_same_message_twice_is_kept_once(log):
    log.add(_message("hi", message_id=7))
    log.add(_message("hi", message_id=7))
    assert len(log.messages_on("55", TODAY)) == 1


def test_old_messages_are_deleted_after_the_keep_window(log):
    log.add(_message("old", when=_at(TODAY - timedelta(days=31), 12)))
    log.add(_message("recent", when=_at(TODAY - timedelta(days=29), 12)))
    assert log.prune(TODAY, keep_days=30) == 1
    assert log.messages_on("55", TODAY - timedelta(days=29))


def test_forget_deletes_their_messages_and_the_summaries_they_are_in(log):
    _chat_day(log)
    yesterday = TODAY - timedelta(days=1)
    log.save_episode(KMUTT, yesterday, "slade and kruskal planned the proposal", ["760", "111"])
    log.add(_message("hi", MINT))
    log.forget_user("760")
    assert all(r["author_id"] != "760" for r in log.messages_on("55", yesterday))
    assert log.episode_count() == 0


def test_a_day_with_enough_talk_and_no_summary_is_due(log):
    _chat_day(log)
    _chat_day(log, count=2, channel=("56", "memes"))
    _chat_day(log, day=TODAY)  # today is not over yet
    assert log.days_to_summarize(TODAY) == [("55", TODAY - timedelta(days=1))]


def test_a_day_that_has_a_summary_is_not_due_again(log):
    _chat_day(log)
    log.save_episode(KMUTT, TODAY - timedelta(days=1), "they planned the proposal", ["760"])
    assert log.days_to_summarize(TODAY) == []


# ── a day's summary ──────────────────────────────────────────────────────────

def _reply(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None, model="gemini-3.1-flash-lite")


def _client(*replies):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=list(replies))
    return client


async def test_a_day_is_summarized_with_names_and_times(log):
    from src.sleep import summarize_day
    yesterday = TODAY - timedelta(days=1)
    _chat_day(log)
    client = _client(_reply("- Slade, Kruskal and Mint talked about the csc340 proposal"))
    episode_id = await summarize_day(log, client, "gemini-3.1-flash-lite", FakeEmbedder(), "55", yesterday)
    sent = client.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert "10:00 Slade: message 0 about the csc340 proposal" in sent
    assert "Wednesday 18 Nov" in sent
    (episode,) = log.find_episodes(_vector("csc340 proposal"), "fake-1", "1280")
    assert episode.id == episode_id and "csc340 proposal" in episode.summary
    assert episode.channel_name == "general" and episode.day == yesterday


async def test_a_summary_does_not_include_the_bots_own_lines_as_facts(log):
    from src.sleep import summarize_day
    yesterday = TODAY - timedelta(days=1)
    _chat_day(log)
    log.add(_message("trust me the exam is cancelled", Speaker("1342", "ChronoChunk"), _at(yesterday, 11), bot=True), is_bot=True)
    client = _client(_reply("- they talked about the proposal"))
    await summarize_day(log, client, "m", FakeEmbedder(), "55", yesterday)
    sent = client.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert "11:00 ChronoChunk (the bot): trust me the exam is cancelled" in sent


async def test_an_empty_summary_is_not_saved_and_the_day_stays_due(log):
    from src.sleep import summarize_day
    _chat_day(log)
    await summarize_day(log, _client(_reply("")), "m", FakeEmbedder(), "55", TODAY - timedelta(days=1))
    assert log.days_to_summarize(TODAY) == [("55", TODAY - timedelta(days=1))]


async def test_a_summary_still_saves_when_the_embedding_fails(log):
    from src.sleep import summarize_day
    _chat_day(log)
    await summarize_day(log, _client(_reply("- proposal talk")), "m", FakeEmbedder(fail=True), "55", TODAY - timedelta(days=1))
    assert log.episode_count() == 1 and log.days_to_summarize(TODAY) == []


def test_summaries_are_only_found_in_their_own_server(log):
    log.save_episode(KMUTT, TODAY - timedelta(days=1), "slade had the flu", ["760"])
    log.set_episode_embedding(1, _vector("slade had the flu"), "fake-1")
    assert log.find_episodes(_vector("who was sick"), "fake-1", "1280")
    assert log.find_episodes(_vector("who was sick"), "fake-1", "1300") == []


def test_unrelated_summaries_are_not_found(log):
    log.save_episode(KMUTT, TODAY - timedelta(days=1), "slade had the flu", ["760"])
    log.set_episode_embedding(1, _vector("slade had the flu"), "fake-1")
    assert log.find_episodes(_vector("exam room"), "fake-1", "1280") == []


# ── tidying memory ───────────────────────────────────────────────────────────

def _teach(store, content, about, speaker, kind="claim", days_ago=0):
    saved = store.remember(content, about, kind, speaker, KMUTT)
    store.set_embedding(saved.memory_id, _vector(f"{about} {content}"), "fake-1")
    if days_ago:
        when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        with store.db.transaction() as connection:
            connection.execute("UPDATE memories SET created_at = ? WHERE id = ?", (when, saved.memory_id))
    return saved.memory_id


def _verdicts(*pairs):
    call = SimpleNamespace(id="c1", type="function", function=SimpleNamespace(
        name="judge_memory_pairs", arguments=json.dumps({"pairs": [{"pair": n, "verdict": v} for n, v in pairs]})))
    return _reply(tool_calls=[call])


async def test_the_same_thing_said_twice_becomes_one_memory_confirmed_by_both(store):
    from src.sleep import tidy_memories
    first = _teach(store, "the exam is in room 410", "exam", KRUSKAL, days_ago=2)
    second = _teach(store, "exam: room 410", "exam", MINT)
    result = await tidy_memories(store, _client(_verdicts((1, "same"))), "m", "fake-1")
    assert result["merged"] == 1
    (kept,) = store.recall("exam room", vector=_vector("exam room"), model="fake-1")
    assert kept.id == first and kept.confirmed_by == ["Mint"]
    assert store.db.query("SELECT replaced_by FROM memories WHERE id = ?", (second,))[0][0] == first


async def test_a_newer_correction_from_the_same_person_replaces_the_old_one(store):
    from src.sleep import tidy_memories
    _teach(store, "the midterm is in room 410", "exam", KRUSKAL, days_ago=3)
    newer = _teach(store, "the midterm moved to room 302", "exam", KRUSKAL)
    result = await tidy_memories(store, _client(_verdicts((1, "newer_corrects"))), "m", "fake-1")
    assert result["settled"] == 1
    assert [m.id for m in store.recall("midterm room", vector=_vector("exam room"), model="fake-1")] == [newer]


async def test_a_correction_cannot_break_the_trust_rules(store):
    # Mint cannot change what Slade said about himself, even at night
    from src.sleep import tidy_memories
    _teach(store, "Slade is vegetarian", "Slade", SLADE, kind="self", days_ago=3)
    _teach(store, "Slade eats meat now, vegetarian no more", "Slade", MINT)
    result = await tidy_memories(store, _client(_verdicts((1, "newer_corrects"))), "m", "fake-1")
    assert result["settled"] == 0
    assert len(store.recall("slade vegetarian meat", vector=_vector("vegetarian meat"), model="fake-1")) == 2


async def test_two_peoples_reports_about_themselves_are_never_merged(store):
    from src.sleep import tidy_memories
    _teach(store, "i am vegetarian", "me", SLADE, kind="self")
    _teach(store, "i am vegetarian", "me", MINT, kind="self")
    client = _client(_verdicts((1, "same")))
    result = await tidy_memories(store, client, "m", "fake-1")
    assert result == {"checked": 0, "merged": 0, "settled": 0}
    client.chat.completions.create.assert_not_called()


async def test_different_memories_are_left_alone_and_not_asked_about_again(store):
    from src.sleep import tidy_memories
    _teach(store, "the midterm is in room 410", "exam", KRUSKAL)
    _teach(store, "the final is in room 302", "exam", MINT)
    client = _client(_verdicts((1, "different")), _verdicts((1, "different")))
    assert (await tidy_memories(store, client, "m", "fake-1"))["checked"] == 1
    assert (await tidy_memories(store, client, "m", "fake-1"))["checked"] == 0
    assert client.chat.completions.create.call_count == 1


async def test_memories_about_different_things_are_not_compared(store):
    from src.sleep import tidy_memories
    _teach(store, "slade had the flu", "Slade", SLADE)
    _teach(store, "the exam is in room 410", "exam", KRUSKAL)
    client = _client()
    assert (await tidy_memories(store, client, "m", "fake-1"))["checked"] == 0
    client.chat.completions.create.assert_not_called()


async def test_a_bad_verdict_changes_nothing(store):
    from src.sleep import tidy_memories
    _teach(store, "the exam is in room 410", "exam", KRUSKAL)
    _teach(store, "exam: room 410", "exam", MINT)
    result = await tidy_memories(store, _client(_reply("no idea")), "m", "fake-1")
    assert result["merged"] == 0 and len(store.recall("exam room")) == 2


# ── fading ───────────────────────────────────────────────────────────────────

def test_a_memory_that_gets_used_stays_ahead_of_one_nobody_uses(store):
    used = _teach(store, "the midterm is in room 410", "exam", KRUSKAL, days_ago=90)
    _teach(store, "the final is in room 302", "exam", MINT, days_ago=60)
    store.mark_used([used])
    found = store.recall("exam room", vector=_vector("exam room"), model="fake-1")
    assert found[0].id == used


async def test_recalled_memories_are_marked_as_used(store):
    from src.tools.memory import RecallMemory
    memory_id = _teach(store, "the midterm is in room 410", "exam", KRUSKAL)
    await RecallMemory(store).run({"query": "midterm room"}, Turn("q", "Mint", "", "", [], user_id="222"))
    assert store.db.query("SELECT last_used_at FROM memories WHERE id = ?", (memory_id,))[0][0]


# ── who a memory is about ────────────────────────────────────────────────────

async def test_a_memory_about_the_speaker_is_kept_as_their_own_word(store):
    # Live, "ugh i got the flu" from Slade was saved as a claim, which anyone could overwrite
    turn = Turn("q", "Slade", "", "", [], user_id="760")
    turn.place = KMUTT
    await RememberMemory(store).run({"content": "Slade has the flu", "about": "slade", "kind": "claim"}, turn)
    assert store.recall("slade flu")[0].kind == "self"


async def test_a_memory_about_someone_else_stays_a_claim(store):
    turn = Turn("q", "Mint", "", "", [], user_id="222")
    turn.place = KMUTT
    await RememberMemory(store).run({"content": "Slade has the flu", "about": "Slade", "kind": "claim"}, turn)
    assert store.recall("slade flu")[0].kind == "claim"


# ── a whole reply ────────────────────────────────────────────────────────────

def _handler(mock_openai_client, embedder):
    from unittest.mock import patch
    from src.ai_response_handler import AIResponseHandler
    with patch("src.ai_provider.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = mock_openai_client
        handler = AIResponseHandler(endpoint="https://fake.openai.azure.com/openai/v1",
                                    api_key="fake_key", deployment="gpt-5-mini")
    handler.ai_client = mock_openai_client
    handler.memory_embedder = embedder
    return handler


async def test_a_reply_brings_up_a_past_day_from_the_same_server(database, mock_openai_client):
    log = ChannelLog(database)
    log.save_episode(KMUTT, date(2026, 11, 1), "- Slade said he had the flu and needed a sick note", ["760"])
    log.set_episode_embedding(1, _vector("slade had the flu sick"), "fake-1")
    mock_openai_client.chat.completions.create.side_effect = [_reply("on the 1st slade had the flu")]
    handler = _handler(mock_openai_client, FakeEmbedder())
    await handler.generate_reply("/was anyone sick", "", "Mint", "222", allow_files=True, place=KMUTT)
    sent = "\n".join(str(m["content"]) for m in mock_openai_client.chat.completions.create.call_args[1]["messages"])
    assert "Slade said he had the flu" in sent
    assert "Sunday 1 Nov" in sent


async def test_a_reply_in_another_server_does_not_see_that_day(database, mock_openai_client):
    log = ChannelLog(database)
    log.save_episode(KMUTT, date(2026, 11, 1), "- Slade said he had the flu", ["760"])
    log.set_episode_embedding(1, _vector("slade had the flu sick"), "fake-1")
    mock_openai_client.chat.completions.create.side_effect = [_reply("no clue")]
    handler = _handler(mock_openai_client, FakeEmbedder())
    await handler.generate_reply("/was anyone sick", "", "Mint", "222", allow_files=True,
                                 place=Place("1300", "XLRX", "66", "chat"))
    sent = "\n".join(str(m["content"]) for m in mock_openai_client.chat.completions.create.call_args[1]["messages"])
    assert "Slade said he had the flu" not in sent


async def test_the_message_is_embedded_once_for_memories_and_past_days(database, mock_openai_client):
    store = MemoryStore(database)
    _teach(store, "slade had the flu", "Slade", SLADE)
    log = ChannelLog(database)
    log.save_episode(KMUTT, date(2026, 11, 1), "- Slade said he had the flu", ["760"])
    log.set_episode_embedding(1, _vector("slade had the flu"), "fake-1")
    embedder = FakeEmbedder()
    mock_openai_client.chat.completions.create.side_effect = [_reply("slade")]
    handler = _handler(mock_openai_client, embedder)
    await handler.generate_reply("/was anyone sick", "", "Mint", "222", allow_files=True, place=KMUTT)
    assert len(embedder.calls) == 1


# ── the nightly run ──────────────────────────────────────────────────────────

async def test_the_night_runs_once_per_day_after_four(database):
    from src.sleep import due
    assert not due(database, datetime(2026, 11, 19, 3, 59, tzinfo=BANGKOK))
    assert due(database, datetime(2026, 11, 19, 4, 1, tzinfo=BANGKOK))
    database.set_meta("sleep_last_day", "2026-11-19")
    assert not due(database, datetime(2026, 11, 19, 23, 0, tzinfo=BANGKOK))
    assert due(database, datetime(2026, 11, 20, 4, 0, tzinfo=BANGKOK))


async def test_one_night_summarizes_tidies_and_prunes(database):
    from src.sleep import sleep_once
    log = ChannelLog(database)
    _chat_day(log)
    log.add(_message("ancient", when=_at(TODAY - timedelta(days=40), 12)))
    client = _client(_reply("- they planned the proposal"))
    result = await sleep_once(database, client, "m", FakeEmbedder(), TODAY)
    assert result["summaries"] == 1 and result["pruned"] == 1
    assert database.get_meta("sleep_last_day") == "2026-11-19"


async def test_a_night_stops_summarizing_when_the_budget_is_spent(database):
    from src.sleep import sleep_once
    from src.usage_guard import BudgetExceededError
    log = ChannelLog(database)
    _chat_day(log)
    _chat_day(log, channel=("56", "memes"))
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=BudgetExceededError("daily", TODAY))
    result = await sleep_once(database, client, "m", FakeEmbedder(), TODAY)
    assert result["summaries"] == 0
    assert client.chat.completions.create.call_count == 1


async def test_a_reply_kept_in_one_server_is_not_served_in_another(database, mock_openai_client):
    # Seen live: the answer from cs-kmutt, made from its past days, came back word for word in XLRX
    mock_openai_client.chat.completions.create.side_effect = [_reply("slade had the flu on the 1st"), _reply("no clue")]
    handler = _handler(mock_openai_client, FakeEmbedder())
    await handler.generate_reply("/was anyone sick", "", "Mint", "222", allow_files=True, place=KMUTT)
    other = await handler.generate_reply("/was anyone sick", "", "Mint", "222", allow_files=True,
                                         place=Place("1300", "XLRX", "66", "chat"))
    assert other.text == "no clue"


# ── review on #56 ────────────────────────────────────────────────────────────

def _episode(log, channel=("56", "staff"), text="- the mods planned a ban", people=("760",)):
    place = Place("1280", "cs-kmutt", channel[0], channel[1])
    episode_id = log.save_episode(place, TODAY - timedelta(days=1), text, list(people))
    log.set_episode_embedding(episode_id, _vector("exam room"), "fake-1")
    return episode_id


def test_a_summary_of_a_channel_they_cannot_see_is_left_out(log):
    _episode(log)
    assert log.find_episodes(_vector("exam room"), "fake-1", "1280", visible=lambda channel_id: channel_id == "55") == []
    assert log.find_episodes(_vector("exam room"), "fake-1", "1280", visible=lambda channel_id: True)


async def test_a_reply_only_shows_days_from_channels_the_asker_can_see(database, mock_openai_client):
    log = ChannelLog(database)
    _episode(log, text="- the mods talked about the exam room in private")
    mock_openai_client.chat.completions.create.side_effect = [_reply("dunno"), _reply("dunno")]
    handler = _handler(mock_openai_client, FakeEmbedder())
    hidden = Place("1280", "cs-kmutt", "55", "general", can_view=lambda channel_id: channel_id == "55")
    await handler.generate_reply("/which exam room", "", "Mint", "222", allow_files=True, place=hidden)
    assert "mods talked" not in "\n".join(str(m["content"]) for m in mock_openai_client.chat.completions.create.call_args[1]["messages"])
    # Without a way to check, only the channel the reply is in counts
    await handler.generate_reply("/which exam room?", "", "Mint", "222", allow_files=True, place=KMUTT)
    assert "mods talked" not in "\n".join(str(m["content"]) for m in mock_openai_client.chat.completions.create.call_args[1]["messages"])


def test_a_place_checks_what_the_member_can_see():
    from src.memory_store import place_of
    allowed = SimpleNamespace(view_channel=True)
    denied = SimpleNamespace(view_channel=False)
    channels = {55: SimpleNamespace(permissions_for=lambda member: allowed),
                56: SimpleNamespace(permissions_for=lambda member: denied)}
    guild = SimpleNamespace(id=1280, name="cs-kmutt", get_channel=channels.get)
    place = place_of(SimpleNamespace(id=55, name="general", guild=guild), member=object())
    assert place.can_view("55") and not place.can_view("56") and not place.can_view("99")


def test_the_help_states_the_configured_keep_time(monkeypatch):
    from config.config import Config
    from src.help_menu import build_help_embed
    monkeypatch.setattr(Config, "CHANNEL_LOG_KEEP_DAYS", 365)
    text = "\n".join(f.value for f in build_help_embed().fields)
    assert "365 days" in text and "30 days" not in text


def test_only_the_closest_pairs_come_out_of_the_database(store):
    for n in range(6):
        _teach(store, f"exam {n} is in room {n}", "exam", KRUSKAL)
    assert len(store.pairs_to_tidy("fake-1", limit=4)) == 4


async def test_a_verdict_does_not_land_on_a_memory_that_took_a_deleted_ones_number(store):
    from src.sleep import tidy_memories
    first = _teach(store, "the exam is in room 410", "exam", KRUSKAL, days_ago=2)
    second = _teach(store, "exam: room 410", "exam", MINT)

    async def judge(**kwargs):
        # Mint runs /forget while the judge is thinking, and her next memory gets the same number
        store.forget_user(MINT.user_id)
        reused = store.remember("the final is in room 302", "final", "claim", SLADE, KMUTT).memory_id
        assert reused == second
        return _verdicts((1, "same"))
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=judge)
    result = await tidy_memories(store, client, "m", "fake-1")
    assert result["merged"] == 0
    assert store.get(second).content == "the final is in room 302" and store.get(first).confirmed_by == []
    assert store.recall("final room 302")


async def test_forgetting_the_kept_memory_brings_back_the_merged_one(store):
    from src.sleep import tidy_memories
    _teach(store, "the exam is in room 410", "exam", KRUSKAL, days_ago=2)
    mint = _teach(store, "exam: room 410", "exam", MINT)
    await tidy_memories(store, _client(_verdicts((1, "same"))), "m", "fake-1")
    store.forget_user(KRUSKAL.user_id)
    assert [m.id for m in store.recall("exam room")] == [mint]


async def test_someones_report_about_themselves_is_never_merged_into_anothers_claim(store):
    from src.sleep import tidy_memories
    _teach(store, "Slade is vegetarian", "Slade", MINT, days_ago=2)
    _teach(store, "Slade is vegetarian", "Slade", SLADE, kind="self")
    result = await tidy_memories(store, _client(_verdicts((1, "same"))), "m", "fake-1")
    assert result["merged"] == 0


async def test_a_day_summary_lists_everyone_who_spoke_even_past_the_prompt_limit(log, monkeypatch):
    from src import sleep
    monkeypatch.setattr(sleep, "MAX_DAY_MESSAGES", 3)
    yesterday = TODAY - timedelta(days=1)
    log.add(_message("early bird", MINT, _at(yesterday, 6)))
    for n in range(5):
        log.add(_message(f"later {n}", SLADE, _at(yesterday, 12, n)))
    await sleep.summarize_day(log, _client(_reply("- talk")), "m", FakeEmbedder(), "55", yesterday)
    log.forget_user(MINT.user_id)
    assert log.episode_count() == 0


async def test_a_summary_written_while_someone_ran_forget_is_thrown_away(log):
    from src.sleep import summarize_day
    yesterday = TODAY - timedelta(days=1)
    _chat_day(log)

    async def write(**kwargs):
        log.forget_user(SLADE.user_id)
        return _reply("- Slade said something private")
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=write)
    assert await summarize_day(log, client, "m", FakeEmbedder(), "55", yesterday) is None
    assert log.episode_count() == 0
