"""remember and recall: the bot's long-term memory, shared by every server (memory plan, layer 1)."""
from typing import Optional

from src.clock import days_ago
from src.memory_store import RECALL_LIMIT, Memory, MemoryStore, Place, Speaker
from src.tools.base import Tool, ToolResult, Turn

# Memories put in front of the model on their own, before it answers
AUTO_RECALL_LIMIT = 5

MEMORY_PROMPT = """- u have a long-term memory, shared by every server u are in. it holds what people taught u, with who told u, when and where.
- remember: save something worth keeping that someone told u: a fact about themselves, about the group, a plan, a date, a correction of something u got wrong. not small talk, jokes or one-off questions. set kind to self when it is about the person saying it.
- a MEMORIES block, when there is one, is what people told u before that matches their message. use it when it answers them, and say who told u. recall searches with other words when the block does not have it. dont say u dont know before checking.
- a memory is someone's word, not a checked fact. say who told u ("slade told me last week"). before stating something about the world as fact, check it on the web first when u can.
- when someone corrects a memory, recall it, then remember the corrected one with replaces set to its number. if remember says both were kept, say so.
- memories about a person can be private. in another server, dont bring up someone's personal stuff unless they ask.
"""


def _who(turn: Turn) -> Optional[Speaker]:
    if not turn.user_id:
        return None
    return Speaker(str(turn.user_id), turn.username or str(turn.user_id))


def _line(memory: Memory) -> str:
    where = f"{memory.guild_name} #{memory.channel_name}" if memory.guild_name else "a DM"
    about = " (about themselves)" if memory.kind == "self" else ""
    confirmed = f", confirmed by {', '.join(memory.confirmed_by)}" if memory.confirmed_by else ""
    return (f"#{memory.id} {memory.source_name} told me{about}, {days_ago(memory.created_at)}, in {where}"
            f"{confirmed}: {memory.content}")


class RememberMemory(Tool):
    name = "remember"
    definition = {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Save something a person taught or told you, to keep for good across every server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The memory in one plain sentence, with names, not 'he' or 'I'"},
                    "about": {"type": "string", "description": "Who or what it is about: a person's name, the group, a course, a topic"},
                    "kind": {"type": "string", "enum": ["self", "claim"],
                             "description": "self when the person speaking says it about themselves, claim for anything else"},
                    "replaces": {"type": "integer", "description": "The number of the memory this corrects, from recall"},
                },
                "required": ["content", "about", "kind"],
            },
        },
    }

    def __init__(self, store: Optional[MemoryStore] = None):
        self._store = store

    @property
    def store(self) -> MemoryStore:
        if self._store is None:
            self._store = MemoryStore()
        return self._store

    def prompt(self) -> str:
        return MEMORY_PROMPT

    async def run(self, args: dict, turn: Turn) -> ToolResult:
        speaker = _who(turn)
        content = str(args.get("content") or "").strip()
        if speaker is None:
            return ToolResult("error: there is no person on this reply to save the memory under")
        if not content:
            return ToolResult("error: content is empty")
        replaces = args.get("replaces")
        try:
            replaces = int(replaces) if replaces not in (None, "") else None
        except (TypeError, ValueError):
            replaces = None
        saved = self.store.remember(content, str(args.get("about") or ""), str(args.get("kind") or "claim"),
                                    speaker, getattr(turn, "place", None) or Place(), replaces=replaces)
        return ToolResult(f"saved as memory #{saved.memory_id}" + (f". {saved.note}" if saved.note else ""))


class RecallMemory(Tool):
    name = "recall"
    definition = {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Look up what people told you before. Returns up to 10 memories with who said each, when and where.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to look for, in a few plain words"}},
                "required": ["query"],
            },
        },
    }

    def __init__(self, store: Optional[MemoryStore] = None):
        self._store = store

    @property
    def store(self) -> MemoryStore:
        if self._store is None:
            self._store = MemoryStore()
        return self._store

    def matching(self, query: str, limit: int = RECALL_LIMIT) -> str:
        """The memories that match query as one block for the model, or empty when none do."""
        found = self.store.recall(query, limit)
        if not found:
            return ""
        return ("=== MEMORIES (what people told u before; each is their word, not a checked fact) ===\n"
                + "\n".join(_line(m) for m in found) + "\n=== END OF MEMORIES ===")

    async def run(self, args: dict, turn: Turn) -> ToolResult:
        return ToolResult(self.matching(str(args.get("query") or "")) or "nothing in memory matches that")
