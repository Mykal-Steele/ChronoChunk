"""The tools that make files: documents (PDF, DOCX, Markdown) and diagrams."""
import logging
import re
from typing import Callable, Optional

from config.ai_config import WRITER_PROMPT
from src.document_builder import (
    DiagramSyntaxError, DocumentError, build_diagram, build_document, clean_document_text, clean_title, split_mermaid,
)
from src.exceptions import RateLimitError
from src.tools.base import Tool, ToolResult, Turn, user_content
from src.usage_guard import BudgetExceededError

logger = logging.getLogger(__name__)

# One reply carries at most this many generated files
MAX_FILES_PER_REPLY = 2
# Text this short beside pasted Mermaid source only introduces it ("paste this into mermaid.live")
PASTED_DIAGRAM_INTRO_CHARS = 120
# Room for a few pages plus the model's reasoning. Azure counts this ceiling against
# the per-minute token limit, so it is not set higher than documents need.
WRITER_MAX_TOKENS = 8000

# Added to the system prompt while the file tools are offered
FILES_PROMPT = """
FILES AND DIAGRAMS:
- u can make real files with the create_document and create_diagram tools. the file is attached to ur reply automatically.
- create_document: use it when they ask for a pdf, docx, word doc, file, document or report, or when what they want is too long for a chat message (more than about 250 words). the brief says what to write: what it is, who its for, what it must cover, and every fact they gave. the writer already sees the conversation, the replied-to message and attached files, so dont paste those into the brief. format is pdf unless they asked for docx, word or markdown.
- create_diagram: use it when they ask for a diagram, flowchart, sequence, timeline, org chart or mind map. write valid mermaid with short clean labels.
- one request, one file. if they want a diagram inside a document, say so in the brief and the writer adds it. dont also call create_diagram for it.
- dont use a tool for a normal chat answer or a short piece of text.
- after a tool works, reply with one short line in ur normal voice. dont repeat whats in the file and never say u cant send files.
- if a tool returns an error, tell them plainly what went wrong. if it is a mermaid syntax error, fix the diagram and call the tool again.
- never paste mermaid code into the chat or tell them to render it somewhere themselves. a diagram always goes through create_diagram.
"""

