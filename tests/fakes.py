"""Fake Discord objects for tests: authors, attachments, messages and channels."""
from datetime import datetime, timezone
from typing import List
from unittest.mock import AsyncMock, MagicMock

import discord


class AsyncIterator:
    """Minimal async iterator for mocking discord channel.history()."""
    def __init__(self, items=None):
        self._items = list(items or [])

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._items:
            return self._items.pop(0)
        raise StopAsyncIteration


def fake_author(name="TestUser", user_id=12345, bot=False):
    author = MagicMock()
    author.id = user_id
    author.name = name
    author.display_name = name
    author.bot = bot
    return author


def fake_attachment(filename, data=b"", content_type=None, size=None):
    attachment = MagicMock()
    attachment.filename = filename
    attachment.content_type = content_type
    attachment.size = len(data) if size is None else size
    attachment.read = AsyncMock(return_value=data)
    return attachment


def fake_embed(title=None, description=None, url=None):
    embed = MagicMock()
    embed.title = title
    embed.description = description
    embed.url = url
    embed.fields = []
    return embed


def fake_message(author, content="", *, message_id=1, attachments=(), embeds=(), mentions=(),
                 reference=None, created_at=None, channel=None):
    msg = MagicMock()
    msg.id = message_id
    msg.author = author
    msg.content = content
    msg.attachments = list(attachments)
    msg.embeds = list(embeds)
    msg.mentions = list(mentions)
    msg.message_snapshots = []
    msg.reference = reference
    msg.created_at = created_at or datetime.now(timezone.utc)
    msg.channel = channel
    msg.guild = None
    msg.reply = AsyncMock()
    return msg


def reply_to(target):
    """A message reference pointing at target, the way Discord sends it without the resolved message."""
    reference = MagicMock()
    reference.message_id = target.id
    reference.resolved = None
    return reference


def fake_channel(history=(), channel_id=99999):
    """A channel holding the given messages, listed oldest first."""
    channel = MagicMock()
    channel.id = channel_id
    channel.send = AsyncMock()
    typing_cm = MagicMock()
    typing_cm.__aenter__ = AsyncMock(return_value=None)
    typing_cm.__aexit__ = AsyncMock(return_value=None)
    channel.typing = MagicMock(return_value=typing_cm)

    messages = list(history)
    for msg in messages:
        msg.channel = channel

    def _history(limit=None, before=None, **kwargs):
        visible = [m for m in messages if before is None or m.id < before.id]
        newest_first = list(reversed(visible))  # Discord returns newest first
        return AsyncIterator(newest_first[:limit])

    async def _fetch_message(message_id):
        for m in messages:
            if m.id == message_id:
                return m
        raise discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Message")

    channel.history = MagicMock(side_effect=_history)
    channel.fetch_message = AsyncMock(side_effect=_fetch_message)
    return channel


def make_pdf(pages: List[str]) -> bytes:
    """Build a small valid PDF with one line of text per page. An empty string makes a blank page."""
    page_ids = [4 + 2 * i for i in range(len(pages))]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_id, text in zip(page_ids, pages):
        stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    return bytes(out)
