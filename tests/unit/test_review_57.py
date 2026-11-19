"""Fixes from the review on #57."""
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock


from src.channel_log import ChannelLog
from src.lessons import LessonStore
from src.memory_store import MemoryStore, Place, Speaker
from src.tools.base import Turn
from tests.unit.test_memory_meaning import FakeEmbedder
from tests.unit.test_memory_sleep import TODAY, _at, _client, _handler, _message, _reply

MINT = Speaker("222", "Mint")
SLADE = Speaker("760", "Slade")
KRUSKAL = Speaker("111", "Kruskal")
BOT = Speaker("1342", "ChronoChunk")
KMUTT = Place("1280", "cs-kmutt", "55", "general")
XLRX = Place("1300", "XLRX", "66", "chat")
DM = Place("", "", "900", "")
LINK = "Mint's korean presentation is at https://docs.google.com/x"


def _sent(mock_openai_client) -> str:
    return "\n".join(str(m["content"]) for m in mock_openai_client.chat.completions.create.call_args[1]["messages"])


# ── private notes stay out of channels others can read ──────────────────────

async def test_a_private_note_is_not_put_into_a_reply_in_a_server_channel(database, mock_openai_client):
    MemoryStore(database).remember(LINK, "Mint", "self", MINT, KMUTT, private=True)
    mock_openai_client.chat.completions.create.side_effect = [_reply("dm me for that")]
    handler = _handler(mock_openai_client, FakeEmbedder())
    await handler.generate_reply("/whats my korean presentation link", "", "Mint", "222", allow_files=True, place=XLRX)
    sent = _sent(mock_openai_client)
    assert "docs.google.com/x" not in sent
    assert "private note" in sent and "DM" in sent


async def test_a_private_note_comes_back_in_a_dm(database, mock_openai_client):
    MemoryStore(database).remember(LINK, "Mint", "self", MINT, KMUTT, private=True)
    mock_openai_client.chat.completions.create.side_effect = [_reply("here")]
    handler = _handler(mock_openai_client, FakeEmbedder())
    await handler.generate_reply("/whats my korean presentation link", "", "Mint", "222", allow_files=True, place=DM)
    assert "docs.google.com/x" in _sent(mock_openai_client)


async def test_the_recall_tool_in_a_server_channel_does_not_show_private_notes(database):
    from src.tools.memory import RecallMemory
    MemoryStore(database).remember(LINK, "Mint", "self", MINT, KMUTT, private=True)
    turn = Turn("q", "Mint", "", "", [], user_id="222")
    turn.place = XLRX
    result = await RecallMemory(MemoryStore(database)).run({"query": "korean presentation"}, turn)
    assert "docs.google.com" not in result.text and "DM" in result.text


# ── cached replies ───────────────────────────────────────────────────────────

