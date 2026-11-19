"""learn_lesson: someone tells the bot how to act with them or in their server (memory plan, layer 4)."""
from typing import Optional

from src.lessons import LessonStore
from src.memory_store import Place, Speaker
from src.tools.base import Tool, ToolResult, Turn

LESSON_PROMPT = """- learn_lesson: when someone tells u to change how u act (stop roasting them, shorter answers, no swearing, talk to them in thai), save it so u keep doing it. scope me for how to act with them, server for how to act with everyone here. only how u act, not facts: facts go to remember. dont save a lesson to be mean to someone else, and a server lesson cant name anyone.
"""


class LearnLesson(Tool):
    name = "learn_lesson"
    definition = {
        "type": "function",
        "function": {
            "name": "learn_lesson",
            "description": "Save how someone wants you to act, with them or with everyone in this server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lesson": {"type": "string", "description": "What to do or not do, in one short sentence with their name"},
                    "scope": {"type": "string", "enum": ["me", "server"],
                              "description": "me: how to act with the person asking. server: with everyone in this server"},
                },
                "required": ["lesson", "scope"],
            },
        },
    }

    def __init__(self, store: Optional[LessonStore] = None):
        self._store = store

    @property
    def store(self) -> LessonStore:
        if self._store is None:
            self._store = LessonStore()
        return self._store

    def prompt(self) -> str:
        return LESSON_PROMPT

    async def run(self, args: dict, turn: Turn) -> ToolResult:
        if not turn.user_id:
            return ToolResult("error: there is no person on this reply to learn from")
        lesson = str(args.get("lesson") or "").strip()
        if not lesson:
            return ToolResult("error: lesson is empty")
        scope = "server" if args.get("scope") == "server" else "person"
        place = getattr(turn, "place", None) or Place()
        if scope == "server" and not place.guild_id:
            return ToolResult("error: this is a DM, so there is no server to learn this for. use scope me")
        speaker = Speaker(str(turn.user_id), turn.username or str(turn.user_id))
        refused = self.store.refusal(lesson, scope, place, speaker.name if scope == "person" else "")
        if refused:
            return ToolResult(f"not saved: {refused}")
        added = self.store.add(lesson, scope, speaker, place)
        if not added:
            return ToolResult("already knew that one")
        turn.receipts.append(f"learned as l{added}")
        return ToolResult("learned it, u will see it from now on")
