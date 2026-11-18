"""The bot sees how old a message or a fact is, so old news is not told as if it happened now."""
from datetime import datetime, timedelta, timezone

from src.command_handler import CHANNEL_ANSWER_RULE
from src.message_context import read_channel_messages
from src.message_handler import MessageHandler
from tests.fakes import fake_author, fake_message

NOW = datetime.now(timezone.utc)


async def test_tldr_messages_are_grouped_under_the_day_they_were_sent():
    # Seen live: an 18 day old "i have the flu" was summed up as "Slade's down bad with the flu"
    slade, kruskal = fake_author("Slade", 1), fake_author("Kruskal", 2)
    old = NOW - timedelta(days=18)
    messages = [
        fake_message(slade, "i have the flu, going to hcu tmw", created_at=old),
        fake_message(kruskal, "get well", created_at=old + timedelta(minutes=1)),
        fake_message(kruskal, "bot is back", created_at=NOW),
    ]
    block = (await read_channel_messages(messages)).text
    lines = block.splitlines()
    old_header = next(i for i, line in enumerate(lines) if "18 days ago" in line)
    today_header = next(i for i, line in enumerate(lines) if "(today)" in line)
    flu = next(i for i, line in enumerate(lines) if "flu" in line)
    back = next(i for i, line in enumerate(lines) if "bot is back" in line)
    assert old_header < flu < today_header < back
    assert sum("18 days ago" in line for line in lines) == 1


def test_the_tldr_rule_says_old_messages_are_old():
    assert "not happening now" in CHANNEL_ANSWER_RULE


def _facts_context(facts):
    return MessageHandler(bot=None).build_conversation_context("c1", {"username": "Slade", "facts": facts}, query="hi")


def test_a_fact_says_how_long_ago_it_was_said():
    text = _facts_context([{"id": 1, "content": "Slade has the flu", "extracted_from": "i have the flu",
                            "timestamp": (NOW - timedelta(days=18)).isoformat()}])
    assert "- Slade has the flu (they said this 18 days ago)" in text
    assert "'content'" not in text and "extracted_from" not in text


def test_a_fact_from_today_says_today():
    text = _facts_context([{"id": 2, "content": "You have Claude", "timestamp": NOW.isoformat()}])
    assert "- You have Claude (they said this today)" in text


def test_a_fact_with_no_usable_time_is_shown_without_one():
    text = _facts_context([{"id": 3, "content": "You like rust", "timestamp": "not a time"}, "You like go"])
    assert "- You like rust\n" in text + "\n" and "- You like go" in text
