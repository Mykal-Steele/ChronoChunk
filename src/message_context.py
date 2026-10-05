"""Reads a Discord message's text, embeds and attachments so the AI can see them."""
import asyncio
import base64
import io
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
# Pictures taken out of PDFs have their own limit per request. They are shrunk before
# they are sent, so ten of them are still a small upload.
MAX_PDF_IMAGES = 10
MAX_FILES = 4
MAX_EMBED_CHARS = 1500
PDF_TIMEOUT_SECONDS = 30

# Pictures taken out of a PDF or a channel recap are shrunk to this before they are sent.
# The model scales anything larger down itself, so more pixels only make the upload bigger.
IMAGE_MAX_SIDE = 1568
# A picture with more pixels than this is skipped before it is decoded. The VM has 1 GiB of RAM.
MAX_DECODE_PIXELS = 40_000_000
# Pictures in a PDF smaller than this on a side are logos, icons and rules
MIN_PDF_IMAGE_SIDE = 120

# /tldr loads the newest pictures and files in the messages it reads, within these limits
TLDR_MAX_IMAGES = 6
TLDR_MAX_FILES = 3
TLDR_FILE_CHARS = 6000

# A plain message with no reply and no attachment still gets what was posted just before
# it, so "this" or "that pic" works without hitting reply. These keep that cheap.
RECENT_POSTS = 5
RECENT_POST_MINUTES = 30
RECENT_MAX_IMAGES = 2
RECENT_MAX_FILES = 1
RECENT_FILE_CHARS = 12000

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


@dataclass
class _ImageBudget:
    """How many more images one request may load. It is used up as attachments are read."""
    images: int      # pictures attached directly
    pdf_images: int  # pictures taken out of PDFs


def _items(value) -> list:
    """Return a Discord list field as a real list, treating anything else as empty."""
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(message) -> str:
    """A message's text with mentions shown as names (@Alex) instead of raw ids."""
    clean = getattr(message, "clean_content", None)
    if isinstance(clean, str):
        return clean.strip()
    return message.content.strip() if isinstance(message.content, str) else ""


def history_text(message, labels: Optional[dict] = None) -> str:
    """
    One-line version of a message for the channel history: its text plus what came
    with it. labels maps id(attachment) to the wording for an attachment that was
    loaded, in place of its bare filename.
    """
    parts = []
    content = _text(message)
    if content:
        parts.append(content)

    filenames = [(labels or {}).get(id(a), a.filename) for a in _items(message.attachments)]
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


def _content_type(attachment) -> str:
    return (attachment.content_type or "").split(";")[0].strip().lower()