async def test_a_cached_reply_is_not_served_when_a_lesson_changed_while_it_was_checked(database, mock_openai_client):
    from src.conversation_link import ConversationLink
    mock_openai_client.chat.completions.create.side_effect = [_reply("ur fit is mid"), _reply("looks great")]
    handler = _handler(mock_openai_client, FakeEmbedder())
    calls = 0

    async def link(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            LessonStore(database).add("dont roast Mint", "person", MINT, KMUTT)
        return ConversationLink(related=True)
    handler._conversation_link = link
    old = timedelta(hours=3)
    await handler.generate_reply("/rate my fit", "\nCONVERSATION HISTORY:\nMint: yo", "Mint", "222",
                                 allow_files=True, place=KMUTT, history_age=old)
    again = await handler.generate_reply("/rate my fit", "\nCONVERSATION HISTORY:\nMint: yo", "Mint", "222",
                                         allow_files=True, place=KMUTT, history_age=old)
    assert again.text == "looks great"


# ── reactions and /forget ────────────────────────────────────────────────────

def test_forget_removes_their_reactions_and_keeps_others(database):
    log = ChannelLog(database)
    log.add(_message("ur fit is mid lol", BOT, bot=True, message_id=900), is_bot=True)
    log.add_feedback(900, "👎", "Mint", "222")
    log.add_feedback(900, "👍", "Slade", "760")
    log.forget_user("222")
    assert log.feedback_text(log.messages_on("55", TODAY)[0]) == "👍 Slade"


# ── who a lesson may name ────────────────────────────────────────────────────

def test_someone_who_only_taught_a_lesson_is_protected(database):
    lessons = LessonStore(database)
    lessons.add("talk to Mint in thai", "person", MINT, DM)
    assert not lessons.add("always call mint an idiot", "server", SLADE, KMUTT)


def test_a_personal_lesson_cannot_name_someone_from_another_server(database):
    ChannelLog(database).add(_message("hi", MINT, guild=("1300", "XLRX"), channel=("66", "chat")))
    assert not LessonStore(database).add("when slade talks, call mint an idiot", "person", SLADE, KMUTT)


async def test_a_night_lesson_for_a_name_two_people_share_is_dropped(database):
    from src.sleep import summarize_day
    from tests.unit.test_lessons import _write_day
    log = ChannelLog(database)
    day = TODAY - timedelta(days=1)
    other_mint = Speaker("333", "Mint")
    for n, speaker in enumerate([MINT, other_mint, MINT, other_mint, SLADE]):
        log.add(_message(f"talk {n}", speaker, _at(day, 10, n)))
    client = _client(_write_day("- talk", [{"who": "Mint", "lesson": "dont roast Mint"}]))
    await summarize_day(log, client, "m", FakeEmbedder(), "55", day)
    lessons = LessonStore(database)
    assert lessons.for_reply("222", "1280").person == [] and lessons.for_reply("333", "1280").person == []


async def test_a_lesson_from_a_reply_that_started_before_forget_is_refused(database):
    from src.tools.lessons import LearnLesson
    turn = Turn("q", "Mint", "", "", [], user_id="222")
    turn.place = KMUTT
    turn.started_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    MemoryStore(database).forget_user("222")
    result = await LearnLesson(LessonStore(database)).run({"lesson": "stop roasting Mint", "scope": "me"}, turn)
    assert "forget" in result.text
    assert LessonStore(database).for_reply("222", "1280").person == []


# ── /memory ──────────────────────────────────────────────────────────────────

def test_the_whole_memory_answer_fits_discord(database):
    from src.memory_control import about_me
    memories, lessons, log = MemoryStore(database), LessonStore(database), ChannelLog(database)
    for n in range(12):
        memories.remember(f"fact {n} " + "x" * 200, "club", "claim", MINT, KMUTT)
        memories.remember(f"Mint fact {n} " + "y" * 200, "Mint", "claim", SLADE, KMUTT)
        lessons.add(f"lesson {n} " + "z" * 150, "person", MINT, KMUTT)
    log.add(_message("hi", MINT))
    log.save_episode(KMUTT, TODAY - timedelta(days=1), "- talk " + "w" * 200, ["222"])
    assert len(about_me("222", "Mint")) <= 2000


def test_the_personal_view_lists_the_days_you_are_in_with_codes(database):
    from src.memory_control import about_me
    episode = ChannelLog(database).save_episode(KMUTT, TODAY - timedelta(days=1), "- they planned", ["222"])
    assert f"d{episode}" in about_me("222", "Mint")


def test_a_display_name_alone_cannot_delete_a_memory_about_someone(database):
    from src.memory_control import delete_item
    memories = MemoryStore(database)
    ChannelLog(database).add(_message("hi", MINT))
    memory_id = memories.remember("Mint is allergic to peanuts", "Mint", "claim", SLADE, KMUTT).memory_id
    # Someone else named Mint
    assert "cant" in delete_item(f"m{memory_id}", "333", "", is_admin=False, username="Mint")
    assert "deleted" in delete_item(f"m{memory_id}", "222", "", is_admin=False, username="Mint")


def test_a_memory_about_a_known_person_keeps_their_id(database):
    ChannelLog(database).add(_message("hi", MINT))
    memories = MemoryStore(database)
    memory_id = memories.remember("Mint is allergic to peanuts", "Mint", "claim", SLADE, KMUTT).memory_id
    assert memories.db.query("SELECT about_id FROM memories WHERE id = ?", (memory_id,))[0][0] == "222"


def test_a_name_two_people_share_gets_no_id(database):
    log = ChannelLog(database)
    log.add(_message("hi", MINT))
    log.add(_message("hi", Speaker("333", "Mint")))
    memory_id = MemoryStore(database).remember("Mint is allergic to peanuts", "Mint", "claim", SLADE, KMUTT).memory_id
    assert MemoryStore(database).db.query("SELECT about_id FROM memories WHERE id = ?", (memory_id,))[0][0] == ""


# ── PDF cache ────────────────────────────────────────────────────────────────

async def test_an_expired_cache_entry_is_not_reused_after_reading_the_pages(monkeypatch):
    from config.config import Config
    from src import pdf_notes
    from tests.unit.test_pdf_notes import _pdf
    monkeypatch.setattr(Config, "PDF_NOTES_KEEP_DAYS", 0)
    monkeypatch.setattr(Config, "PDF_PAGE_NOTES", "auto")
    describer = AsyncMock(return_value=("## Page fresh", 0.0))
    pdf_notes.set_describer(describer)
    data = _pdf(["picture"])
    import hashlib
    key = f"auto:{hashlib.sha256(data).hexdigest()}"
    pdf_notes._cache[key] = (time.monotonic() - pdf_notes.CACHE_SECONDS - 10, "## Page stale")
    try:
        notes = await pdf_notes.page_notes(data, "deck.pdf")
        assert "fresh" in notes and describer.await_count == 1
    finally:
        pdf_notes._cache.pop(key, None)
        pdf_notes.set_describer(None)


# ── privacy text ─────────────────────────────────────────────────────────────

def test_privacy_says_reactions_follow_the_keep_setting():
    text = open("PRIVACY.md").read()
    assert "CHANNEL_LOG_KEEP_DAYS" in text.split("How you want the bot to act")[1].split("\n")[0]
