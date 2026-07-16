import asyncio
import random
import re
import logging
from openai import AsyncOpenAI
from typing import List, Optional
from config.ai_config import PERSONALITY_PROMPT
from collections import deque

logger = logging.getLogger(__name__)

# Strip the old template placeholders at the bottom of the personality prompt
_clean_personality = re.sub(
    r'\{conversation_history\}.*$', '', PERSONALITY_PROMPT, flags=re.DOTALL
).strip()

_SYSTEM_PROMPT = _clean_personality + """

CONVERSATION CONTINUITY RULES:
1. ALWAYS stay on the SAME TOPIC when responding to follow-up questions
2. If asked "why", "why tho", or "more reason", explain YOUR previous statements in your own style
3. When a user asks about "which one", refer to options YOU mentioned previously
4. Short user messages (1-5 words) are ALWAYS a follow-up to what YOU just said
5. NEVER randomly change the subject
6. READ the conversation history before responding

CRITICAL — WHO YOU ARE TALKING TO:
- In the conversation history, "USER (name):" labels mark messages FROM the person you are talking TO directly
- That person IS the one sending you the current message — they are RIGHT THERE in the chat with you
- NEVER refer to them by name in third person (e.g. NEVER say "tell [their name]..." or "[their name] should...")
- Always address them directly as "u", "ur", "bro", "my g", etc.

CRITICAL — DO NOT VOLUNTEER BOT FEATURES:
- NEVER bring up the guessing game, number game, tries remaining, or any game state in casual chat
- NEVER mention /game, /guess, /music, /skip, or any other bot commands unprompted
- These features exist but you do NOT advertise them or bring them up randomly
- Only discuss game state if the user is actively playing and their message is about the game
"""


class AIResponseHandler:
    """Handles all AI response generation logic"""

    def __init__(self, endpoint: str, api_key: str, deployment: str,
                 important_topics: List[str], api_throttler=None, user_data_manager=None):
        self.important_topics = important_topics
        self.api_throttler = api_throttler
        self.user_data_manager = user_data_manager

        self.response_cache = {}
        self.cache_size = 50

        self.ai_client = AsyncOpenAI(base_url=endpoint, api_key=api_key)
        self.deployment = deployment

        self.fallback_responses = deque([
            "yo my neural nets are fried rn, gimme a sec",
            "bruh i think im gettin rate limited, one sec",
            "damn, quota issues again? my dev needs to pay up fr",
            "ngl my brain just glitched for a sec, try again?",
            "lmao my processor just overheated, brb",
            "yo whoever coded me forgot to pay the brain bill 😭",
            "shit i think my dev's api quota just got clapped lol",
            "damn, can't think straight rn, try again in a bit?",
            "bruh im lagging so hard rn, gimme a min",
            "my brain cells just went on strike fr fr"
        ], maxlen=10)

    def extract_important_topics(self, message_content: str) -> List[str]:
        if not message_content:
            return []
        message_lower = message_content.lower()
        return [topic for topic in self.important_topics if topic in message_lower]

    def _format_ai_response(self, raw_response: str) -> str:
        """Light post-processing — preserve length and personality, just clean up artifacts."""
        if not raw_response:
            return "brain fried, hit me up again"

        ai_response = raw_response.strip()

        # Remove any leading role labels the model might add
        ai_response = re.sub(
            r'^(You:|Your response:|ChronoChunk:|Response:|Bot:)\s*',
            '', ai_response, flags=re.IGNORECASE
        )

        # Fix excessive punctuation (3+ → 2)
        ai_response = re.sub(r'\?{3,}', '???', ai_response)
        ai_response = re.sub(r'!{3,}', '!!!', ai_response)

        # Fix space-before-punctuation (e.g. "word , other" → "word, other")
        ai_response = re.sub(r'\s+([.,])', r'\1', ai_response)

        # Manage emojis — if model drops 3+ emojis, cull to 2 max
        emoji_pattern = re.compile(
            r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF'
            r'\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U00002702-\U000027B0]+'
        )
        emojis_found = emoji_pattern.findall(ai_response)
        if len(emojis_found) > 2:
            # Keep first 2 occurrences, strip the rest
            count = 0
            def _keep_two(m):
                nonlocal count
                count += 1
                return m.group() if count <= 2 else ''
            ai_response = emoji_pattern.sub(_keep_two, ai_response)

        return ai_response.strip()

    async def generate_response(self, query: str, conversation_history: str,
                                username: str, user_id: str = None) -> str:
        """Generate AI response using Azure OpenAI chat completions."""
        # Inject explicit context for short follow-up questions
        if len(query.split()) <= 5 and conversation_history:
            last_bot = last_user = ""
            for line in reversed(conversation_history.split('\n')):
                if line.startswith("BOT (ChronoChunk):") and not last_bot:
                    last_bot = line.replace("BOT (ChronoChunk):", "").strip()
                elif line.startswith("USER") and ":" in line and not last_user:
                    last_user = line.split(":", 1)[1].strip()
                if last_bot and last_user:
                    break
            if last_bot:
                conversation_history = (
                    f'[FOLLOW-UP CONTEXT]\n'
                    f'Their last message: "{last_user}"\n'
                    f'Your last response: "{last_bot}"\n'
                    f'Current follow-up: "{query}"\n'
                    f'Stay on the exact same topic. If they ask "why", explain your last statement.\n\n'
                ) + conversation_history

        # Cache check
        cache_key = f"{query}|{conversation_history[-100:] if conversation_history else ''}"
        if cache_key in self.response_cache:
            return self.response_cache[cache_key]

        try:
            clean_query = query[1:].strip() if query.startswith('/') and len(query) > 1 else query

            user_parts = []
            if conversation_history:
                user_parts.append(conversation_history)
            user_parts.append(f'[{username} is talking to you right now — respond to THEM directly, use "u"/"ur", never use their name or refer to them in third person]: "{clean_query}"')

            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": "\n\n".join(user_parts)}
            ]

            resp = await self.ai_client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                max_completion_tokens=2000,  # reasoning model — needs budget for internal thinking + response
            )
            raw = resp.choices[0].message.content or ""
            formatted = self._format_ai_response(raw)

            self.response_cache[cache_key] = formatted
            if len(self.response_cache) > self.cache_size:
                for k in list(self.response_cache.keys())[:-self.cache_size]:
                    self.response_cache.pop(k, None)

            return formatted

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error generating AI response: {error_msg}")
            if "429" in error_msg or "quota" in error_msg.lower() or "rate" in error_msg.lower():
                fallback = self.fallback_responses[0]
                self.fallback_responses.rotate(1)
                return fallback
            return "my brain just glitched fr, try again in a sec 💀"
