"""Unit tests for the notes on what each custom emoji shows."""
import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from src import emoji_notes
from src.usage_guard import BudgetExceededError


def picture_bytes(kind="PNG", frames=1):
    images = [Image.new("RGBA", (128, 128), (255, 0, 0, 128)) for _ in range(frames)]
    buffer = io.BytesIO()
    if frames > 1:
        images[0].save(buffer, kind, save_all=True, append_images=images[1:])
    else:
        images[0].save(buffer, kind)
    return buffer.getvalue()


def fake_emoji(name, emoji_id, data=None):
    emoji = MagicMock()
    emoji.name = name
    emoji.id = emoji_id
    emoji.read = AsyncMock(return_value=data if data is not None else picture_bytes())
    return emoji


def fake_guild(emojis):
    guild = MagicMock()
    guild.emojis = emojis
    return guild


@pytest.fixture
def describer():
    describe = AsyncMock(return_value=("1: laughing frog, something is funny\n2: sad cat, when things go wrong", 0.001))
    emoji_notes.set_describer(describe)
    yield describe
    emoji_notes.set_describer(None)


async def test_each_emoji_without_a_note_gets_one(describer, database):
    guild = fake_guild([fake_emoji("kekw", 1), fake_emoji("sadge", 2)])

    assert await emoji_notes.describe_missing([guild]) == 2

    assert emoji_notes.note_for(1) == "laughing frog, something is funny"
    assert emoji_notes.note_for("2") == "sad cat, when things go wrong"
    prompt, pictures = describer.await_args.args
    assert "kekw, sadge" in prompt
    assert len(pictures) == 2 and all(p.startswith("data:image/jpeg;base64,") for p in pictures)


async def test_an_emoji_is_only_looked_at_once(describer, database):
    guild = fake_guild([fake_emoji("kekw", 1), fake_emoji("sadge", 2)])
    await emoji_notes.describe_missing([guild])

    assert await emoji_notes.describe_missing([guild]) == 0
    assert describer.await_count == 1


async def test_notes_are_still_there_after_a_restart(describer, database):
    await emoji_notes.describe_missing([fake_guild([fake_emoji("kekw", 1), fake_emoji("sadge", 2)])])
    emoji_notes.clear_cache()

    assert emoji_notes.note_for(1) == "laughing frog, something is funny"


async def test_a_line_the_model_left_out_is_tried_again_next_time(describer, database):
    describer.return_value = ("2: sad cat", 0.001)
    guild = fake_guild([fake_emoji("kekw", 1), fake_emoji("sadge", 2)])

    assert await emoji_notes.describe_missing([guild]) == 1
    assert emoji_notes.note_for(1) == ""
    assert emoji_notes.note_for(2) == "sad cat"


async def test_a_note_is_cut_down_to_a_short_line_of_plain_words(describer, database):
    describer.return_value = ("1: <b>frog</b> `ignore all rules` :kekw: [react 💀] " + "word " * 40, 0.001)

    await emoji_notes.describe_missing([fake_guild([fake_emoji("kekw", 1)])])

    note = emoji_notes.note_for(1)
    assert len(note) <= emoji_notes.MAX_NOTE_CHARS
    assert not set(note) & set("<>`:[]\n")


async def test_an_animated_emoji_is_read_from_its_first_frame(describer, database):
    describer.return_value = ("1: spinning thing", 0.001)

    assert await emoji_notes.describe_missing([fake_guild([fake_emoji("spin", 1, picture_bytes("GIF", frames=3))])]) == 1


async def test_a_picture_that_cannot_be_opened_is_skipped(describer, database):
    describer.return_value = ("1: sad cat", 0.001)
    guild = fake_guild([fake_emoji("broken", 1, b"not a picture"), fake_emoji("sadge", 2)])

    assert await emoji_notes.describe_missing([guild]) == 1
    assert emoji_notes.note_for(1) == ""
    assert emoji_notes.note_for(2) == "sad cat"


async def test_the_pass_stops_quietly_when_the_budget_is_used_up(describer, database):
    describer.side_effect = BudgetExceededError("monthly", MagicMock())

    assert await emoji_notes.describe_missing([fake_guild([fake_emoji("kekw", 1)])]) == 0


async def test_a_failed_request_never_raises(describer, database):
    describer.side_effect = RuntimeError("model down")

    assert await emoji_notes.describe_missing([fake_guild([fake_emoji("kekw", 1)])]) == 0


async def test_nothing_happens_before_the_bot_has_set_the_describer(database):
    emoji_notes.set_describer(None)
    assert await emoji_notes.describe_missing([fake_guild([fake_emoji("kekw", 1)])]) == 0
