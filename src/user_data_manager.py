"""What the bot remembers about people, and how it picks facts out of what they say."""
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from config.config import Config
from src import usage_context
from src.usage_guard import BudgetExceededError, guard_client
from src.user_store import UserStore, clean_user_id

logger = logging.getLogger(__name__)

FACT_PROMPT = """
Extract ONLY definite personal facts about the user from this message.
Respond with ONLY a JSON array of facts in second-person format.

Example message: "I am 21 years old and I hate math"
Example response: ["You are 21 years old", "You hate math"]

Example message: "I love coffee so much"
Example response: ["You love coffee"]

Example message: "My cat just died"
Example response: ["Your cat died"]

Example message: "I hate Monday"
Example response: ["You hate Monday"]

Example message: "My friend is a doctor"
Example response: ["Your friend is a doctor"] 

Example message: "I don't eat meat anymore"
Example response: ["You do not eat meat"]

Example message: "wsg"
Example response: []

Example message: "read what this msg say"
Example response: []

Example message: "ur so dumb lol"
Example response: []

IMPORTANT RULES:
- ONLY lasting facts the user states about their own life: name, age, where they live, school or job, hobbies, likes and dislikes, pets, family and friends, things they own or are building
- Return [] for greetings, jokes, insults, trolling, crude or sexual remarks, questions, requests, and anything aimed at the bot
- NEVER describe the message itself or how the user behaved. No "You said ...", "You asked ...", "You used ...", "You want the bot to ..."
- Include any personal facts (preferences, status, relationships, possessions)
- Use second-person format ("You are", "You have", "You like", "Your friend is")
- Return as a plain JSON array ["fact 1", "fact 2"]
- Return empty array [] if no clear facts present
- DO NOT miss obvious facts - especially likes, dislikes, and relationships

USER MESSAGE: """

# A first word that means the message was a command to the bot and holds no facts
_COMMAND_WORDS = ("chat", "code", "help", "summary", "profile", "stats")

# Things a person has one of. A new fact about one replaces the old one. Likes,
# dislikes and belongings are not here, because people have many of those.
_SINGLE_VALUED = {
    "age": ("years old",),
    "location": ("you live in", "you are from", "you moved to"),
}