def _jpeg_data_url(picture) -> str:
    """Shrink a PIL image to what the model can use and return it as a JPEG data URL."""
    from PIL import Image
    # A JPEG that is not decoded yet can be read at a fraction of its size, which saves memory
    picture.draft("RGB", (IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
    if picture.width * picture.height > MAX_DECODE_PIXELS:
        raise ValueError(f"it is too large, {picture.width} by {picture.height} pixels")
    if picture.mode in ("RGBA", "LA", "P"):
        # JPEG has no transparency, so see-through parts become white
        picture = picture.convert("RGBA")
        flat = Image.new("RGB", picture.size, "white")
        flat.paste(picture, mask=picture.getchannel("A"))
        picture = flat
    else:
        picture = picture.convert("RGB")
    picture.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
    buffer = io.BytesIO()
    picture.save(buffer, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _image_data_url(data: bytes) -> str:
    """Open the bytes of a picture and return it shrunk, as a JPEG data URL."""
    from PIL import Image
    with Image.open(io.BytesIO(data)) as picture:
        return _jpeg_data_url(picture)


def _pdf_pictures(reader) -> list:
    """
    List the pictures in a PDF that are worth showing the model, as (page, key)
    pairs, without decoding any of them. Logos and icons are left out, and a
    picture repeated on several pages is listed once.
    """
    found = []
    seen = set()
    for number in range(min(len(reader.pages), MAX_PDF_PAGES)):
        page = reader.pages[number]
        try:
            keys = page.images.keys()
        except Exception:
            continue
        for key in keys:
            try:
                # A list key is a path through forms nested in the page
                node = page
                for name in ([key] if isinstance(key, str) else key):
                    node = node["/Resources"]["/XObject"][name].get_object()
                width, height = int(node["/Width"]), int(node["/Height"])
            except Exception:
                continue  # an inline image or a broken entry
            reference = getattr(node.indirect_reference, "idnum", None)
            if reference in seen or node.get("/ImageMask"):
                continue
            if min(width, height) < MIN_PDF_IMAGE_SIDE or width * height > MAX_DECODE_PIXELS:
                continue
            if reference is not None:
                seen.add(reference)
            found.append((page, key))
    return found


def _read_pdf(data: bytes, image_budget: int = 0, max_chars: Optional[int] = None) -> Tuple[str, int, List[str], int]:
    """
    Pull the text and the pictures out of a PDF. Returns the text, the total page
    count, up to image_budget pictures as data URLs, and how many pictures it has.
    A scanned PDF has no text and one picture per page.
    """
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("it is password protected")

    max_chars = max_chars or MAX_FILE_CHARS
    total_pages = len(reader.pages)
    parts = []
    chars = 0
    for number in range(min(total_pages, MAX_PDF_PAGES)):
        page_text = (reader.pages[number].extract_text() or "").strip()
        if page_text:
            parts.append(f"[page {number + 1}]\n{page_text}")
            chars += len(page_text)
        if chars >= max_chars:
            break

    pictures = _pdf_pictures(reader)
    images = []
    for page, key in pictures:
        if len(images) >= image_budget:
            break
        try:
            images.append(_jpeg_data_url(page.images[key].image))
        except Exception as e:
            logger.warning(f"Could not read a picture in a pdf: {e!r}")
    return "\n\n".join(parts), total_pages, images, len(pictures)


def _file_block(filename: str, text: str, detail: str = "", max_chars: Optional[int] = None) -> List[str]:
    """Wrap a file's text for the prompt, cutting it off at the size limit."""
    max_chars = max_chars or MAX_FILE_CHARS
    note = f" ({detail})" if detail else ""
    lines = [f"file: {filename}{note}"]
    if len(text) > max_chars:
        lines.append(f"(only the first {max_chars} of {len(text)} characters are shown below, the rest is cut off)")
        text = text[:max_chars]
    lines.append(f"--- start of {filename} ---")
    lines.append(text)
    lines.append(f"--- end of {filename} ---")
    return lines


async def _read_attachment(attachment, budget: _ImageBudget,
                           max_chars: Optional[int] = None) -> Tuple[List[str], List[str]]:
    """
    Read one attachment. Returns prompt lines and the images that go with them as
    data URLs: the attachment itself when it is an image, or the pictures inside
    it when it is a PDF. The images it returns are taken off budget.
    """
    filename = attachment.filename or "file"
    content_type = _content_type(attachment)
    kind = _attachment_kind(filename, content_type)
    size_mb = (attachment.size or 0) / (1024 * 1024)

    if kind == "unsupported":
        return [f"file: {filename} (could not be read: this file type is not supported)"], []
    if kind == "image" and budget.images < 1:
        return [f"image: {filename} (not loaded: only {MAX_IMAGES} images are read per message)"], []

    limit = MAX_IMAGE_BYTES if kind == "image" else MAX_FILE_BYTES
    if (attachment.size or 0) > limit:
        label = "image" if kind == "image" else "file"
        return [f"{label}: {filename} (could not be read: it is {size_mb:.1f} MB and the limit is {limit // (1024 * 1024)} MB)"], []

    try:
        data = await attachment.read()
    except discord.HTTPException as e:
        logger.warning(f"Could not download attachment {filename}: {e}")
        return [f"file: {filename} (could not be read: the download from discord failed)"], []

    if kind == "image":
        mime = IMAGE_TYPES.get(os.path.splitext(filename)[1].lower(), content_type)
        encoded = base64.b64encode(data).decode("ascii")
        budget.images -= 1
        return [f"image: {filename} (attached to this prompt as an image, look at it)"], [f"data:{mime};base64,{encoded}"]

    if kind == "pdf":
        try:
            text, pages, pictures, found = await asyncio.wait_for(
                asyncio.to_thread(_read_pdf, data, budget.pdf_images, max_chars), PDF_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return [f"file: {filename} (could not be read: the pdf took too long to open)"], []
        except Exception as e:
            logger.warning(f"Could not read pdf {filename}: {e}")
            return [f"file: {filename} (could not be read: {e})"], []

        budget.pdf_images -= len(pictures)
        detail = f"pdf, {pages} pages"
        if found == 1 and pictures:
            detail += ", the picture in it is attached to this prompt as an image, look at it"
        elif pictures:
            which = f"all {found}" if len(pictures) == found else f"the first {len(pictures)} of the {found}"
            detail += f", {which} pictures in it are attached to this prompt as images, look at them"
        elif found:
            detail += f", it has pictures that were not loaded: only {MAX_PDF_IMAGES} pictures from pdfs are read per message"
        if text:
            return _file_block(filename, text, detail, max_chars), pictures
        if pictures:
            return [f"file: {filename} ({detail}. it has no selectable text, so read it from the images)"], pictures
        if found:
            return [f"file: {filename} (pdf with {pages} pages but no selectable text, and its pictures were not loaded: "
                    f"only {MAX_PDF_IMAGES} pictures from pdfs are read per message)"], []
        return [f"file: {filename} (pdf with {pages} pages but no selectable text, it is probably scanned images)"], []

    return _file_block(filename, data.decode("utf-8", errors="replace"), max_chars=max_chars), []


async def _read_attachments(attachments: list, budget: _ImageBudget) -> MessageContext:
    """Read a message's attachments, taking as many images as budget has left."""
    lines = []
    images = []
    for attachment in attachments[:MAX_FILES]:
        file_lines, file_images = await _read_attachment(attachment, budget)
        lines.extend(file_lines)
        images.extend(file_images)
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


async def _read_message(message, budget: _ImageBudget) -> MessageContext:
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

    files = await _read_attachments(attachments, budget)
    if files.text:
        lines.append(files.text)
    return MessageContext("\n".join(lines), files.images)


async def read_own_attachments(attachments: list, budget: _ImageBudget) -> MessageContext:
    """Read the files someone attached to their own message into a labelled block."""
    own = await _read_attachments(_items(attachments), budget)
    if not own.text:
        return MessageContext()
    return MessageContext(
        "=== FILES THEY ATTACHED TO THIS MESSAGE ===\n"
        f"{own.text}\n"
        "=== END OF ATTACHED FILES ===",
        own.images,
    )


async def _channel_image(attachment) -> Optional[str]:
    """Download a picture posted in the channel and shrink it. None when it cannot be used."""
    if (attachment.size or 0) > MAX_IMAGE_BYTES:
        return None
    try:
        return await asyncio.to_thread(_image_data_url, await attachment.read())
    except Exception as e:
        logger.warning(f"Could not load channel image {attachment.filename}: {e!r}")
        return None


async def read_channel_messages(messages: list) -> MessageContext:
    """
    Turn a run of channel messages, oldest first, into one block for the AI. The
    newest pictures and files in them are loaded as well, so it sees what people
    posted and not only the filenames. Empty when there is nothing to read.
    """
    pictures = {}  # id(attachment) -> data URL
    files = {}     # id(attachment) -> (prompt lines, pictures from inside a pdf)
    budget, files_left = _ImageBudget(TLDR_MAX_IMAGES, MAX_PDF_IMAGES), TLDR_MAX_FILES
    # Newest first, so the limits go to what was posted last
    for message in reversed(messages):
        for attachment in _items(message.attachments):
            kind = _attachment_kind(attachment.filename or "file", _content_type(attachment))
            if kind == "image" and budget.images > 0:
                url = await _channel_image(attachment)
                if url:
                    pictures[id(attachment)] = url
                    budget.images -= 1
            elif kind in ("pdf", "text") and files_left > 0:
                files[id(attachment)] = await _read_attachment(attachment, budget, TLDR_FILE_CHARS)
                files_left -= 1

    lines = []
    images = []
    file_blocks = []
    pdf_pictures = []
    for message in messages:
        labels = {}
        for attachment in _items(message.attachments):
            if id(attachment) in pictures:
                images.append(pictures[id(attachment)])
                labels[id(attachment)] = f"{attachment.filename} (image {len(images)})"
            elif id(attachment) in files:
                file_lines, inside = files[id(attachment)]
                file_blocks.append(f"sent by {message.author.display_name}:\n" + "\n".join(file_lines))
                pdf_pictures.extend(inside)
                labels[id(attachment)] = f"{attachment.filename} (opened below)"
        text = history_text(message, labels)
        if text:
            lines.append(f"{message.author.display_name}: {text}")

    if not lines:
        return MessageContext()
    block = (
        f"=== LAST {len(lines)} MESSAGES IN THIS CHANNEL, OLDEST FIRST ===\n"
        + "\n".join(lines)
        + "\n=== END OF CHANNEL MESSAGES ==="
    )
    if file_blocks:
        block += (
            "\n\n=== FILES POSTED IN THOSE MESSAGES ===\n"
            + "\n\n".join(file_blocks)
            + "\n=== END OF POSTED FILES ==="
        )
    # Pictures from inside pdfs go after the channel images, which are numbered in the text
    return MessageContext(block, images + pdf_pictures)


MESSAGE_LINK = re.compile(r'https://discord\.com/channels/(\d+)/(\d+)/(\d+)')


async def resolve_message_link(bot, content: str) -> Tuple[str, Optional[discord.Message]]:
    """
    Detect a Discord message link in content, fetch it, and return the content with
    the linked message written in, plus that message so its files and images can be
    read. On fetch failure, appends a note so the AI can react naturally to both the
    user's text and the broken link.
    """
    match = MESSAGE_LINK.search(content)
    if not match:
        return content, None

    _, channel_id_str, message_id_str = match.groups()
    try:
        channel = bot.get_channel(int(channel_id_str))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id_str))
        ref_msg = await channel.fetch_message(int(message_id_str))

        author = ref_msg.author.display_name
        ref_content = ref_msg.content or ""
        if ref_msg.attachments:
            filenames = ", ".join(a.filename for a in ref_msg.attachments)
            ref_content = (ref_content + f" [{filenames}]").strip() if ref_content else f"[{filenames}]"
        if not ref_content:
            ref_content = "[no text]"

        embedded = f'[message already fetched — {author} said: "{ref_content}"]'
        return MESSAGE_LINK.sub(embedded, content, count=1), ref_msg

    except Exception as e:
        logger.warning(f"Could not fetch message link: {e}")
        fail_note = (
            "\n(heads up: user shared a discord link but it couldnt be loaded —"
            " react to whatever else they said and drop naturally that u cant see the link, stay in ur personality)"
        )
        return content + fail_note, None


def _sender(message, bot_user_id: Optional[int]) -> str:
    is_own = bot_user_id is not None and message.author.id == bot_user_id
    return "YOU (ChronoChunk)" if is_own else message.author.display_name


async def read_recent_posts(messages: list, bot_user_id: Optional[int] = None, before=None) -> MessageContext:
    """
    Load the pictures and files posted in the channel just before a message, for
    when someone says "this" or "that pic" without replying to anything. messages
    is the recent channel history, oldest first. before is the message being
    answered, when there is one.
    """
    earlier = [m for m in _items(messages) if before is None or m.id < before.id][-RECENT_POSTS:]
    now = getattr(before, "created_at", None)
    now = now if isinstance(now, datetime) else datetime.now(timezone.utc)

    posts = []
    budget, files_left = _ImageBudget(RECENT_MAX_IMAGES, MAX_PDF_IMAGES), RECENT_MAX_FILES
    # Newest first, so the limits go to what was posted last
    for message in reversed(earlier):
        posted_at = getattr(message, "created_at", None)
        if isinstance(posted_at, datetime) and now - posted_at > timedelta(minutes=RECENT_POST_MINUTES):
            break
        lines = []
        pictures = []
        for attachment in _items(message.attachments):
            kind = _attachment_kind(attachment.filename or "file", _content_type(attachment))
            if kind == "image" and budget.images > 0:
                url = await _channel_image(attachment)
                if url:
                    lines.append(f"image: {attachment.filename} (attached to this prompt as an image, look at it)")
                    pictures.append(url)
                    budget.images -= 1
            elif kind in ("pdf", "text") and files_left > 0:
                file_lines, inside = await _read_attachment(attachment, budget, RECENT_FILE_CHARS)
                lines.extend(file_lines)
                pictures.extend(inside)
                files_left -= 1
        if lines:
            posts.append((f"posted by {_sender(message, bot_user_id)}:\n" + "\n".join(lines), pictures))

    if not posts:
        return MessageContext()
    posts.reverse()  # back to oldest first, the order the images are attached in
    return MessageContext(
        "=== POSTED IN THIS CHANNEL RIGHT BEFORE THEIR MESSAGE ===\n"
        + "\n".join(text for text, _ in posts)
        + "\n=== END OF RECENT POSTS ===",
        [url for _, pictures in posts for url in pictures],
    )


async def gather_context(own_attachments: list, referenced=None, bot_user_id: Optional[int] = None,
                         linked=None, recent: Optional[list] = None, before=None) -> MessageContext:
    """
    Gather what a request points at: the message it replies to, a message it links
    to, and the files attached to it, each with its files and images. When it points
    at nothing, what was posted in the channel just before it (recent, oldest first)
    is loaded instead. Returns an empty context when there is nothing at all.
    """
    blocks = []
    images = []
    budget = _ImageBudget(MAX_IMAGES, MAX_PDF_IMAGES)

    if referenced is not None:
        replied = await _read_message(referenced, budget)
        images.extend(replied.images)
        blocks.append(
            f"=== MESSAGE THEY REPLIED TO (sent by {_sender(referenced, bot_user_id)}) ===\n"
            f"{replied.text}\n"
            "=== END OF REPLIED-TO MESSAGE ==="
        )

    # The text of a linked message is already written into the query. This adds what came with it.
    if linked is not None and (_items(linked.attachments) or _items(getattr(linked, "message_snapshots", None))):
        read = await _read_message(linked, budget)
        images.extend(read.images)
        blocks.append(
            f"=== MESSAGE THEY LINKED (sent by {_sender(linked, bot_user_id)}) ===\n"
            f"{read.text}\n"
            "=== END OF LINKED MESSAGE ==="
        )

    own = await read_own_attachments(own_attachments, budget)
    if own.text:
        images.extend(own.images)
        blocks.append(own.text)

    if not blocks and recent:
        return await read_recent_posts(recent, bot_user_id, before)
    return MessageContext("\n\n".join(blocks), images)


async def build_attached_context(message, referenced=None, bot_user_id: Optional[int] = None,
                                 linked=None, recent: Optional[list] = None) -> MessageContext:
    """gather_context for a typed message: its own attachments, and itself as the cut-off for recent posts."""
    return await gather_context(message.attachments, referenced, bot_user_id, linked, recent, before=message)
