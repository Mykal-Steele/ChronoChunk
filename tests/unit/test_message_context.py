"""Unit tests for message_context: reading a message's text, embeds and attachments."""
from unittest.mock import AsyncMock, MagicMock

import discord

from src import message_context
from src.message_context import build_attached_context, history_text
from tests.fakes import fake_attachment, fake_author, fake_embed, fake_message, make_pdf

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
