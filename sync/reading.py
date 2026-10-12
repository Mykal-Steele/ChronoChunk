"""
Turns one downloaded document into pages of English text.

A PDF page gives its text layer, plus a description of what the text layer misses
(pictures of text, tables, charts), written by the model from a picture of the page.
This is the method the bot uses for attached PDFs (src/pdf_notes.py), with one more
rule: everything ends up in English, and Thai wording is kept beside its translation.
Office files are converted to PDF first. Descriptions are cached per group of pages,
so a run that stops halfway through a deck continues from the group it stopped at.
"""
import base64
import io
import logging
import re
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from sync.cache import Cache
from sync.material import CHUNK_MAX_CHARS, OFFICE, has_thai
from sync.models import CALLS_AT_ONCE, Models, SpendCapReached
from sync.settings import HOME

logger = logging.getLogger(__name__)

# Part of every cache key. Raise it when the prompt or the method changes, and every document is read again.
READER_VERSION = 2
PAGES_PER_REQUEST = 8
# Long side of a rendered page. Slides are 16:9, so this is 768 by 432, enough to read a table cell.
PAGE_PICTURE_SIDE = 768
# A page with less text than this is described even when no picture is found on it
SPARSE_PAGE_CHARS = 50
# A text file is cut into pieces of this size, and only this many are kept
TEXT_PIECE_CHARS = 3000
TEXT_PIECES = 20
CONVERT_SECONDS = 300

PROMPT = (
    "These are pages {pages} of a university course document, in that order, as pictures. Below them is the "
    "text a program already extracted from each page. For each page write a heading '## Page N' and then only "
    "what the extracted text is missing: text inside pictures, every table as rows, formulas, and each chart "
    "or diagram with its title, axis labels, legend, node labels and the key values or shape of what it shows. "
    "Do not repeat what the extracted text already says. Write in English and translate anything that is in "
    "another language. Be complete and compact. No commentary. For a page with nothing to add, write the "
    "heading and the word 'nothing'.\n\nExtracted text:\n{extracted}"
)
# How much of a page's extracted text the model is shown
EXTRACT_SHOWN_CHARS = 1500

# pdfium is not safe to call from two threads at once, and LibreOffice runs one conversion at a time
_pdf_lock = threading.Lock()
_office_lock = threading.Lock()


class ReadError(Exception):
    """This document could not be read. The run reports it and carries on with the others."""


@dataclass
class Page:
    number: int
    # English, and what gets searched
    text: str
    # The page's own wording, kept only when text is a translation of it
    original: Optional[str] = None


@dataclass
class Reading:
    pages: List[Page] = field(default_factory=list)
    described: int = 0
    # Requests for descriptions that failed. Their pages keep their text layer, and the next run tries again.
    failed_requests: int = 0
    note: str = ""


def _tidy(text: str) -> str:
    return " ".join(text.split())


def survey(data: bytes) -> List[dict]:
    """Every page's text layer, and whether the page holds something the text layer misses."""
    import pypdfium2
    import pypdfium2.raw as raw

    with _pdf_lock:
        try:
            pdf = pypdfium2.PdfDocument(data)
        except Exception as error:
            raise ReadError(f"not a readable PDF: {error}") from error
        try:
            pages = []
            for number in range(len(pdf)):
                page = pdf[number]
                kinds = [item.type for item in page.get_objects(max_depth=3)]  # pyright: ignore[reportAttributeAccessIssue]
                has_picture = raw.FPDF_PAGEOBJ_IMAGE in kinds or raw.FPDF_PAGEOBJ_SHADING in kinds
                text = _tidy(page.get_textpage().get_text_range() or "")
                pages.append({"text": text,
                              "describe": has_picture or kinds.count(raw.FPDF_PAGEOBJ_PATH) >= 2 or len(text) < SPARSE_PAGE_CHARS})
            return pages
        finally:
            pdf.close()


def render(data: bytes, numbers: List[int]) -> List[str]:
    """The given pages (counted from 0) as JPEG data URLs."""
    import pypdfium2

    with _pdf_lock:
        pdf = pypdfium2.PdfDocument(data)
        try:
            urls = []
            for number in numbers:
                page = pdf[number]
                width, height = page.get_size()
                picture = page.render(scale=PAGE_PICTURE_SIDE / max(width, height)).to_pil().convert("RGB")
                buffer = io.BytesIO()
                picture.save(buffer, "JPEG", quality=85)
                urls.append("data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"))
            return urls
        finally:
            pdf.close()