class UserDataManager:
    """Remembers people: stores their data through UserStore and extracts facts with the AI."""

    def __init__(self, store: Optional[UserStore] = None):
        self.store = store or UserStore()
        try:
            endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
            api_key = os.environ.get("AZURE_OPENAI_KEY", "")
            self.deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
            # Fact extraction runs behind the reply. On gpt-5-nano with minimal reasoning it matched
            # gpt-5-mini on a test set, about 3 times faster and 18 times cheaper. Without a
            # background deployment it stays on the main one at low reasoning.
            self.fact_deployment = Config.AI_BACKGROUND_DEPLOYMENT or self.deployment
            self.fact_reasoning_effort = "minimal" if Config.AI_BACKGROUND_DEPLOYMENT else "low"
            self.ai_client = guard_client(AsyncOpenAI(base_url=endpoint, api_key=api_key))
        except Exception as e:
            logger.error(f"Failed to initialize fact extraction model: {e}")
            self.ai_client = None

    def load_user_data(self, user_id: str, username: str = None) -> Dict[str, Any]:
        """Everything stored about a user. A new user is created on first sight."""
        return self.store.get_profile(user_id, username)

    def forget_user(self, user_id: str) -> None:
        """Delete everything stored about a user: facts, topics and saved conversations."""
        self.store.forget(user_id)
        logger.info(f"Deleted all stored data for {clean_user_id(user_id)}")

    async def add_conversation(self, user_id: str, message_content: str, bot_response: str, username: str = None) -> None:
        """Save one exchange, then pick any facts out of what they said."""
        self.store.add_conversation(user_id, message_content, bot_response, username)
        await self.extract_and_save_facts(user_id, message_content, username)

    async def extract_and_save_facts(self, user_id: str, message_content: str, username: str = None) -> bool:
        """Have the AI pick lasting facts about the user out of one message, and store the new ones."""
        # "/hello" style messages lose the slash
        if message_content.startswith('/') and len(message_content) > 1:
            message_content = message_content[1:].strip()
        words = message_content.split()
        if len(words) < 2 or words[0].lower() in _COMMAND_WORDS or not self.ai_client:
            return False

        # Read before the model call. If they run /forget while it is thinking, the
        # facts from this older message are not written back.
        profile = self.store.get_profile(user_id, username)
        try:
            with usage_context.feature("facts"):
                resp = await self.ai_client.chat.completions.create(
                    model=self.fact_deployment,
                    messages=[{"role": "user", "content": FACT_PROMPT + message_content}],
                    max_completion_tokens=1500,
                    reasoning_effort=self.fact_reasoning_effort,
                )
            facts_text = (resp.choices[0].message.content or "").strip()
        except BudgetExceededError:
            return False  # they or the bot are out of budget. the reply already said so.
        except Exception as e:
            if "429" not in str(e):  # a rate limit is expected now and then
                logger.error(f"Unexpected error in extract_and_save_facts: {e}")
            return False

        # The model sometimes wraps the JSON in a code fence
        if "```json" in facts_text:
            facts_text = facts_text.split("```json")[1].split("```")[0].strip()
        elif "```" in facts_text:
            facts_text = facts_text.split("```")[1].strip()
        try:
            found = json.loads(facts_text)
        except json.JSONDecodeError:
            logger.warning(f"Fact extraction returned invalid JSON (raw: {facts_text[:200]!r})")
            return False
        if not isinstance(found, list):
            return False

        add, remove_ids = self._sort_new_facts(profile["facts"], [f for f in found if isinstance(f, str) and f.strip()])
        if not add:
            return False
        return self.store.change_facts(user_id, add, remove_ids, extracted_from=message_content,
                                       if_created_at=profile["created_at"])

    @staticmethod
    def _sort_new_facts(existing: List[dict], found: List[str]) -> Tuple[List[str], List[int]]:
        """
        Decide what to do with freshly extracted facts. Returns the ones to add and
        the ids of stored facts they replace. A fact already stored is dropped. A new
        age or home replaces the old one.
        """
        add: List[str] = []
        remove_ids: List[int] = []
        known = {fact["content"].lower() for fact in existing}
        for fact in found:
            lowered = fact.lower()
            if lowered in known:
                continue
            known.add(lowered)
            add.append(fact)
            for keywords in _SINGLE_VALUED.values():
                if any(keyword in lowered for keyword in keywords):
                    remove_ids.extend(
                        old["id"] for old in existing
                        if old["id"] not in remove_ids and any(keyword in old["content"].lower() for keyword in keywords))
        return add, remove_ids

    def remove_fact(self, user_id: str, fact_to_remove: str, username: str = None) -> bool:
        """Delete every stored fact that contains the given words. Returns whether any did."""
        if not fact_to_remove:
            return False
        profile = self.store.get_profile(user_id, username)
        matching = [fact["id"] for fact in profile["facts"] if fact_to_remove.lower() in fact["content"].lower()]
        return bool(matching) and self.store.change_facts(user_id, remove_ids=matching)

    def get_user_summary(self, user_id: str, username: str = None) -> str:
        """What the bot knows about a user, for /info. No conversation counts or dates."""
        profile = self.store.get_profile(user_id, username)
        parts: List[str] = []
        facts = [f"- {fact['content']}" for fact in profile["facts"] if fact["content"]]
        if facts:
            parts.append("Things I know about you:")
            parts.extend(facts)
        if profile["topics_of_interest"]:
            parts.append("\nTopics you're interested in:")
            parts.append(", ".join(profile["topics_of_interest"]))
        return "\n".join(parts) if parts else "I don't have any information about you yet."
