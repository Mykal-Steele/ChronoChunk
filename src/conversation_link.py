"""
One judgment call that decides whether a new message still belongs to the chat
before it, after a long quiet gap.

Meaning is not shared words. A joke about Go and a question about the bot's
version can use the same words and still be two different conversations. The
model reads both sides and says whether the new message continues the earlier
one, so nothing is matched against a list or a pattern.
"""
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

# A gap longer than this makes the earlier chat a candidate for being unrelated
LIVE_WINDOW = timedelta(hours=1)

LINK_CHECK_TOOL = "check_conversation_link"


@dataclass
class ConversationLink:
    """What the relevance check read from the two messages."""
    related: bool = False
    # A short reading of what the new message is about, for the answering model
    topic: str = ""


def link_tool() -> dict:
    """The forced tool that returns the judgment. It asks for meaning, not matching words."""
    return {
        "type": "function",
        "function": {
            "name": LINK_CHECK_TOOL,
            "description": "Decide whether a new Discord message continues the earlier conversation "
                           "or starts a separate one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "related": {
                        "type": "boolean",
                        "description": "True only when the new message clearly continues, answers or "
                                       "refers back to the earlier conversation. False when it changes "
                                       "topic, asks a separate question, or only shares a word or a joke.",
                    },
                    "topic": {
                        "type": "string",
                        "description": "One short sentence on what the new message is actually about, "
                                       "including what 'this', 'that' or 'it' points at.",
                    },
                },
                "required": ["related", "topic"],
                "additionalProperties": False,
            },
        },
    }


def link_prompt(gap: str) -> str:
    """The system prompt for the check. It judges meaning in context, never shared wording."""
    return f"""You judge one new Discord message against the earlier chat it lands in. You do not answer it.

The channel has been quiet for {gap}. Read the earlier conversation and the new message, then decide:

related
True only when the new message clearly continues the earlier conversation: it answers a question from it,
asks a follow-up about the same thing, or refers back to something said there.
False when it starts a separate topic or a separate question, even if it uses a word, a name or a joke
from the earlier chat. Shared wording alone is not a link. A short question about the bot itself, a
command, or something new in the room is a fresh topic unless it explicitly points back.

topic
One short sentence on what the new message is actually about, keeping what 'this', 'that' or 'it' means.
"""


def link_question(conversation_history: str, query: str) -> str:
    """The user turn: the earlier chat, then the new message."""
    history = conversation_history[-3000:] if conversation_history else ""
    return f"EARLIER CONVERSATION:\n{history}\n\nNEW MESSAGE:\n\"{query}\""


def parse_link(arguments: str) -> Optional[ConversationLink]:
    """Read the forced tool arguments. Malformed output means no judgment."""
    try:
        data = json.loads(arguments)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("related"), bool):
        return None
    topic = data.get("topic")
    return ConversationLink(related=data["related"],
                            topic=(topic.strip()[:300] if isinstance(topic, str) else ""))