def split_descriptions(answer: str) -> Dict[int, str]:
    """The model's answer as {page number from 1: description}. A page with nothing to add is left out."""
    found: Dict[int, str] = {}
    parts = re.split(r"^#+\s*Page\s+(\d+)\b.*$", answer, flags=re.MULTILINE | re.IGNORECASE)
    for number, body in zip(parts[1::2], parts[2::2], strict=False):
        body = body.strip()
        if body and body.lower().strip(".") != "nothing":
            found[int(number)] = body
    return found


def to_pdf(path: Path) -> bytes:
    """An Office file as PDF bytes, converted by LibreOffice with a profile of its own."""
    with _office_lock, tempfile.TemporaryDirectory() as out:
        # Its own profile, so a LibreOffice window the person has open is neither used nor disturbed
        profile = (HOME / "libreoffice").resolve().as_uri()
        result = subprocess.run(
            ["soffice", f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf", "--outdir", out, str(path)],
            capture_output=True, timeout=CONVERT_SECONDS, check=False)
        converted = Path(out) / (path.stem + ".pdf")
        if not converted.exists():
            raise ReadError(f"LibreOffice could not convert it: {result.stderr.decode(errors='replace')[:200]}")
        return converted.read_bytes()


def _read_text(path: Path, models: Models) -> Reading:
    text = path.read_text(encoding="utf-8", errors="replace")
    pieces: List[str] = []
    piece = ""
    for line in text.splitlines():
        if piece and len(piece) + len(line) > TEXT_PIECE_CHARS:
            pieces.append(piece)
            piece = ""
        piece += line[:TEXT_PIECE_CHARS] + "\n"
    if piece.strip():
        pieces.append(piece)
    reading = Reading(note=f"only the first {TEXT_PIECES} of {len(pieces)} parts were read" if len(pieces) > TEXT_PIECES else "")
    kept = [piece.strip() for piece in pieces[:TEXT_PIECES]]
    thai = [piece for piece in kept if has_thai(piece)]
    english = dict(zip(thai, models.translate(thai), strict=True))
    reading.pages = [Page(number, english.get(piece, piece), piece if piece in english else None)
                     for number, piece in enumerate(kept, 1)]
    return reading


def read_document(path: Path, kind: str, digest: str, models: Models, cache: Cache) -> Reading:
    """
    The pages of one document. kind is its extension and digest the hash of its
    content, which is what its cached descriptions are stored under.
    """
    if kind in (".csv", ".txt"):
        return _read_text(path, models)
    data = to_pdf(path) if kind in OFFICE else path.read_bytes()
    pages = survey(data)
    reading = Reading()

    to_describe = [number for number, page in enumerate(pages) if page["describe"]]
    groups = [to_describe[start:start + PAGES_PER_REQUEST] for start in range(0, len(to_describe), PAGES_PER_REQUEST)]

    def describe(group: List[int]) -> Optional[str]:
        key = f"{READER_VERSION}:{digest}:{group[0]}-{group[-1]}"
        cached = cache.get("description", key)
        if cached is not None:
            return cached
        try:
            extracted = "\n".join(f"Page {number + 1}: {pages[number]['text'][:EXTRACT_SHOWN_CHARS] or '(none)'}" for number in group)
            prompt = PROMPT.format(pages=", ".join(str(number + 1) for number in group), extracted=extracted)
            answer = models.describe(prompt, render(data, group))
        except SpendCapReached:
            raise
        except Exception as error:
            # One failed request costs those pages their description, not the whole file
            logger.warning(f"{path.name}: pages {group[0] + 1} to {group[-1] + 1} were not described: {error!r}")
            return None
        cache.put("description", key, answer)
        return answer

    descriptions: Dict[int, str] = {}
    with ThreadPoolExecutor(CALLS_AT_ONCE) as pool:
        for group, answer in zip(groups, pool.map(describe, groups), strict=True):
            if answer is None:
                reading.failed_requests += 1
                continue
            found = split_descriptions(answer)
            descriptions.update({number: body for number, body in found.items() if number - 1 in group})
    reading.described = len(to_describe)

    thai = [page["text"] for page in pages if has_thai(page["text"])]
    english = dict(zip(thai, models.translate(thai), strict=True))
    for number, page in enumerate(pages, 1):
        own = page["text"]
        parts = [part for part in (english.get(own, own), descriptions.get(number, "")) if part]
        if parts:
            reading.pages.append(Page(number, "\n".join(parts)[:CHUNK_MAX_CHARS], own if own in english else None))
    return reading
