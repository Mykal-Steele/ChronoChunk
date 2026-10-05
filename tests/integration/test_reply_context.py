"""Integration tests for what the AI gets to see when the bot answers a message.

Covers the message being replied to, attached files and images, pings, and the
order and freshness of the channel history.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from src.ai_response_handler import Reply
from tests.fakes import fake_attachment, fake_author, fake_channel, fake_message, make_pdf, make_png, reply_to

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _ai_call(ai_handler, method="generate_reply"):
    """Return what was passed to the AI: (query, history, attached_context, images)."""
    call = getattr(ai_handler, method)
    call.assert_called_once()
    args, kwargs = call.call_args
    return args[0], args[1], kwargs.get("attached_context", ""), kwargs.get("images", [])


def _channel_with(*messages):
    """Put messages (oldest first) in one channel and return the newest, the one being processed."""
    fake_channel(messages)
    return messages[-1]


# ── replying to a message with a slash ────────────────────────────────────────

async def test_slash_reply_passes_the_replied_message_to_the_ai(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    kruskal = fake_author("Kruskal")
    target = fake_message(kruskal, "hey bau", message_id=1)
    trigger = fake_message(kruskal, "/read what this msg say", message_id=2, reference=reply_to(target))
    _channel_with(target, trigger)

    await processor.process_message(trigger)

    query, _, attached, _ = _ai_call(ai_handler)
    assert query == "/read what this msg say"
    assert "MESSAGE THEY REPLIED TO (sent by Kruskal)" in attached
    assert 'text: "hey bau"' in attached


async def test_slash_reply_to_another_users_message_names_that_user(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    target = fake_message(fake_author("Alex", 777), "the deadline is friday", message_id=1)
    trigger = fake_message(fake_author("Kruskal"), "/what does he mean", message_id=2, reference=reply_to(target))
    _channel_with(target, trigger)

    await processor.process_message(trigger)

    _, _, attached, _ = _ai_call(ai_handler)
    assert "sent by Alex" in attached
    assert "the deadline is friday" in attached


async def test_slash_reply_reads_a_markdown_file(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    notes = fake_attachment("notes.md", b"# Trip plan\n\nleave at 6am", "text/markdown")
    target = fake_message(fake_author("Alex", 777), "", message_id=1, attachments=[notes])
    trigger = fake_message(fake_author("Kruskal"), "/summarize", message_id=2, reference=reply_to(target))
    _channel_with(target, trigger)

    await processor.process_message(trigger)

    _, _, attached, _ = _ai_call(ai_handler)
    assert "file: notes.md" in attached
    assert "leave at 6am" in attached


async def test_slash_reply_reads_a_pdf(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    pdf = fake_attachment("syllabus.pdf", make_pdf(["Midterm is on week 8"]), "application/pdf")
    target = fake_message(fake_author("Alex", 777), "syllabus", message_id=1, attachments=[pdf])
    trigger = fake_message(fake_author("Kruskal"), "/when is the midterm", message_id=2, reference=reply_to(target))
    _channel_with(target, trigger)

    await processor.process_message(trigger)

    _, _, attached, _ = _ai_call(ai_handler)
    assert "syllabus.pdf (pdf, 1 pages)" in attached
    assert "Midterm is on week 8" in attached


async def test_slash_reply_to_an_image_sends_the_image(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    image = fake_attachment("proof.png", PNG_BYTES, "image/png")
    target = fake_message(fake_author("Kruskal"), "", message_id=1, attachments=[image])
    trigger = fake_message(fake_author("Kruskal"), "/read", message_id=2, reference=reply_to(target))
    _channel_with(target, trigger)

    await processor.process_message(trigger)

    _, _, attached, images = _ai_call(ai_handler)
    assert "image: proof.png" in attached
    assert len(images) == 1 and images[0].startswith("data:image/png;base64,")


async def test_slash_message_with_its_own_attachment_is_read(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    todo = fake_attachment("todo.txt", b"buy milk", "text/plain")
    trigger = fake_message(fake_author("Kruskal"), "/whats on here", message_id=1, attachments=[todo])
    _channel_with(trigger)

    await processor.process_message(trigger)

    _, _, attached, _ = _ai_call(ai_handler)
    assert "FILES THEY ATTACHED TO THIS MESSAGE" in attached
    assert "buy milk" in attached


async def test_slash_without_a_reply_has_no_attached_context(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    trigger = fake_message(fake_author("Kruskal"), "/yo", message_id=1)
    _channel_with(trigger)

    await processor.process_message(trigger)

    _, _, attached, images = _ai_call(ai_handler)
    assert attached == ""
    assert images == []


async def test_reply_to_a_deleted_message_still_answers(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    deleted = fake_message(fake_author("Alex", 777), "gone", message_id=1)
    trigger = fake_message(fake_author("Kruskal"), "/read", message_id=2, reference=reply_to(deleted))
    _channel_with(trigger)  # the replied-to message is not in the channel any more

    await processor.process_message(trigger)

    _, _, attached, _ = _ai_call(ai_handler)
    assert attached == ""


# ── replying to the bot and pinging it ────────────────────────────────────────

async def test_reply_to_the_bot_includes_the_bots_message(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    bot_msg = fake_message(fake_author("ChronoChunk", bot.user.id, bot=True), "its 401", message_id=1)
    trigger = fake_message(fake_author("Kruskal"), "wdym", message_id=2, reference=reply_to(bot_msg))
    _channel_with(bot_msg, trigger)

    await processor.process_message(trigger)

    query, _, attached, _ = _ai_call(ai_handler)
    assert query == "wdym"
    assert "sent by YOU (ChronoChunk)" in attached
    assert "its 401" in attached


async def test_reply_to_another_user_without_slash_is_ignored(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    target = fake_message(fake_author("Alex", 777), "lunch?", message_id=1)
    trigger = fake_message(fake_author("Kruskal"), "sure", message_id=2, reference=reply_to(target))
    _channel_with(target, trigger)

    await processor.process_message(trigger)

    ai_handler.generate_reply.assert_not_called()


async def test_ping_triggers_an_answer_and_is_stripped_from_the_query(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    trigger = fake_message(fake_author("Kruskal"), f"<@{bot.user.id}> whats 2+2", message_id=1, mentions=[bot.user])
    _channel_with(trigger)

    await processor.process_message(trigger)

    query, _, _, _ = _ai_call(ai_handler)
    assert query == "whats 2+2"


async def test_ping_while_replying_to_someone_reads_that_message(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    target = fake_message(fake_author("Alex", 777), "E = mc^2", message_id=1)
    trigger = fake_message(fake_author("Kruskal"), f"<@{bot.user.id}> explain", message_id=2,
                           mentions=[bot.user], reference=reply_to(target))
    _channel_with(target, trigger)

    await processor.process_message(trigger)

    query, _, attached, _ = _ai_call(ai_handler)
    assert query == "explain"
    assert "E = mc^2" in attached


async def test_answer_is_sent_as_a_reply_to_the_asking_message(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    ai_handler.generate_reply = AsyncMock(return_value=Reply("it says hey bau"))
    trigger = fake_message(fake_author("Kruskal"), "/yo", message_id=1)
    _channel_with(trigger)

    await processor.process_message(trigger)

    trigger.reply.assert_called_once()
    assert trigger.reply.call_args[0][0] == "it says hey bau"
    assert trigger.reply.call_args[1]["mention_author"] is False
    trigger.channel.send.assert_not_called()


async def test_answers_never_ping_anyone(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    ai_handler.generate_reply = AsyncMock(return_value=Reply('it says "@everyone free nitro <@777>"'))
    target = fake_message(fake_author("Alex", 777), "@everyone free nitro", message_id=1)
    trigger = fake_message(fake_author("Kruskal"), "/read", message_id=2, reference=reply_to(target))
    _channel_with(target, trigger)

    await processor.process_message(trigger)

    allowed = trigger.reply.call_args[1]["allowed_mentions"]
    assert allowed.everyone is False and allowed.users is False and allowed.roles is False


# ── channel history ───────────────────────────────────────────────────────────

async def test_history_is_oldest_first_with_the_current_message_last(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    kruskal = fake_author("Kruskal")
    chrono = fake_author("ChronoChunk", bot.user.id, bot=True)
    older = [
        fake_message(kruskal, "first", message_id=1),
        fake_message(chrono, "second", message_id=2),
        fake_message(kruskal, "third", message_id=3),
    ]
    trigger = fake_message(kruskal, "/fourth", message_id=4)
    _channel_with(*older, trigger)

    await processor.process_message(trigger)

    _, history, _, _ = _ai_call(ai_handler)
    lines = [line for line in history.splitlines() if "Kruskal:" in line or "ChronoChunk" in line]
    assert [line.split(": ", 1)[1] for line in lines] == ["first", "second", "third", "fourth"]
    assert "YOU (ChronoChunk): second" in history


async def test_history_does_not_pile_up_duplicates_across_messages(pipeline):
    processor, mh, _, _, ai_handler, bot = pipeline
    kruskal = fake_author("Kruskal")
    first = fake_message(kruskal, "/one", message_id=1)
    second = fake_message(kruskal, "/two", message_id=2)

    _channel_with(first)
    await processor.process_message(first)
    _channel_with(first, second)
    await processor.process_message(second)

    stored = [m["content"] for m in mh.last_channel_messages[str(second.channel.id)] if not m["is_bot"]]
    assert stored == ["one", "two"]


async def test_other_bots_are_not_labelled_as_chronochunk(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    other_bot = fake_message(fake_author("MusicBot", 4242, bot=True), "now playing lofi", message_id=1)
    trigger = fake_message(fake_author("Kruskal"), "/yo", message_id=2)
    _channel_with(other_bot, trigger)

    await processor.process_message(trigger)

    _, history, _, _ = _ai_call(ai_handler)
    assert "MusicBot: now playing lofi" in history
    assert "YOU (ChronoChunk): now playing lofi" not in history


async def test_old_messages_are_not_marked_as_the_live_conversation(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    kruskal = fake_author("Kruskal")
    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
    old = fake_message(kruskal, "ancient drama", message_id=1, created_at=three_days_ago)
    trigger = fake_message(kruskal, "/yo", message_id=2)
    _channel_with(old, trigger)

    await processor.process_message(trigger)

    _, history, _, _ = _ai_call(ai_handler)
    assert "[3 days ago, LOW priority] Kruskal: ancient drama" in history
    assert ">>> Kruskal: yo" in history


async def test_image_only_messages_show_up_in_history(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    kruskal = fake_author("Kruskal")
    picture = fake_message(kruskal, "", message_id=1, attachments=[fake_attachment("proof.png", PNG_BYTES, "image/png")])
    trigger = fake_message(kruskal, "/yo", message_id=2)
    _channel_with(picture, trigger)

    await processor.process_message(trigger)

    _, history, _, _ = _ai_call(ai_handler)
    assert "Kruskal: [attached: proof.png]" in history


# ── things posted right before the message ────────────────────────────────────

async def test_picture_posted_just_before_is_shown_to_the_ai_without_a_reply(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    posted = fake_message(fake_author("Alex", 777), "", message_id=1,
                          attachments=[fake_attachment("meme.png", make_png(), "image/png")])
    trigger = fake_message(fake_author("Kruskal"), "/explain this", message_id=2)
    _channel_with(posted, trigger)

    await processor.process_message(trigger)

    _, _, attached, images = _ai_call(ai_handler)
    assert "POSTED IN THIS CHANNEL RIGHT BEFORE THEIR MESSAGE" in attached
    assert "posted by Alex:" in attached and "image: meme.png" in attached
    assert len(images) == 1


async def test_just_posted_picture_is_left_alone_where_the_channel_switched_it_off(pipeline, channel_settings):
    processor, _, _, _, ai_handler, bot = pipeline
    posted = fake_message(fake_author("Alex", 777), "", message_id=1,
                          attachments=[fake_attachment("meme.png", make_png(), "image/png")])
    trigger = fake_message(fake_author("Kruskal"), "/explain this", message_id=2)
    _channel_with(posted, trigger)
    channel_settings.set_reads_recent_posts(trigger.channel.id, False)

    await processor.process_message(trigger)

    _, history, attached, images = _ai_call(ai_handler)
    assert attached == "" and images == []
    assert "[attached: meme.png]" in history  # the filename still shows in the chat history
    posted.attachments[0].read.assert_not_called()


async def test_linked_message_attachment_is_read(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    notes = fake_attachment("notes.md", b"# Trip plan\n\nleave at 6am", "text/markdown")
    linked = fake_message(fake_author("Alex", 777), "the plan", message_id=5, attachments=[notes])
    elsewhere = fake_channel([linked], channel_id=4242)
    bot.get_channel = lambda channel_id: elsewhere if channel_id == 4242 else None
    trigger = fake_message(fake_author("Kruskal"), "/what time do we leave https://discord.com/channels/1/4242/5", message_id=9)
    _channel_with(trigger)

    await processor.process_message(trigger)

    query, _, attached, _ = _ai_call(ai_handler)
    assert 'Alex said: "the plan [notes.md]"' in query
    assert "MESSAGE THEY LINKED (sent by Alex)" in attached
    assert "leave at 6am" in attached


# ── /tldr ─────────────────────────────────────────────────────────────────────

async def test_tldr_sums_up_the_channel(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    bot.ai_handler = ai_handler
    processor.command_handler.bot = bot
    ai_handler.generate_reply = AsyncMock(return_value=Reply("alex wants lunch, sam said no"))
    older = [
        fake_message(fake_author("Alex", 777), "lunch at 12?", message_id=1),
        fake_message(fake_author("Sam", 888), "cant, meeting", message_id=2),
    ]
    trigger = fake_message(fake_author("Kruskal"), "/tldr", message_id=3)
    _channel_with(*older, trigger)

    await processor.process_message(trigger)

    query, _, attached, _ = _ai_call(ai_handler)
    assert "tldr" in query
    assert "LAST 2 MESSAGES IN THIS CHANNEL" in attached
    assert attached.index("Alex: lunch at 12?") < attached.index("Sam: cant, meeting")
    assert "/tldr" not in attached
    assert attached.rstrip().endswith("keep it shorter than the chat it covers.)")
    # a plain recap is text only
    assert ai_handler.generate_reply.call_args[1]["allow_files"] is False
    trigger.reply.assert_called_once()
    assert trigger.reply.call_args[0][0] == "alex wants lunch, sam said no"


async def test_tldr_shows_the_ai_the_images_posted_in_the_channel(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    bot.ai_handler = ai_handler
    processor.command_handler.bot = bot
    shot = fake_attachment("error.png", make_png(), "image/png")
    older = [fake_message(fake_author("Alex", 777), "anyone seen this before", message_id=1, attachments=[shot])]
    trigger = fake_message(fake_author("Kruskal"), "/tldr 10", message_id=2)
    _channel_with(*older, trigger)

    await processor.process_message(trigger)

    _, _, attached, images = _ai_call(ai_handler)
    assert "Alex: anyone seen this before [attached: error.png (image 1)]" in attached
    assert len(images) == 1 and images[0].startswith("data:image/jpeg;base64,")


async def test_tldr_with_a_question_answers_it_from_the_channel(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    bot.ai_handler = ai_handler
    processor.command_handler.bot = bot
    ai_handler.generate_reply = AsyncMock(return_value=Reply("postgres, sam already runs it"))
    older = [fake_message(fake_author("Alex", 777), f"option {n}", message_id=n) for n in range(1, 9)]
    trigger = fake_message(fake_author("Kruskal"), "/tldr 6 what is the best option?", message_id=9)
    _channel_with(*older, trigger)

    await processor.process_message(trigger)

    query, _, attached, _ = _ai_call(ai_handler)
    assert query == "what is the best option?"
    assert "LAST 6 MESSAGES IN THIS CHANNEL" in attached
    assert "option 3" in attached and "option 2" not in attached
    assert ai_handler.generate_reply.call_args[1]["allow_files"] is True
    assert trigger.reply.call_args[0][0] == "postgres, sam already runs it"


async def test_tldr_request_can_come_back_with_a_file(pipeline):
    from src.document_builder import BuiltFile
    processor, _, _, _, ai_handler, bot = pipeline
    bot.ai_handler = ai_handler
    processor.command_handler.bot = bot
    ai_handler.generate_reply = AsyncMock(return_value=Reply("here u go", [BuiltFile("design.pdf", b"%PDF")]))
    older = [fake_message(fake_author("Alex", 777), "use a queue between the api and the worker", message_id=1)]
    trigger = fake_message(fake_author("Kruskal"), "/tldr 55 make a pdf of\nthe design discussed here", message_id=2)
    _channel_with(*older, trigger)

    await processor.process_message(trigger)

    query, _, attached, _ = _ai_call(ai_handler)
    assert query == "make a pdf of\nthe design discussed here"
    assert "use a queue between the api and the worker" in attached
    assert [f.filename for f in trigger.reply.call_args[1]["files"]] == ["design.pdf"]


async def test_tldr_file_requests_count_against_the_file_limit(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    bot.ai_handler = ai_handler
    processor.command_handler.bot = bot
    older = [fake_message(fake_author("Alex", 777), "lunch at 12?", message_id=1)]
    trigger = fake_message(fake_author("Kruskal"), "/tldr 20 make a pdf of this", message_id=2)
    _channel_with(*older, trigger)

    await processor.process_message(trigger)

    limiter = processor.command_handler.rate_limiter
    ai_handler.generate_reply.call_args[1]["file_gate"]()
    assert len(limiter._history[str(trigger.author.id)]["file"]) == 1


async def test_tldr_as_a_reply_sums_up_that_message_instead(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    bot.ai_handler = ai_handler
    processor.command_handler.bot = bot
    target = fake_message(fake_author("Alex", 777), "a very long story about my weekend", message_id=1)
    trigger = fake_message(fake_author("Kruskal"), "/tldr", message_id=2, reference=reply_to(target))
    _channel_with(target, trigger)

    await processor.process_message(trigger)

    _, _, attached, _ = _ai_call(ai_handler)
    assert "MESSAGE THEY REPLIED TO (sent by Alex)" in attached
    assert "a very long story about my weekend" in attached


async def test_tldr_without_a_count_reads_the_default_number_of_messages(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    bot.ai_handler = ai_handler
    processor.command_handler.bot = bot
    older = [fake_message(fake_author("Alex", 777), f"msg {n}", message_id=n) for n in range(1, 61)]
    trigger = fake_message(fake_author("Kruskal"), "/tldr who is bringing what", message_id=61)
    _channel_with(*older, trigger)

    await processor.process_message(trigger)

    query, _, attached, _ = _ai_call(ai_handler)
    assert query == "who is bringing what"
    assert "LAST 50 MESSAGES IN THIS CHANNEL" in attached


async def test_tldr_number_inside_a_word_is_part_of_the_request(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    bot.ai_handler = ai_handler
    processor.command_handler.bot = bot
    older = [fake_message(fake_author("Alex", 777), "the 3d printer jammed", message_id=1)]
    trigger = fake_message(fake_author("Kruskal"), "/tldr 3d printer status", message_id=2)
    _channel_with(*older, trigger)

    await processor.process_message(trigger)

    query, _, _, _ = _ai_call(ai_handler)
    assert query == "3d printer status"


# ── typed /chat, typed commands, limits and files ─────────────────────────────

async def test_typed_chat_reads_the_replied_image_and_drops_the_word_chat(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    image = fake_attachment("proof.png", PNG_BYTES, "image/png")
    target = fake_message(fake_author("Kruskal"), "", message_id=1, attachments=[image])
    trigger = fake_message(fake_author("Kruskal"), "/chat what is this", message_id=2, reference=reply_to(target))
    _channel_with(target, trigger)

    await processor.process_message(trigger)

    query, _, attached, images = _ai_call(ai_handler)
    assert query == "/what is this"
    assert "image: proof.png" in attached
    assert len(images) == 1
    trigger.channel.send.assert_not_called()


async def test_bare_typed_chat_points_the_ai_at_the_reply(pipeline):
    from src.message_processor import NO_TEXT
    processor, _, _, _, ai_handler, bot = pipeline
    target = fake_message(fake_author("Alex", 777), "E = mc^2", message_id=1)
    trigger = fake_message(fake_author("Kruskal"), "/chat", message_id=2, reference=reply_to(target))
    _channel_with(target, trigger)

    await processor.process_message(trigger)

    query, _, attached, _ = _ai_call(ai_handler)
    assert query == NO_TEXT
    assert "E = mc^2" in attached


async def test_words_outside_the_typed_command_list_are_chat(pipeline):
    processor, _, gm, _, ai_handler, bot = pipeline
    processor.text_commands = {"tldr", "usage"}  # how the real bot is wired
    trigger = fake_message(fake_author("Kruskal"), "/stop being weird", message_id=1)
    _channel_with(trigger)

    await processor.process_message(trigger)

    query, _, _, _ = _ai_call(ai_handler)
    assert query == "/stop being weird"
    trigger.channel.send.assert_not_called()


async def test_typed_usage_reports_the_budget(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    processor.text_commands = {"tldr", "usage"}
    trigger = fake_message(fake_author("Kruskal"), "/usage", message_id=1)
    _channel_with(trigger)

    await processor.process_message(trigger)

    ai_handler.generate_reply.assert_not_called()
    sent = trigger.channel.send.call_args[0][0]
    assert "ai budget" in sent and "resets on" in sent


async def test_typed_tldr_works_with_the_real_wiring(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    processor.text_commands = {"tldr", "usage"}
    bot.ai_handler = ai_handler
    processor.command_handler.bot = bot
    older = [fake_message(fake_author("Alex", 777), "lunch at 12?", message_id=1)]
    trigger = fake_message(fake_author("Kruskal"), "/tldr 20", message_id=2)
    _channel_with(*older, trigger)

    await processor.process_message(trigger)

    _, _, attached, _ = _ai_call(ai_handler)
    assert "LAST 1 MESSAGES IN THIS CHANNEL" in attached


async def test_typed_help_shows_the_menu_without_calling_the_ai(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    processor.text_commands = {"tldr", "usage", "help"}  # how the real bot is wired
    trigger = fake_message(fake_author("Kruskal"), "/help", message_id=1)
    _channel_with(trigger)

    await processor.process_message(trigger)

    ai_handler.generate_reply.assert_not_called()
    embed = trigger.channel.send.call_args[1]["embed"]
    assert any("limits" in field.name for field in embed.fields)


async def test_help_with_more_words_is_chat(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    processor.text_commands = {"tldr", "usage", "help"}
    trigger = fake_message(fake_author("Kruskal"), "/help me write an email to my landlord", message_id=1)
    _channel_with(trigger)

    await processor.process_message(trigger)

    query, _, _, _ = _ai_call(ai_handler)
    assert query == "/help me write an email to my landlord"


async def test_chat_rate_limit_stops_calls_to_the_ai(pipeline):
    from src.rate_limiter import RateLimiter
    processor, _, _, _, ai_handler, bot = pipeline
    limiter = RateLimiter()
    limiter._limits = {"chat": (2, 600), "file": (1, 600), "default": (2, 600)}
    processor.rate_limiter = limiter

    last = None
    for message_id in (1, 2, 3):
        last = fake_message(fake_author("Kruskal"), f"/msg {message_id}", message_id=message_id)
        _channel_with(last)
        await processor.process_message(last)

    assert ai_handler.generate_reply.call_count == 2
    assert "slow down" in last.channel.send.call_args[0][0]
    last.reply.assert_not_called()


async def test_ai_is_allowed_to_make_files_behind_the_file_limit(pipeline):
    from src.exceptions import RateLimitError
    from src.rate_limiter import RateLimiter
    import pytest
    processor, _, _, _, ai_handler, bot = pipeline
    limiter = RateLimiter()
    limiter._limits = {"chat": (50, 600), "file": (1, 600), "default": (2, 600)}
    processor.rate_limiter = limiter
    trigger = fake_message(fake_author("Kruskal"), "/make me a pdf", message_id=1)
    _channel_with(trigger)

    await processor.process_message(trigger)

    kwargs = ai_handler.generate_reply.call_args[1]
    assert kwargs["allow_files"] is True
    kwargs["file_gate"]()                 # first file passes
    with pytest.raises(RateLimitError):   # second one is over the limit
        kwargs["file_gate"]()


async def test_generated_files_are_uploaded_with_the_reply(pipeline):
    from src.document_builder import BuiltFile
    processor, _, _, _, ai_handler, bot = pipeline
    ai_handler.generate_reply = AsyncMock(return_value=Reply("here u go", [BuiltFile("proposal.pdf", b"%PDF-1.7 data")]))
    trigger = fake_message(fake_author("Kruskal"), "/write a proposal as a pdf", message_id=1)
    _channel_with(trigger)

    await processor.process_message(trigger)

    assert trigger.reply.call_args[0][0] == "here u go"
    uploads = trigger.reply.call_args[1]["files"]
    assert [upload.filename for upload in uploads] == ["proposal.pdf"]
    assert uploads[0].fp.read() == b"%PDF-1.7 data"


async def test_plain_replies_send_no_files_argument(pipeline):
    processor, _, _, _, ai_handler, bot = pipeline
    trigger = fake_message(fake_author("Kruskal"), "/yo", message_id=1)
    _channel_with(trigger)

    await processor.process_message(trigger)

    assert "files" not in trigger.reply.call_args[1]
