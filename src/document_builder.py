"""Turns Markdown into files people can use: PDF, DOCX, Markdown, and rendered Mermaid diagrams."""
import asyncio
import base64
import html
import io
import json
import logging
import os
import re
import struct
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import aiohttp

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = ("pdf", "docx", "md")
MAX_DOCUMENT_CHARS = 80000
MAX_DIAGRAMS_PER_DOCUMENT = 6
MAX_FILE_BYTES = 9 * 1024 * 1024  # Discord rejects uploads over 10 MB
PANDOC_TIMEOUT_SECONDS = 60
PDF_TIMEOUT_SECONDS = 120
MERMAID_TIMEOUT_SECONDS = 30
MERMAID_URL = "https://mermaid.ink/img/"
# Used only when mermaid.ink is down or failing. kroki.io either answers in a second or
# two or hangs for half a minute, so it gets a short timeout and a second try.
KROKI_URL = "https://kroki.io/mermaid/png/"
KROKI_TIMEOUT_SECONDS = 10
KROKI_TRIES = 2
DIAGRAM_WIDTH = 1400
# A flowchart more than this many times wider than it is tall is a strip of tiny text
MAX_WIDTH_TO_HEIGHT = 2.5

# gfm is what the model writes. Newlines stay as line breaks so address blocks and
# signatures keep their shape, and raw HTML is read as plain text.
PANDOC_INPUT_FORMAT = "gfm+hard_line_breaks-raw_html"
PANDOC_FILTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pandoc_filter.lua")
# Word template that sets fonts, heading colors and table borders. See assets/build_reference_docx.py.
DOCX_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "reference.docx")

