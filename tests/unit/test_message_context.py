"""Unit tests for message_context: reading a message's text, embeds and attachments."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import discord

from src import message_context
from src.message_context import build_attached_context, gather_context, history_text, read_channel_messages
from tests.fakes import (
    fake_attachment, fake_author, fake_embed, fake_message, make_pdf, make_picture_pdf, make_png,
)

BOT_ID = 99001
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


# ── history_text ──────────────────────────────────────────────────────────────

def test_history_text_plain_message():
    msg = fake_message(fake_author(), "hey bau")
    assert history_text(msg) == "hey bau"


def test_history_text_keeps_attachment_only_message():
    msg = fake_message(fake_author(), "", attachments=[fake_attachment("proof.png"), fake_attachment("notes.pdf")])
    assert history_text(msg) == "[attached: proof.png, notes.pdf]"


def test_history_text_shows_embed_title():
    msg = fake_message(fake_author(), "", embeds=[fake_embed(title="ChronoChunk Bot Commands")])
    assert history_text(msg) == "[embed: ChronoChunk Bot Commands]"


def test_history_text_empty_message():
    assert history_text(fake_message(fake_author(), "")) == ""


def test_history_text_shows_mentions_as_names():
    msg = fake_message(fake_author(), "<@777> lunch?")
    msg.clean_content = "@Alex lunch?"
    assert history_text(msg) == "@Alex lunch?"


# ── replied-to message ────────────────────────────────────────────────────────

async def test_replied_message_text_and_sender_are_included():
    replied = fake_message(fake_author("Kruskal"), "hey bau")
    trigger = fake_message(fake_author("Kruskal"), "/read what this msg say")
    context = await build_attached_context(trigger, replied, BOT_ID)
    assert "MESSAGE THEY REPLIED TO (sent by Kruskal)" in context.text
    assert 'text: "hey bau"' in context.text
    assert context.images == []


async def test_replied_message_from_the_bot_is_labelled_as_own():
    replied = fake_message(fake_author("ChronoChunk", BOT_ID, bot=True), "its 401")
    trigger = fake_message(fake_author("Kruskal"), "wdym")
    context = await build_attached_context(trigger, replied, BOT_ID)
    assert "sent by YOU (ChronoChunk)" in context.text


async def test_no_reply_and_no_attachments_gives_empty_context():
    context = await build_attached_context(fake_message(fake_author(), "/yo"), None, BOT_ID)
    assert context.text == ""
    assert context.images == []


async def test_embed_text_is_included():
    replied = fake_message(fake_author("Kruskal"), "", embeds=[
        fake_embed(title="Release notes", description="v2 ships friday", url="https://example.com/notes")
    ])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert "Release notes" in context.text
    assert "v2 ships friday" in context.text


async def test_forwarded_message_content_is_included():
    snapshot = MagicMock()
    snapshot.content = "original forwarded text"
    snapshot.embeds = []
    snapshot.attachments = [fake_attachment("fwd.md", b"# forwarded file")]
    replied = fake_message(fake_author("Kruskal"), "")
    replied.message_snapshots = [snapshot]
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert "original forwarded text" in context.text
    assert "# forwarded file" in context.text


# ── text files ────────────────────────────────────────────────────────────────

async def test_markdown_attachment_content_is_read():
    notes = fake_attachment("notes.md", b"# Plan\n\n- ship the bot\n- sleep", "text/markdown; charset=utf-8")
    replied = fake_message(fake_author("Kruskal"), "here are my notes", attachments=[notes])
    context = await build_attached_context(fake_message(fake_author(), "/summarize"), replied, BOT_ID)
    assert "file: notes.md" in context.text
    assert "- ship the bot" in context.text


async def test_code_file_is_read_by_extension_when_discord_gives_no_type():
    script = fake_attachment("main.py", b"print('hello')", content_type=None)
    replied = fake_message(fake_author(), "", attachments=[script])
    context = await build_attached_context(fake_message(fake_author(), "/explain"), replied, BOT_ID)
    assert "print('hello')" in context.text


async def test_long_text_file_is_cut_off_with_a_note(monkeypatch):
    monkeypatch.setattr(message_context, "MAX_FILE_CHARS", 100)
    big = fake_attachment("big.txt", b"A" * 250 + b"TAIL", "text/plain")
    replied = fake_message(fake_author(), "", attachments=[big])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert "only the first 100 of 254 characters" in context.text
    assert "TAIL" not in context.text


# ── pdf files ─────────────────────────────────────────────────────────────────

async def test_pdf_text_is_extracted_with_page_numbers():
    pdf = fake_attachment("report.pdf", make_pdf(["Quarterly revenue was 42 dollars", "Second page here"]), "application/pdf")
    replied = fake_message(fake_author("Kruskal"), "", attachments=[pdf])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert "file: report.pdf (pdf, 2 pages)" in context.text
    assert "[page 1]" in context.text
    assert "Quarterly revenue was 42 dollars" in context.text
    assert "Second page here" in context.text


async def test_pdf_without_text_says_so():
    pdf = fake_attachment("scan.pdf", make_pdf([""]), "application/pdf")
    replied = fake_message(fake_author(), "", attachments=[pdf])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert "scan.pdf" in context.text
    assert "no selectable text" in context.text


async def test_broken_pdf_is_reported_not_raised():
    pdf = fake_attachment("broken.pdf", b"this is not a pdf at all", "application/pdf")
    replied = fake_message(fake_author(), "", attachments=[pdf])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert "broken.pdf (could not be read" in context.text


async def test_scanned_pdf_is_read_from_its_page_pictures():
    scan = fake_attachment("scan.pdf", make_picture_pdf([(850, 1100)] * 2), "application/pdf")
    replied = fake_message(fake_author(), "", attachments=[scan])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert len(context.images) == 2
    assert all(image.startswith("data:image/jpeg;base64,") for image in context.images)
    assert "all 2 pictures in it are attached to this prompt as images" in context.text
    assert "no selectable text, so read it from the images" in context.text


async def test_pdf_pictures_stop_at_their_own_limit():
    limit = message_context.MAX_PDF_IMAGES
    scan = fake_attachment("scan.pdf", make_picture_pdf([(425, 550)] * (limit + 2)), "application/pdf")
    replied = fake_message(fake_author(), "", attachments=[scan])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert limit == 10
    assert len(context.images) == limit
    assert f"the first {limit} of the {limit + 2} pictures" in context.text


async def test_pdf_pictures_do_not_use_up_the_slots_for_attached_images():
    images = [fake_attachment(f"pic{i}.png", PNG_BYTES, "image/png") for i in range(message_context.MAX_IMAGES)]
    scan = fake_attachment("scan.pdf", make_picture_pdf([(425, 550)] * 2), "application/pdf")
    replied = fake_message(fake_author(), "", attachments=[scan, *images])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert len(context.images) == message_context.MAX_IMAGES + 2
    assert "not loaded" not in context.text


async def test_pdf_picture_limit_is_shared_by_every_pdf_in_the_request(monkeypatch):
    monkeypatch.setattr(message_context, "MAX_PDF_IMAGES", 3)
    first = fake_attachment("first.pdf", make_picture_pdf([(425, 550)] * 2), "application/pdf")
    second = fake_attachment("second.pdf", make_picture_pdf([(425, 550)] * 2), "application/pdf")
    third = fake_attachment("third.pdf", make_picture_pdf([(425, 550)]), "application/pdf")
    replied = fake_message(fake_author(), "", attachments=[first])
    trigger = fake_message(fake_author(), "/compare these", attachments=[second, third])
    context = await build_attached_context(trigger, replied, BOT_ID)
    assert len(context.images) == 3
    assert "second.pdf (pdf, 2 pages, the first 1 of the 2 pictures" in context.text
    assert "third.pdf (pdf with 1 pages but no selectable text, and its pictures were not loaded" in context.text


async def test_small_pictures_in_a_pdf_are_skipped():
    # a logo sized picture is not worth an image slot
    logo = fake_attachment("letterhead.pdf", make_picture_pdf([(60, 60)]), "application/pdf")
    replied = fake_message(fake_author(), "", attachments=[logo])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert context.images == []
    assert "pictures" not in context.text


async def test_large_pdf_pictures_are_shrunk(monkeypatch):
    import base64
    import io
    from PIL import Image
    monkeypatch.setattr(message_context, "IMAGE_MAX_SIDE", 500)
    scan = fake_attachment("scan.pdf", make_picture_pdf([(1700, 2200)]), "application/pdf")
    replied = fake_message(fake_author(), "", attachments=[scan])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    picture = Image.open(io.BytesIO(base64.b64decode(context.images[0].split(",", 1)[1])))
    assert max(picture.size) == 500


# ── images ────────────────────────────────────────────────────────────────────

async def test_image_is_returned_as_data_url():
    image = fake_attachment("proof.png", PNG_BYTES, "image/png")
    replied = fake_message(fake_author("Kruskal"), "", attachments=[image])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert len(context.images) == 1
    assert context.images[0].startswith("data:image/png;base64,")
    assert "image: proof.png" in context.text


async def test_images_over_the_limit_are_not_loaded():
    images = [fake_attachment(f"pic{i}.png", PNG_BYTES, "image/png") for i in range(message_context.MAX_IMAGES + 1)]
    replied = fake_message(fake_author(), "", attachments=images)
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert len(context.images) == message_context.MAX_IMAGES
    assert "not loaded" in context.text


async def test_image_budget_is_shared_between_reply_and_own_attachments():
    replied = fake_message(fake_author(), "", attachments=[
        fake_attachment(f"old{i}.png", PNG_BYTES, "image/png") for i in range(message_context.MAX_IMAGES)
    ])
    trigger = fake_message(fake_author(), "/compare", attachments=[fake_attachment("mine.png", PNG_BYTES, "image/png")])
    context = await build_attached_context(trigger, replied, BOT_ID)
    assert len(context.images) == message_context.MAX_IMAGES
    assert "FILES THEY ATTACHED TO THIS MESSAGE" in context.text


# ── own attachments ───────────────────────────────────────────────────────────

async def test_attachment_on_the_users_own_message_is_read():
    trigger = fake_message(fake_author(), "/what is this", attachments=[fake_attachment("todo.txt", b"buy milk", "text/plain")])
    context = await build_attached_context(trigger, None, BOT_ID)
    assert "FILES THEY ATTACHED TO THIS MESSAGE" in context.text
    assert "buy milk" in context.text


# ── files that cannot be read ─────────────────────────────────────────────────

async def test_unsupported_file_type_is_named_and_not_downloaded():
    archive = fake_attachment("stuff.zip", b"PK\x03\x04", "application/zip")
    replied = fake_message(fake_author(), "", attachments=[archive])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert "stuff.zip (could not be read: this file type is not supported)" in context.text
    archive.read.assert_not_called()


async def test_oversized_file_is_not_downloaded():
    huge = fake_attachment("huge.pdf", b"", "application/pdf", size=message_context.MAX_FILE_BYTES + 1)
    replied = fake_message(fake_author(), "", attachments=[huge])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert "huge.pdf (could not be read: it is" in context.text
    huge.read.assert_not_called()


async def test_failed_download_is_reported_not_raised():
    gone = fake_attachment("gone.md", b"", "text/markdown")
    gone.read = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=404, reason="Not Found"), "gone"))
    replied = fake_message(fake_author(), "", attachments=[gone])
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert "gone.md (could not be read: the download from discord failed)" in context.text


async def test_only_the_first_files_are_opened():
    files = [fake_attachment(f"f{i}.txt", f"content {i}".encode(), "text/plain") for i in range(message_context.MAX_FILES + 2)]
    replied = fake_message(fake_author(), "", attachments=files)
    context = await build_attached_context(fake_message(fake_author(), "/read"), replied, BOT_ID)
    assert "content 0" in context.text
    assert f"content {message_context.MAX_FILES}" not in context.text
    assert "not opened" in context.text


# ── channel messages for /tldr ────────────────────────────────────────────────

async def test_channel_messages_are_listed_oldest_first():
    messages = [
        fake_message(fake_author("Alex", 777), "lunch at 12?", message_id=1),
        fake_message(fake_author("Sam", 888), "cant, meeting", message_id=2),
    ]
    context = await read_channel_messages(messages)
    assert context.text.startswith("=== LAST 2 MESSAGES IN THIS CHANNEL, OLDEST FIRST ===\nAlex: lunch at 12?\nSam: cant, meeting")
    assert context.images == []


async def test_channel_images_are_loaded_and_numbered_in_order():
    messages = [
        fake_message(fake_author("Alex", 777), "old layout", message_id=1,
                     attachments=[fake_attachment("before.png", make_png(), "image/png")]),
        fake_message(fake_author("Sam", 888), "new layout", message_id=2,
                     attachments=[fake_attachment("after.jpg", make_png(color="red"), "image/jpeg")]),
    ]
    context = await read_channel_messages(messages)
    assert len(context.images) == 2
    assert "Alex: old layout [attached: before.png (image 1)]" in context.text
    assert "Sam: new layout [attached: after.jpg (image 2)]" in context.text


async def test_channel_image_limit_goes_to_the_newest_images(monkeypatch):
    monkeypatch.setattr(message_context, "TLDR_MAX_IMAGES", 2)
    messages = [
        fake_message(fake_author("Alex", 777), f"shot {n}", message_id=n,
                     attachments=[fake_attachment(f"shot{n}.png", make_png(), "image/png")])
        for n in (1, 2, 3)
    ]
    context = await read_channel_messages(messages)
    assert len(context.images) == 2
    assert "shot 1 [attached: shot1.png]" in context.text
    assert "shot2.png (image 1)" in context.text and "shot3.png (image 2)" in context.text
    messages[0].attachments[0].read.assert_not_called()


async def test_channel_image_that_cannot_be_opened_is_only_named():
    broken = fake_attachment("broken.png", b"not a picture", "image/png")
    context = await read_channel_messages([fake_message(fake_author("Alex", 777), "look", attachments=[broken])])
    assert context.images == []
    assert "Alex: look [attached: broken.png]" in context.text


async def test_channel_files_are_opened_below_the_messages(monkeypatch):
    monkeypatch.setattr(message_context, "TLDR_FILE_CHARS", 40)
    notes = fake_attachment("notes.md", b"# Plan\n\nship on friday. " + b"x" * 100, "text/markdown")
    spec = fake_attachment("spec.pdf", make_pdf(["The api returns json"]), "application/pdf")
    messages = [
        fake_message(fake_author("Alex", 777), "the plan", message_id=1, attachments=[notes]),
        fake_message(fake_author("Sam", 888), "", message_id=2, attachments=[spec]),
    ]
    context = await read_channel_messages(messages)
    assert "Alex: the plan [attached: notes.md (opened below)]" in context.text
    assert "Sam: [attached: spec.pdf (opened below)]" in context.text
    files = context.text.split("=== FILES POSTED IN THOSE MESSAGES ===")[1]
    assert "sent by Alex:\nfile: notes.md" in files and "ship on friday" in files
    assert "only the first 40 of" in files
    assert "sent by Sam:\nfile: spec.pdf (pdf, 1 pages)" in files and "The api returns json" in files


async def test_channel_file_limit_goes_to_the_newest_files(monkeypatch):
    monkeypatch.setattr(message_context, "TLDR_MAX_FILES", 1)
    messages = [
        fake_message(fake_author("Alex", 777), "", message_id=n,
                     attachments=[fake_attachment(f"notes{n}.txt", f"content {n}".encode(), "text/plain")])
        for n in (1, 2)
    ]
    context = await read_channel_messages(messages)
    assert "content 2" in context.text and "content 1" not in context.text
    assert "[attached: notes1.txt]" in context.text


async def test_pictures_inside_a_channel_pdf_come_after_the_channel_images():
    scan = fake_attachment("scan.pdf", make_picture_pdf([(850, 1100)]), "application/pdf")
    photo = fake_attachment("photo.png", make_png(), "image/png")
    messages = [
        fake_message(fake_author("Alex", 777), "the signed form", message_id=1, attachments=[scan]),
        fake_message(fake_author("Sam", 888), "and the receipt", message_id=2, attachments=[photo]),
    ]
    context = await read_channel_messages(messages)
    assert len(context.images) == 2
    assert "photo.png (image 1)" in context.text
    assert "the picture in it is attached to this prompt as an image" in context.text


async def test_channel_with_nothing_to_read_is_empty():
    context = await read_channel_messages([fake_message(fake_author("Alex", 777), "")])
    assert context.text == "" and context.images == []


# ── what was posted just before a message ─────────────────────────────────────

async def test_recent_picture_is_loaded_when_the_message_points_at_nothing():
    posted = fake_message(fake_author("Alex", 777), "", message_id=1,
                          attachments=[fake_attachment("meme.png", make_png(), "image/png")])
    trigger = fake_message(fake_author("Kruskal"), "/explain this", message_id=2)
    context = await build_attached_context(trigger, None, BOT_ID, recent=[posted, trigger])
    assert context.text.startswith("=== POSTED IN THIS CHANNEL RIGHT BEFORE THEIR MESSAGE ===\nposted by Alex:\nimage: meme.png")
    assert len(context.images) == 1


async def test_recent_file_from_the_bot_itself_is_read():
    plan = fake_attachment("plan.pdf", make_pdf(["Ship the queue first"]), "application/pdf")
    posted = fake_message(fake_author("ChronoChunk", BOT_ID), "here u go", message_id=1, attachments=[plan])
    trigger = fake_message(fake_author("Kruskal"), "/make it shorter", message_id=2)
    context = await build_attached_context(trigger, None, BOT_ID, recent=[posted, trigger])
    assert "posted by YOU (ChronoChunk):" in context.text
    assert "Ship the queue first" in context.text


async def test_recent_posts_are_skipped_when_they_replied_to_something():
    posted = fake_message(fake_author("Alex", 777), "", message_id=1,
                          attachments=[fake_attachment("meme.png", make_png(), "image/png")])
    target = fake_message(fake_author("Sam", 888), "the deadline is friday", message_id=2)
    trigger = fake_message(fake_author("Kruskal"), "/what does he mean", message_id=3)
    context = await build_attached_context(trigger, target, BOT_ID, recent=[posted, target, trigger])
    assert "POSTED IN THIS CHANNEL" not in context.text
    assert context.images == []
    posted.attachments[0].read.assert_not_called()


async def test_old_and_far_back_posts_are_not_loaded(monkeypatch):
    monkeypatch.setattr(message_context, "RECENT_POSTS", 2)
    now = datetime.now(timezone.utc)
    far_back = fake_message(fake_author("Alex", 777), "", message_id=1, created_at=now,
                            attachments=[fake_attachment("far.png", make_png(), "image/png")])
    filler = [fake_message(fake_author("Sam", 888), "ok", message_id=n, created_at=now) for n in (2, 3)]
    trigger = fake_message(fake_author("Kruskal"), "/yo", message_id=4, created_at=now)
    context = await build_attached_context(trigger, None, BOT_ID, recent=[far_back, *filler, trigger])
    assert context.text == ""

    stale = fake_message(fake_author("Alex", 777), "", message_id=3,
                         created_at=now - timedelta(minutes=message_context.RECENT_POST_MINUTES + 5),
                         attachments=[fake_attachment("stale.png", make_png(), "image/png")])
    context = await build_attached_context(trigger, None, BOT_ID, recent=[stale, trigger])
    assert context.text == ""


async def test_recent_posts_keep_to_their_limits():
    posts = [
        fake_message(fake_author("Alex", 777), "", message_id=n,
                     attachments=[fake_attachment(f"pic{n}.png", make_png(), "image/png"),
                                  fake_attachment(f"notes{n}.txt", f"content {n}".encode(), "text/plain")])
        for n in (1, 2, 3)
    ]
    trigger = fake_message(fake_author("Kruskal"), "/what is all this", message_id=4)
    context = await build_attached_context(trigger, None, BOT_ID, recent=[*posts, trigger])
    assert len(context.images) == message_context.RECENT_MAX_IMAGES
    assert "pic3.png" in context.text and "pic2.png" in context.text and "pic1.png" not in context.text
    assert "content 3" in context.text and "content 2" not in context.text
    # oldest first, the order the images are attached in
    assert context.text.index("pic2.png") < context.text.index("pic3.png")


async def test_messages_after_the_one_being_answered_are_ignored():
    trigger = fake_message(fake_author("Kruskal"), "/yo", message_id=1)
    later = fake_message(fake_author("Alex", 777), "", message_id=2,
                         attachments=[fake_attachment("later.png", make_png(), "image/png")])
    context = await build_attached_context(trigger, None, BOT_ID, recent=[trigger, later])
    assert context.text == ""


# ── a message they linked to ──────────────────────────────────────────────────

async def test_linked_message_files_and_images_are_read():
    notes = fake_attachment("notes.md", b"# Trip plan\n\nleave at 6am", "text/markdown")
    photo = fake_attachment("map.png", PNG_BYTES, "image/png")
    linked = fake_message(fake_author("Alex", 777), "the plan", message_id=1, attachments=[notes, photo])
    trigger = fake_message(fake_author("Kruskal"), "/sum up that message", message_id=9)
    context = await build_attached_context(trigger, None, BOT_ID, linked=linked)
    assert "=== MESSAGE THEY LINKED (sent by Alex) ===" in context.text
    assert "leave at 6am" in context.text
    assert len(context.images) == 1


async def test_linked_message_with_only_text_adds_no_block():
    # its text is already written into the query
    linked = fake_message(fake_author("Alex", 777), "just words", message_id=1)
    context = await build_attached_context(fake_message(fake_author("Kruskal"), "/read"), None, BOT_ID, linked=linked)
    assert context.text == ""


async def test_context_for_a_slash_command_has_no_message_to_cut_off_at():
    posted = fake_message(fake_author("Alex", 777), "", message_id=1,
                          attachments=[fake_attachment("shot.png", make_png(), "image/png")])
    context = await gather_context([], bot_user_id=BOT_ID, recent=[posted])
    assert "image: shot.png" in context.text and len(context.images) == 1
