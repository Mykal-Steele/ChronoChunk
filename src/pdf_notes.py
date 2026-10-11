"""
Page notes for PDFs: one pass over the page images that writes down what each page
shows, so slides whose tables, charts and diagrams are pictures can be answered from
text. Tested on real lecture decks against sending the pages as pictures with every
question: this was the most accurate, and follow-up questions cost and wait less.
"""
import asyncio
import base64
import hashlib
import io
import logging
import threading
import time
from collections import OrderedDict
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from config.config import Config
from src.usage_guard import BudgetExceededError

logger = logging.getLogger(__name__)

# A PDF with more pages than this has only its first pages described
MAX_NOTE_PAGES = 150
# Pages sent to the model in one request, and how many requests run at once
PAGES_PER_REQUEST = 8
REQUESTS_AT_ONCE = 5
# Long side of a rendered page. Slides are 16:9, so this is 768 by 432, enough to read a table cell.
PAGE_PICTURE_SIDE = 768
# Room for the descriptions of one request's pages
NOTES_MAX_TOKENS = 4000
# The whole first pass may take this long. What is done by then is used.
NOTES_TIMEOUT_SECONDS = 120
# Descriptions are cut here before they go into a prompt
NOTES_MAX_CHARS = 90000
# In "auto", a page with less text than this is described even when no picture is found on it
SPARSE_PAGE_CHARS = 50
# Descriptions are kept in memory, never on disk: this many files, for this long
CACHE_FILES = 30
CACHE_SECONDS = 6 * 3600

PROMPT = (
    "These are pages {pages} of a document, in that order. For each one write a heading '## Page N' and then "
    "everything a reader would need that plain text extraction misses: text inside pictures, every table as "
    "rows, formulas, and each chart or diagram with its title, axis labels, legend, node labels and the key "
    "values or shape of what it shows. Be complete and compact. No commentary."
)

# Set by the bot at startup: describe(prompt, images) -> (text, cost in USD)
Describer = Callable[[str, List[str]], Awaitable[Tuple[str, float]]]
_describe: Optional[Describer] = None

_cache: "OrderedDict[str, Tuple[float, str]]" = OrderedDict()
_in_progress: Dict[str, "asyncio.Future"] = {}
# pdfium is not safe to call from two threads at once
_render_lock = threading.Lock()


def set_describer(describe: Optional[Describer]) -> None:
    """Give this module the model call it uses to describe page images. None switches page notes off."""
    global _describe
    _describe = describe


def clear_cache() -> None:
    _cache.clear()


def _pages_to_describe(data: bytes, mode: str) -> List[int]:
    """
    Page numbers, from 0, worth describing. "on" is every page. "auto" is every page
    that holds a picture, drawn shapes or almost no text. In a slide deck that is
    nearly all of them, and in a plain text report nearly none.
    """
    import pypdfium2
    import pypdfium2.raw as raw

    pdf = pypdfium2.PdfDocument(data)
    try:
        total = min(len(pdf), MAX_NOTE_PAGES)
        if mode == "on":
            return list(range(total))
        chosen = []
        for number in range(total):
            page = pdf[number]
            kinds = [item.type for item in page.get_objects(max_depth=3)]
            has_picture = raw.FPDF_PAGEOBJ_IMAGE in kinds or raw.FPDF_PAGEOBJ_SHADING in kinds
            drawn = kinds.count(raw.FPDF_PAGEOBJ_PATH)
            text = page.get_textpage().get_text_range() or ""
            if has_picture or drawn >= 2 or len(text.strip()) < SPARSE_PAGE_CHARS:
                chosen.append(number)
        return chosen
    finally:
        pdf.close()


def _render(data: bytes, pages: List[int]) -> List[str]:
    """Render pages to JPEG data URLs."""
    import pypdfium2

    pdf = pypdfium2.PdfDocument(data)
    try:
        urls = []
        for number in pages:
            page = pdf[number]
            width, height = page.get_size()
            picture = page.render(scale=PAGE_PICTURE_SIDE / max(width, height)).to_pil().convert("RGB")
            buffer = io.BytesIO()
            picture.save(buffer, "JPEG", quality=85)
            urls.append("data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"))
        return urls
    finally:
        pdf.close()


def _locked(function, *args):
    with _render_lock:
        return function(*args)


