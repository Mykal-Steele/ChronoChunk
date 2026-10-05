"""Unit tests for how the message processor splits long answers."""
from src.message_processor import MessageProcessor


def test_short_text_is_one_chunk():
    assert MessageProcessor._split_text("hello") == ["hello"]


def test_empty_text_is_no_chunks():
    assert MessageProcessor._split_text("") == []


def test_long_text_splits_under_the_discord_limit():
    chunks = MessageProcessor._split_text("word " * 1000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 2000 for chunk in chunks)
    assert "".join(chunks).replace(" ", "") == "word" * 1000


def test_split_prefers_line_breaks():
    text = "\n".join(f"line {i} " + "x" * 80 for i in range(40))
    chunks = MessageProcessor._split_text(text)
    assert all(not chunk.endswith("x" * 5 + " ") for chunk in chunks)
    assert all(chunk.splitlines()[-1].startswith("line ") for chunk in chunks)


def test_split_keeps_code_blocks_closed_in_every_chunk():
    text = "here:\n```python\n" + "value = compute(1, 2)\n" * 300 + "```\ndone"
    chunks = MessageProcessor._split_text(text)
    assert len(chunks) > 1
    assert all(chunk.count("```") % 2 == 0 for chunk in chunks)
    assert all(len(chunk) <= 2000 for chunk in chunks)
