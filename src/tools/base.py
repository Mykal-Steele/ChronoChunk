"""What every tool shares: the state of the reply being built, and the interface a tool implements."""
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from src.document_builder import BuiltFile


def user_content(text: str, images: List[str]):
    """A user turn for the model: plain text, or text plus image parts when there are images (data URLs)."""
    if not images:
        return text
    content: List[dict] = [{"type": "text", "text": text}]
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in images)
    return content


@dataclass
class Turn:
    """
    One reply in the making: what it is built from, what the tools have produced so
    far, and what it has cost. Every tool called for the reply gets the same Turn.
    """
    query: str
    username: str
    history: str
    attached: str
    images: List[str]
    # Files made for this reply, attached when it is sent
    files: List[BuiltFile] = field(default_factory=list)
    # Called before a file is made. Raises RateLimitError when this user has made too many.
    file_gate: Optional[Callable[[], None]] = None
    # Called before each web search or opened link. Raises RateLimitError when they have had their daily lookups.
    web_gate: Optional[Callable[[], None]] = None
    # Runs a model call that is counted against this reply: ask_model(messages, max_tokens)
    ask_model: Optional[Callable[[list, int], Awaitable]] = None
    # Search results and pages read for this reply, for tools that run later (the document writer)
    findings: List[str] = field(default_factory=list)
    lookups: int = 0
    calls: int = 0
    cost: float = 0.0
    started: float = field(default_factory=time.monotonic)
    model_seconds: float = 0.0  # time spent waiting for the model, out of the whole reply
    tool_seconds: float = 0.0  # time spent inside tools, not counting the model calls they make


@dataclass
class ToolResult:
    """What a tool hands back: the text the model reads next."""
    text: str
    # Name of a tool the model has to call again on its next turn, set when a diagram did not parse
    call_again: Optional[str] = None


class Tool:
    """
    One thing the chat model can do. Subclasses set name and definition and implement run.
    """
    name = ""
    # The function schema sent to the model
    definition: dict = {}
    # When the model calls several tools at once, lower numbers run first
    priority = 1
    # Sent to the user when the model kept being told to call this tool again and never got it right
    gave_up = ""

    def available(self) -> bool:
        """False hides the tool from the model, for example when its API key is not configured."""
        return True

    def prompt(self) -> str:
        """Lines added to the system prompt while the tool is offered."""
        return ""

    async def run(self, args: dict, turn: Turn) -> ToolResult:
        raise NotImplementedError

    async def recover(self, text: str, turn: Turn) -> str:
        """
        Called with the model's final text when the reply has no file. A tool can use it to
        save a reply where the model wrote the tool's input into chat. Returns the text to send.
        """
        return text
