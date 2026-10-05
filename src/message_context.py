"""Reads a Discord message's text, embeds and attachments so the AI can see them."""
import asyncio
import base64
import io
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import discord
from pypdf import PdfReader

logger = logging.getLogger(__name__)

# Limits keep one message from blowing up memory, the prompt size or the bill
MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_FILE_CHARS = 30000
MAX_PDF_PAGES = 60
MAX_IMAGES = 3
MAX_FILES = 4
MAX_EMBED_CHARS = 1500
PDF_TIMEOUT_SECONDS = 30

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".log", ".rst", ".tex", ".srt", ".vtt",
    ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml",
    ".html", ".htm", ".css", ".scss", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".py", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".go", ".rs", ".rb",
    ".php", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".sql", ".swift", ".dart", ".lua", ".r",
}

TEXT_CONTENT_TYPES = {"application/json", "application/xml", "application/x-yaml", "application/toml"}


@dataclass
class MessageContext:
    """What the AI gets to read: a text block plus any images as data URLs."""
    text: str = ""
    images: List[str] = field(default_factory=list)


def _items(value) -> list:
    """Return a Discord list field as a real list, treating anything else as empty."""
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(message) -> str:
    """A message's text with mentions shown as names (@Alex) instead of raw ids."""
    clean = getattr(message, "clean_content", None)
    if isinstance(clean, str):
        return clean.strip()
    return message.content.strip() if isinstance(message.content, str) else ""


def history_text(message) -> str:
    """One-line version of a message for the channel history: its text plus what came with it."""
    parts = []
    content = _text(message)
    if content:
        parts.append(content)

    filenames = [a.filename for a in _items(message.attachments)]
    if filenames:
        parts.append(f"[attached: {', '.join(filenames)}]")

    titles = [e.title for e in _items(message.embeds) if e.title]
    if titles:
        parts.append(f"[embed: {', '.join(titles)}]")

    if _items(getattr(message, "message_snapshots", None)):
        parts.append("[forwarded message]")

    return " ".join(parts)


def _attachment_kind(filename: str, content_type: str) -> str:
    """Sort an attachment into image, pdf, text or unsupported."""
    ext = os.path.splitext(filename)[1].lower()
    if ext in IMAGE_TYPES or content_type in IMAGE_TYPES.values():
        return "image"
    if ext == ".pdf" or content_type == "application/pdf":
        return "pdf"
    if ext in TEXT_EXTENSIONS or content_type.startswith("text/") or content_type in TEXT_CONTENT_TYPES:
        return "text"
    return "unsupported"


def _pdf_text(data: bytes) -> Tuple[str, int]:
    """Pull the text out of a PDF. Returns the text and the total page count."""
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("it is password protected")

    total_pages = len(reader.pages)
    parts = []
    chars = 0
    for number in range(min(total_pages, MAX_PDF_PAGES)):
        page_text = (reader.pages[number].extract_text() or "").strip()
        if page_text:
            parts.append(f"[page {number + 1}]\n{page_text}")
            chars += len(page_text)
        if chars >= MAX_FILE_CHARS:
            break
    return "\n\n".join(parts), total_pages


def _file_block(filename: str, text: str, detail: str = "") -> List[str]:
    """Wrap a file's text for the prompt, cutting it off at the size limit."""
    note = f" ({detail})" if detail else ""
    lines = [f"file: {filename}{note}"]
    if len(text) > MAX_FILE_CHARS:
        lines.append(f"(only the first {MAX_FILE_CHARS} of {len(text)} characters are shown below, the rest is cut off)")
        text = text[:MAX_FILE_CHARS]
    lines.append(f"--- start of {filename} ---")
    lines.append(text)
    lines.append(f"--- end of {filename} ---")
    return lines


