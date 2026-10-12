"""Unit tests for MessageHandler — Discord objects are mocked."""
import pytest
from src.message_handler import MessageHandler
from config.config import Config

CHANNEL_ID = "99999"
USER_ID = "12345"


@pytest.fixture
def handler():
    return MessageHandler(bot=None)


# ── update_channel_history ────────────────────────────────────────────────────

def test_update_creates_channel_entry(handler):
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "hello", is_bot=False)
    assert CHANNEL_ID in handler.last_channel_messages
    assert len(handler.last_channel_messages[CHANNEL_ID]) == 1


def test_update_stores_correct_fields(handler):
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "hello there", is_bot=False)
    entry = handler.last_channel_messages[CHANNEL_ID][0]
    assert entry["author_name"] == "Alice"
    assert entry["content"] == "hello there"
    assert entry["is_bot"] is False


def test_update_bot_message_flagged(handler):
    handler.update_channel_history(CHANNEL_ID, "0", "ChronoChunk", "beep boop", is_bot=True)
    entry = handler.last_channel_messages[CHANNEL_ID][0]
    assert entry["is_bot"] is True


def test_update_strips_slash_from_command(handler):
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "/game 10", is_bot=False, is_command=True)
    entry = handler.last_channel_messages[CHANNEL_ID][0]
    assert entry["content"] == "game 10"


def test_channel_history_size_limit(handler):
    for i in range(Config.CHANNEL_HISTORY_SIZE + 10):
        handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", f"msg {i}", is_bot=False)
    assert len(handler.last_channel_messages[CHANNEL_ID]) == Config.CHANNEL_HISTORY_SIZE


def test_channel_history_keeps_most_recent(handler):
    for i in range(Config.CHANNEL_HISTORY_SIZE + 5):
        handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", f"msg {i}", is_bot=False)
    last = handler.last_channel_messages[CHANNEL_ID][-1]["content"]
    assert last == f"msg {Config.CHANNEL_HISTORY_SIZE + 4}"


def test_multiple_channels_isolated(handler):
    handler.update_channel_history("CH_A", USER_ID, "Alice", "hi A", is_bot=False)
    handler.update_channel_history("CH_B", USER_ID, "Bob", "hi B", is_bot=False)
    assert len(handler.last_channel_messages["CH_A"]) == 1
    assert len(handler.last_channel_messages["CH_B"]) == 1


# ── build_conversation_context ────────────────────────────────────────────────

def test_build_context_empty_returns_string(handler):
    result = handler.build_conversation_context(CHANNEL_ID, {})
    assert isinstance(result, str)


def test_build_context_includes_messages(handler):
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "what is python", is_bot=False)
    handler.update_channel_history(CHANNEL_ID, "0", "ChronoChunk", "it's a language fr", is_bot=True)
    result = handler.build_conversation_context(CHANNEL_ID, {})
    assert "Alice" in result or "python" in result
    assert "ChronoChunk" in result or "language" in result


def test_build_context_short_followup_injects_hint(handler):
    # Build 2 turns of history so bot_messages and user_messages are detected
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "what is python", is_bot=False)
    handler.update_channel_history(CHANNEL_ID, "0", "ChronoChunk", "python is a programming language", is_bot=True)
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "why", is_bot=False)
    result = handler.build_conversation_context(CHANNEL_ID, {})
    # Short follow-up (≤4 words) should inject the hint with topic words
    assert "short reply context hint" in result and "python" in result


# ── history snapshots and message age ─────────────────────────────────────────

def test_replace_channel_history_swaps_the_stored_messages(handler):
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "stale", is_bot=False)
    handler.replace_channel_history(CHANNEL_ID, [
        {"user_id": USER_ID, "username": "Alice", "content": "one", "is_bot": False},
        {"user_id": "0", "username": "ChronoChunk", "content": "two", "is_bot": True},
    ])
    assert [m["content"] for m in handler.last_channel_messages[CHANNEL_ID]] == ["one", "two"]


def test_update_channel_history_keeps_the_real_send_time(handler):
    from datetime import datetime, timezone
    sent = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "hello", is_bot=False, timestamp=sent)
    assert handler.last_channel_messages[CHANNEL_ID][0]["timestamp"] == sent.isoformat()


def test_build_context_marks_fresh_messages_as_live(handler):
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "what is python", is_bot=False)
    result = handler.build_conversation_context(CHANNEL_ID, {})
    assert ">>> Alice: what is python" in result


def test_build_context_never_marks_day_old_messages_as_live(handler):
    from datetime import datetime, timedelta, timezone
    yesterday = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "old news", is_bot=False, timestamp=yesterday)
    result = handler.build_conversation_context(CHANNEL_ID, {})
    assert "[yesterday, LOW priority] Alice: old news" in result
    assert ">>>" not in result