_MERMAID_BLOCK = re.compile(r"^```mermaid[^\n]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)

# Opening words that are Mermaid and nothing else, so they can be spotted in chat text
_DIAGRAM_HEADER = (r"(?:(?:flowchart|graph)[ \t]+(?:TD|TB|BT|LR|RL)\b"
                   r"|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram)")
_FENCED_BLOCK = re.compile(r"```(\w*)[ \t]*\n(.*?)```", re.DOTALL)
_LOOSE_DIAGRAM = re.compile(rf"\b{_DIAGRAM_HEADER}.*", re.DOTALL)

_FLOWCHART_HEADER = re.compile(r"\s*(?:flowchart|graph)\b")
# A sequence diagram prints the quotes around a participant's display name
_QUOTED_PARTICIPANT = re.compile(r'^([ \t]*(?:participant|actor)[ \t]+\S+[ \t]+as[ \t]+)"([^"\n]*)"[ \t]*$', re.MULTILINE)
_FLOWCHART_DIRECTION = re.compile(r"\A(?:flowchart|graph)[ \t]+(TD|TB|LR)\b")
# Sent with every diagram. Notes get dark text on a light box and long text wraps,
# because the renderer otherwise cuts note text off at the edge of its box. The wide
# spacing between participants gives long messages room before they have to wrap.
MERMAID_CONFIG = {
    "theme": "neutral",
    "sequence": {"wrap": True, "actorMargin": 140},
    "themeVariables": {"noteBkgColor": "#fff8c4", "noteTextColor": "#222222", "noteBorderColor": "#c9b458"},
}
# Flowchart node shapes with the brackets that close them, longest opener first
_NODE_SHAPES = (
    ("(((", (")))",)), ("((", ("))",)), ("([", ("])",)), ("[[", ("]]",)), ("[(", (")]",)),
    ("[/", ("/]", "\\]")), ("[\\", ("\\]", "/]")), ("{{", ("}}",)),
    ("[", ("]",)), ("(", (")",)), ("{", ("}",)),
)
# What can follow a node: the end of the statement, another node, a class or a link
_AFTER_NODE = re.compile(r"[ \t\r]*(?:\Z|[\n;&]|:::|--|-\.|==|~~~|<--|[ox]--|%%)")
# Tags people put in a label on purpose. Anything else in angle brackets is text, such as List<T>.
_LABEL_TAG = re.compile(r"(</?(?:[bB][rR]|b|i|u|em|strong|sub|sup|small)\s*/?>)")

# The VM is small, so documents are built one at a time
_build_lock = asyncio.Lock()

# Sizes are relative to the root font size, so one number scales the whole page
PDF_STYLE = """
@page {
  size: A4;
  margin: __MARGIN__;
  @bottom-center { content: counter(page) " / " counter(pages); font-size: 8.5pt; color: #777; }
}
html { font-family: "Noto Sans", "Noto Sans Thai", "DejaVu Sans", sans-serif; font-size: __BASE__pt; line-height: __LEADING__; color: #1a1a1a; }
h1 { font-size: 2rem; line-height: 1.2; font-weight: 700; margin: 0 0 1.4rem; }
h2 { font-size: 1.3rem; line-height: 1.3; font-weight: 700; margin: 1.7rem 0 0.55rem; break-after: avoid; }
h3 { font-size: 1.1rem; font-weight: 700; margin: 1.1rem 0 0.4rem; break-after: avoid; }
h4, h5, h6 { font-size: 1rem; font-weight: 700; margin: 0.85rem 0 0.4rem; break-after: avoid; }
p { margin: 0 0 0.7rem; orphans: 2; widows: 2; }
ul, ol { margin: 0 0 0.7rem; padding-left: 1.7rem; }
li { margin-bottom: 0.17rem; }
li > p { margin-bottom: 0.17rem; }
table { border-collapse: collapse; width: 100%; margin: 0.85rem 0 1.1rem; font-size: 0.95rem; }
th, td { border: 0.5pt solid #b8b8b8; padding: 0.4rem 0.7rem; text-align: left; vertical-align: top; }
th { background: #efefef; font-weight: 700; }
tr { break-inside: avoid; }
pre { background: #f5f5f5; border: 0.5pt solid #dcdcdc; padding: 0.85rem; font-size: 0.85rem; line-height: 1.4; white-space: pre-wrap; overflow-wrap: anywhere; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 0.9em; }
img { display: block; max-width: 100%; max-height: __IMAGE__mm; margin: 1.1rem auto; }
blockquote { border-left: 2pt solid #b8b8b8; margin: 0.85rem 0; padding-left: 1.1rem; color: #444; }
a { color: #0b57d0; text-decoration: none; }
hr { border: 0; border-top: 0.5pt solid #b8b8b8; margin: 1.7rem 0; }
"""

PDF_BASE_FONT_PT = 10
# Tried in order when a page limit is given: (font scale, page margin, line height,
# tallest image in mm). Text never goes below 8pt.
PDF_FIT_STEPS = (
    (1.0, "18mm 19mm 20mm 19mm", 1.45, 90),
    (0.93, "16mm 17mm 18mm 17mm", 1.42, 80),
    (0.86, "14mm 16mm 17mm 16mm", 1.38, 68),
    (0.8, "12mm 15mm 15mm 15mm", 1.32, 55),
)

# Built with chr() so this file itself holds no long dashes or curly quotes
_DASHES = f"[{chr(0x2013)}{chr(0x2014)}]"
_CURLY_QUOTES = {chr(0x201C): '"', chr(0x201D): '"', chr(0x2018): "'", chr(0x2019): "'"}

# Stock sentences that mark a text as machine-written. They are removed outright.
_FILLER_SENTENCES = re.compile(
    r"(?:"
    r"I hope (?:that )?(?:this|my) (?:email|message|letter|note) finds you(?: and your \w+)? (?:well|in good (?:health|spirits))"
    r"|I hope (?:that )?you(?:'re| are) (?:doing |keeping )?well"
    r"|I hope (?:that )?this helps"
    r"|(?:Please )?(?:feel free to|do not hesitate to|don't hesitate to) (?:reach out|contact me|let me know)[^.!?\n]*"
    r")[.!][ \t]*",
    flags=re.IGNORECASE,
)


class DocumentError(Exception):
    """A file could not be built. The message is written so it can be shown to the model."""


class DiagramSyntaxError(DocumentError):
    """The Mermaid parser turned the source down."""


@dataclass
class BuiltFile:
    """A finished file ready to upload to Discord."""
    filename: str
    data: bytes


def clean_title(title: str) -> str:
    """Turn a title the model wrote like a filename ("Deploy_Process.pdf") into a normal title."""
    title = re.sub(r"\.(pdf|docx?|md|png)$", "", (title or "").strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", title.replace("_", " ")).strip()


def slugify(title: str, fallback: str = "document") -> str:
    """
    Make a safe ASCII filename stem from a title. A title that is mostly not
    ASCII (Thai, for example) uses the fallback instead of the scraps left over.
    """
    title = title or ""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:60].strip("-")
    ascii_chars = len(re.sub(r"[^A-Za-z0-9]", "", title))
    all_chars = len(re.sub(r"\s", "", title))
    return slug if slug and ascii_chars * 2 >= all_chars else fallback


def normalize_markdown(markdown: str) -> str:
    """
    Add the blank line Markdown needs before a table. Without it a table that
    follows a line of text is read as part of that paragraph and comes out raw.
    """
    lines = markdown.split("\n")
    fixed: List[str] = []
    in_code = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_code = not in_code
        starts_table = line.lstrip().startswith("|") and not in_code
        if starts_table and fixed and fixed[-1].strip() and not fixed[-1].lstrip().startswith("|"):
            fixed.append("")
        fixed.append(line)
    return "\n".join(fixed)


def strip_filler(text: str) -> str:
    """Remove stock filler sentences such as "I hope this email finds you well." """
    return re.sub(r"\n{3,}", "\n\n", _FILLER_SENTENCES.sub("", text))


def clean_document_text(markdown: str) -> str:
    """
    Remove the marks that make a document read as machine-written: stock filler
    sentences, em and en dashes and curly quotes. Fenced code blocks are left alone.
    """
    def clean(text: str) -> str:
        for curly, straight in _CURLY_QUOTES.items():
            text = text.replace(curly, straight)
        text = strip_filler(text)
        text = re.sub(rf"(?<=\d)\s*{_DASHES}\s*(?=\d)", "-", text)   # number ranges keep a hyphen
        # a dash that opens a line (or a list item) is just removed
        text = re.sub(rf"^([ \t]*(?:(?:[-*+]|\d+\.)[ \t]+)?){_DASHES}[ \t]*", r"\1", text, flags=re.MULTILINE)
        return re.sub(rf"[ \t]*{_DASHES}[ \t]*", ", ", text)        # a dash between words becomes a comma

    parts = re.split(r"(^```.*?^```[ \t]*$)", markdown, flags=re.DOTALL | re.MULTILINE)
    return "".join(part if part.startswith("```") else clean(part) for part in parts)


def split_mermaid(text: str) -> Optional[Tuple[str, str]]:
    """
    Find Mermaid source written into a chat message, in a code block or loose
    at the end. Returns the source and the text around it, or None.
    """
    for block in _FENCED_BLOCK.finditer(text):
        language, body = block.group(1).lower(), block.group(2).strip()
        if language == "mermaid" or (not language and re.match(_DIAGRAM_HEADER, body)):
            return body, (text[:block.start()] + text[block.end():]).strip()
    loose = _LOOSE_DIAGRAM.search(text)
    # The arrow check keeps a sentence that only mentions "flowchart TD" out
    if loose and "```" not in loose.group(0) and re.search(r"--|->", loose.group(0)):
        return loose.group(0).strip(), text[:loose.start()].strip()
    return None


def _escape_angles(text: str) -> str:
    """Escape < and > so Mermaid prints them instead of reading them as HTML."""
    parts = _LABEL_TAG.split(text)
    return "".join(part if index % 2 else part.replace("<", "#lt;").replace(">", "#gt;")
                   for index, part in enumerate(parts))


def _quote_label(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
        if text.startswith("`"):
            return f'"{text}"'  # a Markdown string, left as written
    else:
        text = text.replace('"', "#quot;")
    return f'"{_escape_angles(text)}"'


def _line_end(source: str, start: int) -> int:
    end = source.find("\n", start)
    return len(source) if end == -1 else end


def _node_label(source: str, start: int) -> Optional[Tuple[str, str, str, int]]:
    """
    Read the node label whose bracket opens at start. Returns the opener, the
    label, the closer and the position after it, or None when no label ends on
    this line.
    """
    line_end = _line_end(source, start)
    for opener, closers in _NODE_SHAPES:
        if not source.startswith(opener, start):
            continue
        body = start + len(opener)
        if source[body:line_end].lstrip().startswith('"'):
            return None  # already quoted, the caller handles the string itself
        for closer in closers:
            # A label may hold its own closing bracket, as in stream(words), so the
            # label ends at the first closer that something valid follows
            end = source.find(closer, body, line_end)
            while end != -1:
                if end > body and _AFTER_NODE.match(source, end + len(closer)):
                    return opener, source[body:end], closer, end + len(closer)
                end = source.find(closer, end + 1, line_end)
    return None


def fix_flowchart_labels(source: str) -> str:
    """
    Quote every flowchart label and escape the characters that break one. The
    model writes labels such as stream(words) and List<T>. Mermaid rejects the
    first and drops the <T> from the second unless they are quoted and escaped.
    Other diagram types come back unchanged.
    """
    if not _FLOWCHART_HEADER.match(source):
        return source

    out = []
    i = 0
    while i < len(source):
        char = source[i]
        end = -1
        if char == '"':
            end = source.find('"', i + 1)
            if end == -1:
                break
            out.append(_quote_label(source[i:end + 1]))
        elif char == "|":
            # An edge label sits between two pipes on one line
            end = source.find("|", i + 1, _line_end(source, i))
            if end != -1 and source[i + 1:end].strip():
                out.append(f"|{_quote_label(source[i + 1:end])}|")
            else:
                end = -1
        elif char in "[({" and i > 0 and (source[i - 1].isalnum() or source[i - 1] == "_"):
            node = _node_label(source, i)
            if node:
                opener, label, closer, after = node
                out.append(opener + _quote_label(label) + closer)
                end = after - 1
        if end == -1:
            out.append(char)
            end = i
        i = end + 1
    out.append(source[i:])
    return "".join(out)


def fix_sequence_names(source: str) -> str:
    """
    Take the quotes off participant names in a sequence diagram, where Mermaid
    prints them as part of the name. Other diagram types come back unchanged.
    """
    if not source.lstrip().startswith("sequenceDiagram"):
        return source
    return _QUOTED_PARTICIPANT.sub(r"\1\2", source)


async def render_mermaid(source: str) -> bytes:
    """
    Render Mermaid source to a PNG with the public mermaid.ink service.
    Raises DocumentError with the parser's message when the diagram is invalid.
    """
    source = source.strip()
    if not source:
        raise DocumentError("the mermaid source is empty")

    fixed = fix_sequence_names(fix_flowchart_labels(source))
    try:
        return await _request_diagram(fixed)
    except DiagramSyntaxError:
        if fixed == source:
            raise
    # The label fix can misread unusual source, so the diagram as written gets a try too.
    # If that fails as well, the error describes the source the model wrote.
    return await _request_diagram(source)


async def _request_diagram(source: str) -> bytes:
    """Render with mermaid.ink, and with kroki.io when that service is down."""
    payload = json.dumps({"code": source, "mermaid": MERMAID_CONFIG})
    encoded = base64.urlsafe_b64encode(zlib.compress(payload.encode("utf-8"), 9)).decode("ascii")
    try:
        return await _fetch_diagram(f"{MERMAID_URL}pako:{encoded}?type=png&bgColor=white&width={DIAGRAM_WIDTH}", "mermaid.ink")
    except DiagramSyntaxError:
        raise
    except DocumentError as e:
        down = e  # kept, because this is the error to report if the backup fails as well
        logger.warning(f"{down}. Trying kroki.io")

    # kroki takes its settings as a line in the source. It rejects the note settings on
    # anything but a sequence diagram, so the other types only get the theme.
    settings = MERMAID_CONFIG if source.startswith("sequenceDiagram") else {"theme": MERMAID_CONFIG["theme"]}
    with_settings = "%%{init: " + json.dumps(settings) + "}%%\n" + source
    encoded = base64.urlsafe_b64encode(zlib.compress(with_settings.encode("utf-8"), 9)).decode("ascii")
    for _ in range(KROKI_TRIES):
        try:
            return await _fetch_diagram(KROKI_URL + encoded, "kroki.io", KROKI_TIMEOUT_SECONDS)
        except DocumentError as e:
            logger.warning(f"The backup renderer failed too: {e}")
    raise down


async def _fetch_diagram(url: str, service: str, timeout_seconds: int = MERMAID_TIMEOUT_SECONDS) -> bytes:
    """Download one rendered diagram and check that it is a whole PNG."""
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                body = await response.read()
                status = response.status
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning(f"Diagram renderer {service} unreachable: {e!r}")
        raise DocumentError(f"the diagram renderer ({service}) could not be reached, try again later")

    if status == 400:
        detail = body.decode("utf-8", errors="replace").strip()[:600]
        raise DiagramSyntaxError(f"the mermaid source has a syntax error: {detail}")
    if status != 200 or not body.startswith(b"\x89PNG"):
        logger.warning(f"Diagram renderer {service} returned status {status}")
        raise DocumentError(f"the diagram renderer ({service}) failed with status {status}, try again later")

    # A cut-off download would break the whole document later, so decode it once here
    try:
        from PIL import Image
        Image.open(io.BytesIO(body)).load()
    except Exception as e:
        logger.warning(f"Diagram renderer {service} returned a broken image: {e!r}")
        raise DocumentError(f"the diagram renderer ({service}) returned a broken image, try again later")
    return body


async def _render_diagrams(markdown: str, workdir: str) -> Tuple[str, List[str]]:
    """
    Replace each ```mermaid block with a rendered image saved in workdir.
    Returns the new Markdown and the image filenames. A diagram that fails to
    render stays in the document as its source code.
    """
    images: List[str] = []
    rendered: Dict[str, str] = {}

    for index, match in enumerate(_MERMAID_BLOCK.finditer(markdown)):
        if index >= MAX_DIAGRAMS_PER_DOCUMENT:
            break
        try:
            png = await render_mermaid(match.group(1))
        except DocumentError as e:
            logger.warning(f"Diagram {index + 1} left as code: {e}")
            continue
        name = f"diagram-{index + 1}.png"
        with open(os.path.join(workdir, name), "wb") as f:
            f.write(png)
        images.append(name)
        rendered[match.group(0)] = f"![]({name})"

    for block, image in rendered.items():
        markdown = markdown.replace(block, image, 1)
    return markdown, images


async def _run_pandoc(markdown: str, workdir: str, images: List[str], extra_args: List[str]) -> bytes:
    """Run pandoc on the Markdown inside workdir and return what it wrote to stdout."""
    env = dict(os.environ, ALLOWED_IMAGES=":".join(images))
    process = await asyncio.create_subprocess_exec(
        "pandoc", "--from", PANDOC_INPUT_FORMAT, "--lua-filter", PANDOC_FILTER, *extra_args,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=workdir, env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(markdown.encode("utf-8")), PANDOC_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        process.kill()
        raise DocumentError("converting the document took too long")
    if process.returncode != 0:
        logger.error(f"pandoc failed ({process.returncode}): {stderr.decode('utf-8', errors='replace')[:500]}")
        raise DocumentError("the document could not be converted")
    return stdout


def _widen_tables(docx: bytes) -> bytes:
    """
    Make every table in a DOCX span the page width. Pandoc sizes tables to their
    content, which leaves them narrow with badly wrapped cells.
    """
    source = zipfile.ZipFile(io.BytesIO(docx))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "word/document.xml":
                # width type "pct" counts in fiftieths of a percent, so 5000 is 100%
                data = re.sub(rb"<w:tblW [^>]*/>", b'<w:tblW w:type="pct" w:w="5000" />', data)
            target.writestr(item, data)
    return output.getvalue()


def _workdir_fetcher(workdir: str):
    """A WeasyPrint URL fetcher that only serves files the bot put in workdir."""
    from weasyprint import URLFetcher

    root = os.path.realpath(workdir) + os.sep

    class WorkdirFetcher(URLFetcher):
        def fetch(self, url, headers=None):
            parsed = urlparse(url)
            if parsed.scheme != "file" or not os.path.realpath(unquote(parsed.path)).startswith(root):
                raise ValueError(f"blocked resource: {url[:80]}")
            return super().fetch(url, headers)

    return WorkdirFetcher(allowed_protocols=("file",), allow_redirects=False)


def _html_to_pdf(body_html: str, title: str, workdir: str, max_pages: Optional[int] = None) -> bytes:
    """
    Lay out the HTML as an A4 PDF. Runs in a worker thread. With max_pages the
    text and margins shrink step by step until the document fits. If it cannot
    fit even at the smallest step, the normal size is kept.
    """
    from weasyprint import HTML

    fetcher = _workdir_fetcher(workdir)
    normal = None
    for scale, margin, leading, image_mm in (PDF_FIT_STEPS if max_pages else PDF_FIT_STEPS[:1]):
        style = (
            PDF_STYLE.replace("__BASE__", f"{PDF_BASE_FONT_PT * scale:.2f}")
            .replace("__MARGIN__", margin)
            .replace("__LEADING__", str(leading))
            .replace("__IMAGE__", str(image_mm))
        )
        page = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{html.escape(title)}</title><style>{style}</style></head>"
            f"<body>{body_html}</body></html>"
        )
        document = HTML(string=page, base_url=workdir + os.sep, url_fetcher=fetcher).render()
        normal = normal or document
        if not max_pages or len(document.pages) <= max_pages:
            return document.write_pdf()
    return normal.write_pdf()


async def build_document(fmt: str, title: str, markdown: str, max_pages: Optional[int] = None) -> BuiltFile:
    """
    Build a document from Markdown. fmt is "pdf", "docx" or "md". Mermaid code
    blocks become rendered diagrams in PDF and DOCX output. max_pages asks a PDF
    to shrink its text, within reason, until it fits that many pages.
    """
    fmt = (fmt or "").lower().lstrip(".")
    if fmt not in SUPPORTED_FORMATS:
        raise DocumentError(f"unsupported format '{fmt}', use one of: {', '.join(SUPPORTED_FORMATS)}")
    markdown = (markdown or "").strip()
    if not markdown:
        raise DocumentError("the document is empty")
    if len(markdown) > MAX_DOCUMENT_CHARS:
        raise DocumentError(f"the document is too long ({len(markdown)} characters, the limit is {MAX_DOCUMENT_CHARS})")

    filename = f"{slugify(title)}.{fmt}"
    if fmt == "md":
        return BuiltFile(filename, (markdown + "\n").encode("utf-8"))

    async with _build_lock:
        with tempfile.TemporaryDirectory(prefix="chronodoc-") as workdir:
            markdown, images = await _render_diagrams(normalize_markdown(markdown), workdir)

            if fmt == "docx":
                data = _widen_tables(await _run_pandoc(
                    markdown, workdir, images, ["--to", "docx", "--reference-doc", DOCX_TEMPLATE, "--output", "-"]
                ))
            else:
                body_html = (await _run_pandoc(markdown, workdir, images, ["--to", "html5"])).decode("utf-8")
                try:
                    data = await asyncio.wait_for(
                        asyncio.to_thread(_html_to_pdf, body_html, title, workdir, max_pages), PDF_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    raise DocumentError("laying out the pdf took too long")
                except Exception as e:
                    logger.error(f"PDF layout failed: {e!r}")
                    raise DocumentError("the pdf could not be laid out")

    if len(data) > MAX_FILE_BYTES:
        raise DocumentError(f"the finished file is {len(data) / (1024 * 1024):.1f} MB, too big to upload to discord")
    return BuiltFile(filename, data)


def _width_to_height(png: bytes) -> float:
    """How many times wider than tall a PNG is, read from its header."""
    width, height = struct.unpack(">II", png[16:24])
    return width / max(1, height)


def _turned(source: str) -> Optional[str]:
    """The same flowchart drawn the other way: top-down swapped with left-to-right. None for other diagrams."""
    direction = _FLOWCHART_DIRECTION.match(source)
    if not direction:
        return None
    other = "TD" if direction.group(1) == "LR" else "LR"
    return source[:direction.start(1)] + other + source[direction.end(1):]


async def build_diagram(title: str, mermaid_source: str) -> BuiltFile:
    """Render one Mermaid diagram to a PNG file."""
    png = await render_mermaid(mermaid_source)

    # A box with a dozen arrows fanning out is drawn as one wide strip, and the text in
    # it is too small to read. Turned the other way the same chart is usually fine.
    turned = _turned(mermaid_source.strip())
    if turned and _width_to_height(png) > MAX_WIDTH_TO_HEIGHT:
        try:
            other = await render_mermaid(turned)
            if _width_to_height(other) < _width_to_height(png):
                png = other
        except DocumentError as e:
            logger.warning(f"Could not draw the diagram the other way: {e}")
    return BuiltFile(f"{slugify(title, 'diagram')}.png", png)