async def _read_attachment(attachment, allow_image: bool) -> Tuple[List[str], Optional[str]]:
    """Read one attachment. Returns prompt lines and, for an image, its data URL."""
    filename = attachment.filename or "file"
    content_type = (attachment.content_type or "").split(";")[0].strip().lower()
    kind = _attachment_kind(filename, content_type)
    size_mb = (attachment.size or 0) / (1024 * 1024)

    if kind == "unsupported":
        return [f"file: {filename} (could not be read: this file type is not supported)"], None
    if kind == "image" and not allow_image:
        return [f"image: {filename} (not loaded: only {MAX_IMAGES} images are read per message)"], None

    limit = MAX_IMAGE_BYTES if kind == "image" else MAX_FILE_BYTES
    if (attachment.size or 0) > limit:
        label = "image" if kind == "image" else "file"
        return [f"{label}: {filename} (could not be read: it is {size_mb:.1f} MB and the limit is {limit // (1024 * 1024)} MB)"], None

    try:
        data = await attachment.read()
    except discord.HTTPException as e:
        logger.warning(f"Could not download attachment {filename}: {e}")
        return [f"file: {filename} (could not be read: the download from discord failed)"], None

    if kind == "image":
        mime = IMAGE_TYPES.get(os.path.splitext(filename)[1].lower(), content_type)
        encoded = base64.b64encode(data).decode("ascii")
        return [f"image: {filename} (attached to this prompt as an image, look at it)"], f"data:{mime};base64,{encoded}"

    if kind == "pdf":
        try:
            text, pages = await asyncio.wait_for(asyncio.to_thread(_pdf_text, data), PDF_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return [f"file: {filename} (could not be read: the pdf took too long to open)"], None
        except Exception as e:
            logger.warning(f"Could not read pdf {filename}: {e}")
            return [f"file: {filename} (could not be read: {e})"], None
        if not text:
            return [f"file: {filename} (pdf with {pages} pages but no selectable text, it is probably scanned images)"], None
        return _file_block(filename, text, f"pdf, {pages} pages"), None

    return _file_block(filename, data.decode("utf-8", errors="replace")), None


async def _read_attachments(attachments: list, image_budget: int) -> MessageContext:
    """Read a message's attachments, taking at most image_budget images."""
    lines = []
    images = []
    for attachment in attachments[:MAX_FILES]:
        file_lines, image = await _read_attachment(attachment, allow_image=len(images) < image_budget)
        lines.extend(file_lines)
        if image:
            images.append(image)
    if len(attachments) > MAX_FILES:
        skipped = ", ".join(a.filename for a in attachments[MAX_FILES:])
        lines.append(f"(not opened, only {MAX_FILES} files are read per message: {skipped})")
    return MessageContext("\n".join(lines), images)


def _embed_lines(embeds: list) -> List[str]:
    """Describe embeds (link previews, bot cards) as text."""
    lines = []
    for embed in embeds:
        pieces = [p for p in (embed.title, embed.description, embed.url) if isinstance(p, str) and p]
        pieces.extend(f"{f.name}: {f.value}" for f in _items(embed.fields))
        if pieces:
            lines.append(f"embed: {' | '.join(pieces)[:MAX_EMBED_CHARS]}")
    return lines


async def _read_message(message, image_budget: int) -> MessageContext:
    """Read everything in one message: text, embeds, forwarded content and attachments."""
    lines = []
    content = _text(message)
    lines.append(f'text: "{content}"' if content else "text: (none)")
    lines.extend(_embed_lines(_items(message.embeds)))

    attachments = _items(message.attachments)
    # A forwarded message keeps its real content in a snapshot
    for snapshot in _items(getattr(message, "message_snapshots", None)):
        if isinstance(snapshot.content, str) and snapshot.content.strip():
            lines.append(f'forwarded text: "{snapshot.content.strip()}"')
        lines.extend(_embed_lines(_items(snapshot.embeds)))
        attachments.extend(_items(snapshot.attachments))

    files = await _read_attachments(attachments, image_budget)
    if files.text:
        lines.append(files.text)
    return MessageContext("\n".join(lines), files.images)


async def build_attached_context(message, referenced=None, bot_user_id: Optional[int] = None) -> MessageContext:
    """
    Gather what a message points at: the message it replies to (with its files and
    images) and anything attached to the message itself. Returns an empty context
    when there is neither.
    """
    blocks = []
    images = []

    if referenced is not None:
        is_own = bot_user_id is not None and referenced.author.id == bot_user_id
        sender = "YOU (ChronoChunk)" if is_own else referenced.author.display_name
        replied = await _read_message(referenced, MAX_IMAGES)
        images.extend(replied.images)
        blocks.append(
            f"=== MESSAGE THEY REPLIED TO (sent by {sender}) ===\n"
            f"{replied.text}\n"
            "=== END OF REPLIED-TO MESSAGE ==="
        )

    own_attachments = _items(message.attachments)
    if own_attachments:
        own = await _read_attachments(own_attachments, MAX_IMAGES - len(images))
        images.extend(own.images)
        blocks.append(
            "=== FILES THEY ATTACHED TO THIS MESSAGE ===\n"
            f"{own.text}\n"
            "=== END OF ATTACHED FILES ==="
        )

    return MessageContext("\n\n".join(blocks), images)