def test_build_context_same_day_but_hours_old_is_secondary(handler):
    from datetime import datetime, timedelta, timezone
    five_hours_ago = datetime.now(timezone.utc) - timedelta(hours=5)
    handler.update_channel_history(CHANNEL_ID, USER_ID, "Alice", "earlier today", is_bot=False, timestamp=five_hours_ago)
    result = handler.build_conversation_context(CHANNEL_ID, {})
    assert "[1 msgs ago] Alice: earlier today" in result


def test_message_age_accepts_old_style_naive_timestamps(handler):
    from datetime import datetime, timedelta
    naive = (datetime.now() - timedelta(days=2, hours=1)).isoformat()
    assert handler._message_age(naive).days == 2
    assert handler._message_age(None).days == 0
    assert handler._message_age("not a date").days == 0


# ── how much of the channel one answer reads ──────────────────────────────────

from datetime import datetime, timedelta, timezone  # noqa: E402

from src.message_handler import MAX_MESSAGES, OLDER_MESSAGE_CHARS, QUICK_MESSAGES, THREAD_MESSAGES  # noqa: E402

BOT_ID = "0"


def _say(handler, name, text, *, minutes_ago=0.0, bot=False, message_id=None, reply_to="", reply_to_id=""):
    handler.update_channel_history(
        CHANNEL_ID, BOT_ID if bot else name.lower(), "ChronoChunk" if bot else name, text, is_bot=bot,
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago), message_id=message_id,
        reply_to=reply_to, reply_to_id=reply_to_id)


def _history_lines(context):
    if "CONVERSATION HISTORY:" not in context:
        return []
    lines = context.split("CONVERSATION HISTORY:\n", 1)[1].splitlines()
    return [line for line in lines if line and not line.startswith(("(short reply", "(BANNED"))]


def test_a_one_off_question_reads_only_the_last_few_messages(handler):
    for number in range(20):
        _say(handler, "Sam", f"chatter {number}", minutes_ago=30 - number)
    lines = _history_lines(handler.build_conversation_context(CHANNEL_ID, {"user_id": "alex", "username": "Alex"}))
    assert len(lines) == QUICK_MESSAGES
    assert lines[-1].endswith("chatter 19")


def test_a_running_conversation_reads_everything_since_it_began(handler):
    for number in range(10):
        _say(handler, "Sam", f"before {number}", minutes_ago=60 - number)
    began = datetime.now(timezone.utc) - timedelta(minutes=5)
    for number in range(15):
        _say(handler, "Sam", f"during {number}", minutes_ago=4 - number * 0.2)
    context = handler.build_conversation_context(CHANNEL_ID, {"user_id": "alex", "username": "Alex"}, session_start=began)
    lines = _history_lines(context)
    assert sum("during" in line for line in lines) == 15
    assert sum("before" in line for line in lines) == 3  # a little of what led up to it


def test_the_window_stops_growing_at_its_limit(handler):
    began = datetime.now(timezone.utc) - timedelta(minutes=10)
    for number in range(50):
        _say(handler, "Sam", f"msg {number}", minutes_ago=9 - number * 0.1)
    context = handler.build_conversation_context(CHANNEL_ID, {"user_id": "alex", "username": "Alex"}, session_start=began)
    assert len(_history_lines(context)) == MAX_MESSAGES


def test_the_bots_own_exchange_with_the_asker_survives_other_peoples_chatter(handler):
    _say(handler, "Alex", "recommend me one sci fi book", minutes_ago=20)
    _say(handler, "x", "Dune, obviously", minutes_ago=19.9, bot=True, reply_to="Alex", reply_to_id="alex")
    for number in range(15):
        _say(handler, "Sam", f"lunch talk {number}", minutes_ago=15 - number)
    context = handler.build_conversation_context(CHANNEL_ID, {"user_id": "alex", "username": "Alex"})
    assert "Alex: recommend me one sci fi book" in context
    assert "YOU (ChronoChunk) (replying to Alex): Dune, obviously" in context
    assert "(other messages in between left out)" in context
    assert "lunch talk 3" not in context


def test_the_bots_answers_to_other_people_are_not_pulled_back_in(handler):
    _say(handler, "x", "an answer for sam", minutes_ago=20, bot=True, reply_to="Sam", reply_to_id="sam")
    for number in range(10):
        _say(handler, "Sam", f"chatter {number}", minutes_ago=15 - number)
    context = handler.build_conversation_context(CHANNEL_ID, {"user_id": "alex", "username": "Alex"})
    assert "an answer for sam" not in context


