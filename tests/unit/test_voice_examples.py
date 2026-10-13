"""The example replies in config/voice_examples.md: which ones are read, and how they reach the prompt."""
from config.ai_config import VOICE_EXAMPLES_FILE, load_voice_examples, voice_examples_prompt

SHEET = """# Voice examples

Intro text with a line that looks like one: `-# Source: [Search slides, page 44](<LINK>)`

## Chat

### 1

Message: `yo whats good`

Reply: What's up. How's it hanging


### 2

Message: `what should i eat for dinner`

Reply:


## Explaining

### 3

Message: `whats the difference between tcp and udp`

Note: keep it short.

Facts:
- TCP sets up a connection first.
- UDP just sends.

Reply: TCP needs to have a connection. UDP doesnt.
UDP is for calls and games.
-# Source: [Networking slides, page 3](<LINK>)

### 4

Message: `/explain this` with a screenshot of a Python error

The screenshot: `TypeError` on the line `print("total: " + total)`.

Facts:
- `total` is a number.

Reply:
- Turn it into a string first
- Or use an f-string

## Your own

### 5

Message:

Reply: a reply with no message
"""


def _sheet(tmp_path, text=SHEET):
    path = tmp_path / "voice_examples.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_filled_replies_are_read_with_their_message(tmp_path):
    examples = load_voice_examples(_sheet(tmp_path))
    assert [example.message for example in examples] == [
        "yo whats good", "whats the difference between tcp and udp", "/explain this"]
    assert examples[0].reply == "What's up. How's it hanging"


def test_an_empty_reply_and_an_empty_message_are_skipped(tmp_path):
    messages = [example.message for example in load_voice_examples(_sheet(tmp_path))]
    assert "what should i eat for dinner" not in messages
    assert "" not in messages


def test_a_reply_keeps_its_lines_whether_it_starts_beside_the_label_or_under_it(tmp_path):
    examples = load_voice_examples(_sheet(tmp_path))
    assert examples[1].reply == ("TCP needs to have a connection. UDP doesnt.\nUDP is for calls and games.\n"
                                 "-# Source: [Networking slides, page 3](<LINK>)")
    assert examples[2].reply == "- Turn it into a string first\n- Or use an f-string"


def test_what_the_message_came_with_is_kept_and_notes_and_facts_are_not(tmp_path):
    examples = load_voice_examples(_sheet(tmp_path))
    assert examples[2].context == 'with a screenshot of a Python error. The screenshot: `TypeError` on the line `print("total: " + total)`.'
    assert examples[1].context == ""
    assert all("Facts" not in example.reply and "Note" not in example.reply for example in examples)


def test_a_missing_sheet_means_no_examples(tmp_path):
    assert load_voice_examples(tmp_path / "nothing.md") == []
    assert voice_examples_prompt([]) == ""


def test_the_prompt_shows_each_message_with_its_reply(tmp_path):
    prompt = voice_examples_prompt(load_voice_examples(_sheet(tmp_path)))
    assert 'they said: "yo whats good"\nu said:\nWhat\'s up. How\'s it hanging' in prompt
    assert 'they said: "/explain this" (with a screenshot of a Python error.' in prompt
    assert "UDP is for calls and games." in prompt
    assert "TCP sets up a connection first" not in prompt and "keep it short" not in prompt


def test_the_prompt_never_shows_the_link_placeholder(tmp_path):
    prompt = voice_examples_prompt(load_voice_examples(_sheet(tmp_path)))
    assert "LINK" not in prompt
    assert "[Networking slides, page 3](<https://" in prompt


def test_the_bots_prompt_carries_the_examples_from_the_real_sheet():
    from src.ai_response_handler import _SYSTEM_PROMPT
    examples = load_voice_examples(VOICE_EXAMPLES_FILE)
    assert examples, "config/voice_examples.md has no filled replies"
    for example in examples:
        assert example.reply.splitlines()[0] in _SYSTEM_PROMPT