async def _in_render_thread(function, *args):
    return await asyncio.to_thread(_locked, function, *args)


async def _build(data: bytes, filename: str, mode: str) -> Tuple[Optional[str], bool]:
    """
    Describe the pages of one PDF. Returns the descriptions (None when there is nothing
    to add) and whether every page that should be described was.
    """
    started = time.monotonic()
    pages = await _in_render_thread(_pages_to_describe, data, mode)
    if not pages:
        return None, True

    limit = asyncio.Semaphore(REQUESTS_AT_ONCE)
    costs: List[float] = []

    async def describe_group(group: List[int]) -> str:
        async with limit:
            pictures = await _in_render_thread(_render, data, group)
            text, cost = await _describe(PROMPT.format(pages=", ".join(str(n + 1) for n in group)), pictures)
            costs.append(cost)
            return text.strip()

    groups = [pages[start:start + PAGES_PER_REQUEST] for start in range(0, len(pages), PAGES_PER_REQUEST)]
    tasks = [asyncio.ensure_future(describe_group(group)) for group in groups]
    done, pending = await asyncio.wait(tasks, timeout=NOTES_TIMEOUT_SECONDS)
    for task in pending:
        task.cancel()

    parts = []
    for task, group in zip(tasks, groups):
        if task in pending:
            logger.warning(f"Page notes for {filename}: pages {group[0] + 1} to {group[-1] + 1} ran out of time")
            continue
        error = task.exception()
        if isinstance(error, BudgetExceededError):
            raise error
        if error:
            # One failed group costs those pages their description, not the whole file
            logger.warning(f"Page notes for {filename}: pages {group[0] + 1} to {group[-1] + 1} failed: {error!r}")
        elif task.result():
            parts.append(task.result())
    complete = len(parts) == len(groups)
    if not parts:
        return None, complete

    notes = "\n\n".join(parts)
    if len(notes) > NOTES_MAX_CHARS:
        notes = notes[:NOTES_MAX_CHARS] + "\n(the descriptions are longer than this, the rest was cut off)"
    logger.info(
        f"Page notes for {filename}: described {len(pages)} pages in {len(groups)} requests, {len(notes)} chars, "
        f"about ${sum(costs):.4f}, took {time.monotonic() - started:.1f}s"
        + ("" if complete else f", {len(groups) - len(parts)} requests failed")
    )
    return notes, complete


async def page_notes(data: bytes, filename: str, wait_seconds: Optional[float] = None) -> Optional[str]:
    """
    What each page of a PDF shows, written from the page images. None when page notes
    are off, the PDF has nothing worth describing, or the pass failed. The first call
    for a file takes a while. The result is kept in memory, so later questions about
    the same file get it at once.

    wait_seconds is how long this caller will wait. When it runs out the caller gets
    None, and the pass carries on so the next question about the file finds it done.
    """
    mode = Config.PDF_PAGE_NOTES
    if _describe is None or mode not in ("auto", "on"):
        return None

    key = f"{mode}:{hashlib.sha256(data).hexdigest()}"
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        _cache.move_to_end(key)
        return cached[1]

    # Two people asking about the same new file share one pass
    running = _in_progress.get(key)
    if running is None:
        running = asyncio.ensure_future(_build(data, filename, mode))
        _in_progress[key] = running
        running.add_done_callback(lambda finished: _finish(key, finished))
    try:
        notes, _ = await asyncio.wait_for(asyncio.shield(running), wait_seconds)
    except asyncio.TimeoutError:
        logger.warning(f"Page notes for {filename} are not ready after {wait_seconds:.0f}s, answering without them")
        return None
    except BudgetExceededError:
        logger.warning(f"Page notes for {filename} skipped: the AI budget is used up")
        return None
    except Exception as e:
        logger.error(f"Page notes for {filename} failed: {e!r}")
        return None
    return notes


def _finish(key: str, finished: "asyncio.Future") -> None:
    """Keep a finished pass for later questions. Runs even when nobody waited for it to end."""
    _in_progress.pop(key, None)
    if finished.cancelled() or finished.exception():
        return
    notes, complete = finished.result()
    # A pass that lost pages to a rate limit or a slow model is used once and tried again next time
    if notes and complete:
        _cache[key] = (time.monotonic(), notes)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_FILES:
            _cache.popitem(last=False)