def test_only_a_few_older_lines_of_the_thread_are_kept(handler):
    for number in range(12):
        _say(handler, "Alex", f"old question {number}", minutes_ago=60 - number)
    for number in range(10):
        _say(handler, "Sam", f"chatter {number}", minutes_ago=15 - number)
    context = handler.build_conversation_context(CHANNEL_ID, {"user_id": "alex", "username": "Alex"})
    assert sum("old question" in line for line in _history_lines(context)) == THREAD_MESSAGES


def test_the_message_being_answered_is_not_repeated_in_the_history(handler):
    _say(handler, "Alex", "earlier thing", message_id=1)
    _say(handler, "Alex", "the question", message_id=2)
    context = handler.build_conversation_context(CHANNEL_ID, {"user_id": "alex", "username": "Alex"}, current_id=2)
    assert "earlier thing" in context
    assert "the question" not in context


def test_a_reply_shows_who_it_answered(handler):
    _say(handler, "Sam", "nah python clears", reply_to="Mina", reply_to_id="mina")
    _say(handler, "Mina", "ok chrono settle this", reply_to="YOU", reply_to_id=BOT_ID)
    context = handler.build_conversation_context(CHANNEL_ID, {})
    assert "Sam (replying to Mina): nah python clears" in context
    assert "Mina (replying to YOU): ok chrono settle this" in context


def test_an_old_long_message_is_cut_and_the_newest_ones_are_whole(handler):
    long_text = "word " * 400
    _say(handler, "Sam", long_text)
    for number in range(4):
        _say(handler, "Sam", f"short {number}")
    _say(handler, "Sam", long_text)
    lines = _history_lines(handler.build_conversation_context(CHANNEL_ID, {}))
    assert len(lines[0]) < OLDER_MESSAGE_CHARS + 60 and lines[0].endswith("[...]")
    assert len(lines[-1]) > 1900


def test_saved_chats_with_the_person_are_shown_when_they_are_not_in_the_channel(handler):
    _say(handler, "Alex", "what did we talk about")
    user_data = {"user_id": "alex", "username": "Alex", "conversation_history": [
        {"user_message": f"question {number}", "bot_response": f"answer {number}"} for number in range(6)]}
    context = handler.build_conversation_context(CHANNEL_ID, user_data)
    assert "EARLIER CHATS WITH THEM" in context
    assert "them: question 5 | u: answer 5" in context
    assert "question 2" not in context  # only the last few


def test_a_saved_chat_that_is_already_in_the_channel_is_not_shown_twice(handler):
    _say(handler, "Alex", "whats a good book")
    _say(handler, "x", "Dune, obviously", bot=True)
    user_data = {"user_id": "alex", "username": "Alex", "conversation_history": [
        {"user_message": "whats a good book", "bot_response": "Dune, obviously"}]}
    assert "EARLIER CHATS WITH THEM" not in handler.build_conversation_context(CHANNEL_ID, user_data)


def test_a_stand_in_for_a_failed_answer_never_comes_back_as_context(handler):
    _say(handler, "Alex", "hello")
    _say(handler, "x", "some brand new wording for an error", bot=True)
    handler.not_answers.append("some brand new wording for an error")
    assert "brand new wording" not in handler.build_conversation_context(CHANNEL_ID, {})


def test_a_short_follow_up_is_read_from_the_message_being_answered(handler):
    _say(handler, "Alex", "what is python")
    _say(handler, "x", "python is a programming language", bot=True)
    context = handler.build_conversation_context(CHANNEL_ID, {}, query="/why")
    assert "short reply context hint — likely topic: python, programming, language" in context


# ── worn-out phrases ──────────────────────────────────────────────────────────

def test_a_number_is_never_called_worn_out(handler):
    _say(handler, "x", "67", bot=True)
    _say(handler, "x", "its 67 again, six seven", bot=True)
    assert handler._get_banned_phrases(CHANNEL_ID) == []


def test_a_phrase_is_only_matched_as_whole_words(handler):
    _say(handler, "x", "this generation has a huge telescope budget", bot=True)
    assert handler._get_banned_phrases(CHANNEL_ID) == []
    _say(handler, "x", "ratio, cope harder", bot=True)
    assert set(handler._get_banned_phrases(CHANNEL_ID)) == {"ratio", "cope", "cope harder"}


def test_the_worn_out_note_says_it_is_about_wording_only(handler):
    _say(handler, "x", "skill issue tbh", bot=True)
    context = handler.build_conversation_context(CHANNEL_ID, {})
    assert "BANNED this response" in context and "never change or dodge a fact, a name or a number" in context