_PAGE_WORDS = {"a": 1, "one": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_PAGE_LIMIT = re.compile(r"\b(a|one|single|two|three|four|five|\d{1,2})[\s-]?pages?\b|(\d{1,2})\s*หน้า", re.IGNORECASE)


_WANTS_DIAGRAM = re.compile(r"diagram|flow ?chart|แผนภาพ|แผนผัง|ไดอะแกรม", re.IGNORECASE)
_WANTS_SOURCE = re.compile(r"code|source|syntax|โค้ด", re.IGNORECASE)


def _source_line(error: str, source: str) -> str:
    """The line of Mermaid source a parser error points at, so the model fixes the right one."""
    match = re.search(r"line (\d+)", error)
    lines = source.strip().splitlines()
    if not match or not 1 <= int(match.group(1)) <= len(lines):
        return ""
    number = int(match.group(1))
    return f"\nLine {number} of your source is: {lines[number - 1].strip()}"


def page_limit_from(text: str) -> Optional[int]:
    """Find a page limit someone asked for in plain words, such as "one page" or "2-page"."""
    match = _PAGE_LIMIT.search(text or "")
    if not match:
        return None
    word = (match.group(1) or match.group(2)).lower()
    pages = _PAGE_WORDS.get(word) or int(word)
    return pages if 1 <= pages <= 20 else None


def file_limit_refusal(file_gate: Optional[Callable[[], None]]) -> Optional[str]:
    """Count one file against this user's daily limit. Returns what to tell the model when they are over it."""
    if not file_gate:
        return None
    try:
        file_gate()
    except RateLimitError as e:
        hours = max(1, round(e.retry_after / 3600))
        return f"error: this user reached their limit for generated files, they can try again in about {hours} hours"
    return None


async def write_document(fmt: str, title: str, brief: str, turn: Turn, max_pages: Optional[int] = None) -> str:
    """Have the professional writer produce the document as Markdown."""
    parts = []
    if turn.history:
        parts.append("CONVERSATION SO FAR, background only:\n" + turn.history)
    if turn.attached:
        parts.append(turn.attached)
    if turn.findings:
        parts.append("LOOKED UP ON THE WEB FOR THIS REQUEST:\n" + "\n\n".join(turn.findings))
    parts.append(f'WHAT {turn.username} ASKED FOR: "{turn.query}"')
    parts.append(f"BRIEF FOR THIS DOCUMENT\nworking title (reword it into a plain sentence-case title): {title}\n{brief}")
    if max_pages:
        parts.append(f"PAGE LIMIT: {max_pages}. Hard limit of {max_pages * 280} words in total, counting tables and lists.")
    else:
        parts.append(
            "LENGTH: about one page, roughly 300 words. Hard limit of 550 words, unless the request "
            "itself asks for a longer or more detailed document."
        )
    # Only their own words count here. The brief is the model's and mentions things nobody asked for.
    if _WANTS_DIAGRAM.search(turn.query):
        parts.append("DIAGRAM: they asked for one. Include it as a fenced code block marked mermaid.")
    elif max_pages:
        parts.append("DIAGRAM: none. There is no room for one under the page limit.")
    # Said last because the model otherwise writes plain text when the target is a pdf or docx
    target = {"pdf": "PDF", "docx": "Word"}.get(fmt, "Markdown")
    parts.append(
        f"Write the document now, in Markdown. It is converted to a {target} file afterwards. "
        "Start with '# ' and the title, start every section title with '## ', and leave a blank "
        "line before and after each heading, list, table and code block."
    )

    message = await turn.ask_model(
        [
            {"role": "system", "content": WRITER_PROMPT},
            {"role": "user",   "content": user_content("\n\n".join(parts), turn.images)}
        ],
        WRITER_MAX_TOKENS,
    )
    markdown = (message.content if isinstance(message.content, str) else "").strip()

    # Drop a code fence wrapped around the whole document
    fenced = re.match(r"^```(?:markdown|md)?[ \t]*\n(.*)\n```$", markdown, flags=re.DOTALL)
    if fenced:
        markdown = fenced.group(1).strip()
    if not markdown:
        raise DocumentError("the writer returned an empty document")
    return clean_document_text(markdown)


class _FileTool(Tool):
    """Shared by the tools that attach a file: the per-reply limit, the error wording and the bookkeeping."""

    async def _build(self, args: dict, turn: Turn):
        """Make the file. Returns a BuiltFile, or a ToolResult when it was turned down."""
        raise NotImplementedError

    def _source(self, args: dict) -> str:
        """The diagram source in the arguments, if this tool takes one. Used to point at a bad line."""
        return ""

    async def run(self, args: dict, turn: Turn) -> ToolResult:
        # The model sometimes asks for the same file twice or for a diagram the document
        # already contains. Those are turned down before they cost anything.
        if len(turn.files) >= MAX_FILES_PER_REPLY:
            return ToolResult("error: this reply already has its files. do not make more, just tell them what is attached")
        try:
            built = await self._build(args, turn)
        except DiagramSyntaxError as e:
            logger.warning(f"Tool {self.name} failed: {e}")
            return ToolResult(
                f"error: {e}{_source_line(str(e), self._source(args))}\nFix it and call create_diagram again with the whole "
                "corrected source. Do not paste the source into the chat.",
                call_again=CreateDiagram.name,
            )
        except DocumentError as e:
            logger.warning(f"Tool {self.name} failed: {e}")
            return ToolResult(f"error: {e}")
        except BudgetExceededError:
            raise
        except Exception as e:
            # A bug in a builder should cost them the file, not the whole reply
            logger.error(f"Tool {self.name} crashed: {e!r}")
            return ToolResult("error: the file could not be made because of an internal error")
        if isinstance(built, ToolResult):
            return built

        turn.files.append(built)
        logger.info(f"Built {built.filename} ({len(built.data)} bytes) for {turn.username}")
        return ToolResult(f"done: {built.filename} is attached to your reply. Tell them in one short line, do not repeat its contents.")


class CreateDocument(_FileTool):
    name = "create_document"
    # Documents are built first, so a diagram the document already holds can be skipped
    priority = 0
    definition = {
    "type": "function",
    "function": {
        "name": "create_document",
        "description": (
            "Write a finished document and attach it to the reply as a file. A separate professional "
            "writer produces it from the brief plus the conversation, the replied-to message and any "
            "attached files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["pdf", "docx", "md"],
                    "description": "pdf unless they asked for docx, word or markdown",
                },
                "title": {"type": "string", "description": "Plain title of the document, also used for the filename"},
                "brief": {
                    "type": "string",
                    "description": (
                        "In plain standard English: what the document is, who it is for, what it must "
                        "cover, the tone and length they asked for, and every fact they gave. Do not add "
                        "sections or requirements they did not ask for."
                    ),
                },
                "max_pages": {
                    "type": "integer",
                    "description": "Only when they asked for a page limit, such as 'one page'. Leave it out otherwise.",
                },
            },
            "required": ["format", "title", "brief"],
        },
    },
}

    def prompt(self) -> str:
        return FILES_PROMPT

    async def _build(self, args: dict, turn: Turn):
        fmt = str(args.get("format") or "pdf").lower().lstrip(".")
        if any(f.filename.endswith(f".{fmt}") for f in turn.files):
            return ToolResult(f"error: a {fmt} was already made for this reply, do not make it again")
        # Checked before the writer runs, because writing is the part that costs money
        refusal = file_limit_refusal(turn.file_gate)
        if refusal:
            return ToolResult(refusal)
        title = clean_title(str(args.get("title") or "")) or "Document"
        brief = str(args.get("brief") or "")
        max_pages = args.get("max_pages")
        if not (isinstance(max_pages, int) and 1 <= max_pages <= 20):
            # The model often leaves the limit out even when they said "one page"
            max_pages = page_limit_from(turn.query) or page_limit_from(brief)
        markdown = await write_document(fmt, title, brief, turn, max_pages)
        built = await build_document(fmt, title, markdown, max_pages)
        # A diagram image made earlier in this reply is now inside the document
        turn.files[:] = [f for f in turn.files if not f.filename.endswith(".png")]
        return built


