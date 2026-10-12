"""
What each custom emoji shows. An emoji's name often says little ("carrotsideway"),
so the bot looks at each one's picture once, has the model write a few words about
it, and keeps them in the database. The chat model is then told the name and the
note, and can pick an emoji that fits.
"""
import asyncio
import io
import logging
import re
import time
from typing import Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from src import usage_context
from src.database import get_database
from src.usage_guard import BudgetExceededError

logger = logging.getLogger(__name__)

# Emoji sent to the model in one request
PER_REQUEST = 10
# Long side of the picture the model sees. An emoji is drawn to be read at a glance.
PICTURE_SIDE = 96
# A note longer than this is cut. It goes into every chat prompt.
MAX_NOTE_CHARS = 70

PROMPT = (
    "These are {count} custom Discord emoji, in this order. Their names are: {names}. For each one write one "
    "line, the number, a colon, then what it shows and the mood people use it for, in at most 8 plain words. "
    "Example: '1: laughing frog, used when something is funny'. No other text."
)

# Set by the bot at startup: describe(prompt, images) -> (text, cost in USD)
Describer = Callable[[str, List[str]], Awaitable[Tuple[str, float]]]
_describe: Optional[Describer] = None

# emoji id -> note, filled from the database on first use
_notes: Optional[Dict[str, str]] = None
# One pass at a time, however many events ask for one
_lock = asyncio.Lock()


def set_describer(describe: Optional[Describer]) -> None:
    global _describe
    _describe = describe


def clear_cache() -> None:
    """Forget the notes held in memory, for tests."""
    global _notes
    _notes = None


def _loaded() -> Dict[str, str]:
    global _notes
    if _notes is None:
        _notes = {row["emoji_id"]: row["note"] for row in get_database().query("SELECT emoji_id, note FROM emoji_notes")}
    return _notes


def note_for(emoji_id) -> str:
    """The note for one emoji, or an empty string when none has been written yet."""
    try:
        return _loaded().get(str(emoji_id), "")
    except Exception as e:
        # A missing note only means the model sees the bare name
        logger.warning(f"Could not read the emoji notes: {e!r}")
        return ""


def _picture(data: bytes) -> str:
    """An emoji's image as a small JPEG data URL. An animated one gives its first frame."""
    import base64

    from PIL import Image
    with Image.open(io.BytesIO(data)) as picture:
        picture.seek(0)
        frame = picture.convert("RGBA")
    flat = Image.new("RGB", frame.size, "white")
    flat.paste(frame, mask=frame.getchannel("A"))
    flat.thumbnail((PICTURE_SIDE, PICTURE_SIDE))
    buffer = io.BytesIO()
    flat.save(buffer, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _clean(note: str) -> str:
    """One short line of plain words. A note is shown to the chat model, so it must not carry anything else."""
    note = re.sub(r"[^\w ,.'!?-]", "", note.replace("\n", " "))
    return re.sub(r"\s+", " ", note).strip(" .,-")[:MAX_NOTE_CHARS].strip()


def _parse(text: str, count: int) -> Dict[int, str]:
    """The numbered lines of the model's answer, as position (from 0) to note."""
    found: Dict[int, str] = {}
    for line in text.splitlines():
        match = re.match(r"\s*(\d+)\s*[:.)]\s*(.+)", line)
        if match and 1 <= int(match.group(1)) <= count:
            note = _clean(match.group(2))
            if note:
                found[int(match.group(1)) - 1] = note
    return found


async def _describe_group(group: list) -> int:
    """Write and store the notes for a few emoji. Returns how many were written."""
    assert _describe is not None
    pictures, kept = [], []
    for emoji in group:
        try:
            pictures.append(await asyncio.to_thread(_picture, await emoji.read()))
            kept.append(emoji)
        except Exception as e:
            logger.warning(f"Could not load the picture of emoji {emoji.name}: {e!r}")
    if not kept:
        return 0
    text, _ = await _describe(PROMPT.format(count=len(kept), names=", ".join(e.name for e in kept)), pictures)
    notes = _parse(text, len(kept))
    now = time.time()
    for position, note in notes.items():
        emoji = kept[position]
        get_database().execute(
            "INSERT INTO emoji_notes(emoji_id, name, note, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(emoji_id) DO UPDATE SET name = excluded.name, note = excluded.note",
            (str(emoji.id), emoji.name, note, now))
        _loaded()[str(emoji.id)] = note
    return len(notes)


async def describe_missing(guilds: Iterable) -> int:
    """
    Write a note for every emoji that has none yet. Runs at startup and when a server
    changes its emoji. Returns how many were written. It never raises: without a note
    an emoji still works, the model just sees its name alone.
    """
    if _describe is None:
        return 0
    async with _lock:
        try:
            usage_context.label("emoji_notes")
            known = _loaded()
            missing = [emoji for guild in guilds for emoji in guild.emojis if str(emoji.id) not in known]
            written = 0
            for start in range(0, len(missing), PER_REQUEST):
                try:
                    written += await _describe_group(missing[start:start + PER_REQUEST])
                except BudgetExceededError:
                    logger.warning("Emoji notes stopped: the AI budget is used up")
                    break
                except Exception as e:
                    logger.warning(f"Emoji notes for one group failed: {e!r}")
            if missing:
                logger.info(f"Emoji notes: wrote {written} of {len(missing)} missing")
            return written
        except Exception as e:
            logger.error(f"Emoji notes failed: {e!r}")
            return 0