class CreateDiagram(_FileTool):
    name = "create_diagram"
    gave_up = "couldnt get that diagram to render after a few tries. ask again or make it a bit simpler"
    definition = {
    "type": "function",
    "function": {
        "name": "create_diagram",
        "description": "Render a Mermaid diagram to a PNG image and attach it to the reply.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short plain title, used for the filename"},
                "mermaid": {
                    "type": "string",
                    "description": (
                        "Valid Mermaid source, for example starting with 'flowchart TD' or 'sequenceDiagram'. "
                        "Short labels in clean standard wording, plain text with no markdown such as *stars*. "
                        "Double quotes around a label are for flowcharts only. Never quote names in a sequenceDiagram. "
                        "In a sequenceDiagram keep each message under about 40 characters and leave out long URLs "
                        "and query strings. When one box has more than five arrows going out, use flowchart LR."
                    ),
                },
            },
            "required": ["title", "mermaid"],
        },
    },
}

    def _source(self, args: dict) -> str:
        return str(args.get("mermaid") or "")

    async def _build(self, args: dict, turn: Turn):
        if any(not f.filename.endswith(".png") for f in turn.files):
            return ToolResult("skipped: the document made for this reply already contains its diagrams, so no separate image is needed")
        built = await build_diagram(clean_title(str(args.get("title") or "")) or "Diagram", self._source(args))
        # Counted once it has rendered, so a diagram that failed to parse costs them nothing
        refusal = file_limit_refusal(turn.file_gate)
        if refusal:
            return ToolResult(refusal)
        return built

    async def recover(self, text: str, turn: Turn) -> str:
        """
        The model sometimes writes Mermaid source into its message instead of calling
        this tool, mostly after the tool turned a diagram down. Render that source
        so they still get an image. Returns the text to send with it.
        """
        found = split_mermaid(text)
        if not found:
            return text
        source, rest = found
        try:
            built = await build_diagram("Diagram", source)
        except Exception as e:
            logger.warning(f"Pasted diagram left as text: {e!r}")
            return text
        if file_limit_refusal(turn.file_gate):
            return text

        turn.files.append(built)
        logger.info(f"Built {built.filename} ({len(built.data)} bytes) for {turn.username} from pasted source")
        if _WANTS_SOURCE.search(turn.query):
            return text  # they may want the code itself, so it stays beside the image
        return rest if len(rest) > PASTED_DIAGRAM_INTRO_CHARS else ""
