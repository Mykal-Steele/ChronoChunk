import re
import logging
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from config.config import Config

# Setup logging
logger = logging.getLogger(__name__)

# How much of a channel one answer reads. A one-off question reads the last few messages.
# Once people are talking with the bot, it reads everything said since that began.
QUICK_MESSAGES = 6
# Messages from just before the conversation with the bot began, for what led up to it
LEAD_IN = 3
# The most channel messages one answer reads, however long the conversation runs
MAX_MESSAGES = 30
# Older lines between the bot and the person asking, kept when other chatter has pushed them out of view
THREAD_MESSAGES = 6
# The newest messages are read whole. An older one is cut to this many characters.
WHOLE_MESSAGES = 4
OLDER_MESSAGE_CHARS = 300
# Saved exchanges with this person from other days or channels: how many, and how much of each side
PAST_EXCHANGES = 3
PAST_EXCHANGE_CHARS = 160


def _cut(text: str, limit: int) -> str:
    """Text held to limit characters, on one line, with a mark where it was cut."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + " [...]"


class MessageHandler:
    """Keeps what was said in each channel and builds the background the model answers from."""

    def __init__(self, bot=None):
        self.last_channel_messages = {}  # channel_id -> list of recent messages
        self.config = Config
        self.bot = bot
        # Texts the bot sent in place of an answer it could not give. They never go back to the model.
        self.not_answers: deque = deque(maxlen=100)

    def update_channel_history(self, channel_id: str, user_id: str, username: str,
                               content: str, is_bot: bool, is_command: bool = False,
                               timestamp: Optional[datetime] = None, message_id=None,
                               reply_to: str = "", reply_to_id: str = ""):
        """
        Add one message to a channel's history. timestamp is when it was sent, default
        now. message_id is its Discord id. reply_to and reply_to_id are the name and id
        of whoever it replied to, when it was a reply.
        """
        if channel_id not in self.last_channel_messages:
            self.last_channel_messages[channel_id] = []

        # For command-like messages, store without the slash for more natural flow
        content_to_store = content
        if is_command and content.startswith('/') and not content.startswith('//'):
            content_to_store = content[1:] if len(content) > 1 else content

        self.last_channel_messages[channel_id].append({
            "author_id": user_id,
            "author_name": username,
            "is_bot": is_bot,
            "content": content_to_store,
            "timestamp": (timestamp if isinstance(timestamp, datetime) else datetime.now(timezone.utc)).isoformat(),
            "message_id": str(message_id) if message_id is not None else "",
            "reply_to": reply_to,
            "reply_to_id": str(reply_to_id) if reply_to_id else "",
        })

        if len(self.last_channel_messages[channel_id]) > Config.CHANNEL_HISTORY_SIZE:
            self.last_channel_messages[channel_id] = self.last_channel_messages[channel_id][-Config.CHANNEL_HISTORY_SIZE:]

    def recent_bot_replies(self, channel_id: str, count: int) -> List[str]:
        """The bot's own last count messages in a channel, oldest first."""
        mine = [msg["content"] for msg in self.last_channel_messages.get(channel_id, []) if msg["is_bot"]]
        return mine[-count:]

    def replace_channel_history(self, channel_id: str, messages: List[Dict[str, Any]]) -> None:
        """
        Swap the stored history for a fresh snapshot of the channel. messages must be
        oldest first, each a dict of update_channel_history arguments.
        """
        self.last_channel_messages[channel_id] = []
        for msg in messages:
            self.update_channel_history(channel_id=channel_id, **msg)

    @staticmethod
    def _message_age(timestamp: Optional[str]) -> timedelta:
        """How long ago a stored message was sent. A missing or bad timestamp counts as just now."""
        try:
            sent = datetime.fromisoformat(timestamp or "")
        except (TypeError, ValueError):
            return timedelta(0)
        if sent.tzinfo is None:
            sent = sent.astimezone()  # naive timestamps are local time
        return max(datetime.now(timezone.utc) - sent, timedelta(0))

    # Bot messages that must never appear in AI context
    _EXCLUDED_BOT_PATTERNS = (
        # game/command responses
        "tries left",
        "i'm thinking of a number",
        "im thinkin of a number",
        "start guessing with /guess",
        "use /guess to guess it",
        "the number is higher",
        "the number is lower",
        "go higher than",
        "go lower than",
        "game started",
        "game over",
        "yooo you got it",
        "yooo u got it",
        "you already got a game going",
        "u already got a game going",
        "you don't have a game going",
        "u dont have a game going",
        "you don't even have a game going",
        "u dont even have a game going",
        # error / fallback messages — must not be referenced in future turns
        "brain fried, hit me up again",
        "neural nets are fried",
        "rate limited",
        "quota issues",
        "brain just glitched",
        "processor just overheated",
        "brain bill",
        "api quota just got clapped",
        "can't think straight rn",
        "im lagging so hard",
        "brain cells just went on strike",
        "my brain just glitched",
        "something broke on my end",
        "something went wrong",
        "something just broke",
    )

    # Slang and roast phrases the bot overuses. One it used lately is named to the model as worn out.
    # Only wording belongs here. A number or a fact is never something to avoid.
    _TRACKED_SLANG = frozenset({
        "touch grass", "go outside", "skill issue", "airplane mode", "clown energy",
        "clown school", "clown behavior", "ratio", "no cap", "stay mad", "cope harder",
        "down bad", "caught in 4k", "stay losing", "big l", "take the l", "fr fr",
        "deadass", "on god", "mid af", "dog water", "weak energy", "weak sauce",
        "glazing", "stop glazing", "npc energy", "npc behavior", "kindergarten",
        "remedial", "room temp", "brain dead", "cope", "stay cooked", "lowkey cooked",
        "fully cooked", "clowned", "math class", "calculator", "relearn", "basic math",
        "doubling down",
    })

    # Only messages newer than this can be marked as the live conversation
    _FRESH_WINDOW = timedelta(hours=2)

    def _is_excluded_bot_message(self, content: str) -> bool:
        """True for a bot message that is a game, command or error line and should not feed back into AI context."""
        if content in self.not_answers:
            return True
        lower = content.lower()
        return any(p in lower for p in self._EXCLUDED_BOT_PATTERNS)

    def _get_banned_phrases(self, channel_id: str, n_msgs: int = 5) -> List[str]:
        """
        Phrases the bot used in its last n_msgs answers, so the model can be told they
        are worn out. Whole words only, and never anything with a digit in it.
        """
        if channel_id not in self.last_channel_messages:
            return []

        bot_contents = [
            m["content"] for m in self.last_channel_messages[channel_id]
            if m.get("is_bot") and not self._is_excluded_bot_message(m.get("content", ""))
        ][-n_msgs:]

        if not bot_contents:
            return []

        combined_lower = " ".join(bot_contents).lower()
        found: set = set()

        for phrase in self._TRACKED_SLANG:
            if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", combined_lower):
                found.add(phrase)

        # Find pairs of words that appear in 2+ separate messages (catches organic repeats)
        bigram_counts: Counter = Counter()
        for msg in bot_contents:
            words = re.findall(r"\b[^\W\d_]{3,}\b", msg.lower())
            seen_in_this_msg: set = set()
            for i in range(len(words) - 1):
                bg = f"{words[i]} {words[i+1]}"
                if bg not in seen_in_this_msg:
                    bigram_counts[bg] += 1
                    seen_in_this_msg.add(bg)

        for bigram, count in bigram_counts.items():
            if count >= 2:
                found.add(bigram)

        return sorted(found)[:12]

    def _usable_messages(self, channel_id: str, current_id: str) -> List[Dict[str, Any]]:
        """The channel's stored messages the model may read: no empty ones, no error lines, and not the one being answered."""
        usable = []
        for msg in self.last_channel_messages.get(channel_id, []):
            if not msg.get("content"):
                continue
            if current_id and msg.get("message_id") == current_id:
                continue
            if msg.get("is_bot") and self._is_excluded_bot_message(msg["content"]):
                continue
            usable.append(msg)
        return usable

    def _select(self, messages: List[Dict[str, Any]], asker_id: str,
                session_start: Optional[datetime]) -> List[int]:
        """
        Which of the channel's messages one answer reads, as positions in messages.

        The window grows with the conversation: a one-off question gets the last few
        messages, and while people keep talking with the bot it gets everything said
        since that began. Older lines between the bot and the person asking are added,
        so chatter from others cannot make the bot forget what it just told them.
        """
        since = 0
        if session_start is not None:
            for msg in messages:
                try:
                    since += datetime.fromisoformat(msg["timestamp"]) >= session_start
                except (TypeError, ValueError):
                    continue
        window = min(MAX_MESSAGES, max(QUICK_MESSAGES, since + LEAD_IN))
        first_recent = max(0, len(messages) - window)

        thread = []
        if asker_id:
            for position in range(first_recent):
                msg = messages[position]
                if self._message_age(msg.get("timestamp")) > self._FRESH_WINDOW:
                    continue
                theirs = not msg.get("is_bot") and msg.get("author_id") == asker_id
                to_them = msg.get("is_bot") and msg.get("reply_to_id") == asker_id
                if theirs or to_them:
                    thread.append(position)
        return thread[-THREAD_MESSAGES:] + list(range(first_recent, len(messages)))

    def _past_exchanges(self, user_data: Dict[str, Any], shown: List[Dict[str, Any]]) -> List[str]:
        """Saved exchanges with this person that are not in the channel history already, newest last."""
        in_view = " ".join(msg["content"] for msg in shown)
        lines = []
        for exchange in reversed(user_data.get("conversation_history") or []):
            said, answered = str(exchange.get("user_message") or ""), str(exchange.get("bot_response") or "")
            if not said or not answered or answered[:60] in in_view:
                continue
            lines.append(f"  them: {_cut(said, PAST_EXCHANGE_CHARS)} | u: {_cut(answered, PAST_EXCHANGE_CHARS)}")
            if len(lines) == PAST_EXCHANGES:
                break
        return list(reversed(lines))

    def build_conversation_context(self, channel_id: str, user_data: Dict[str, Any], query: str = "",
                                   session_start: Optional[datetime] = None, current_id=None) -> str:
        """
        The background for one answer: who is asking, what the bot knows about them,
        earlier chats with them, and the part of the channel that matters now.

        query is the message being answered and current_id its Discord id, so it is
        not repeated in the history. session_start is when people began talking with
        the bot here, which decides how far back the channel is read.
        """
        context_parts = []
        asker_id = str(user_data.get("user_id") or "") if user_data else ""

        # Who is speaking now (pure data — no instructions embedded in this field)
        if user_data:
            speaker = user_data.get("username", "")
            if speaker:
                context_parts.append(f"RESPONDING TO: {speaker}")

        # Known facts about this person
        if user_data:
            facts = user_data.get("facts", [])
            if facts:
                context_parts.append("\nWHAT YOU KNOW ABOUT THEM (background only, dont bring it up unless it matters to what they just said):")
                for fact in facts[-15:]:
                    context_parts.append(f"  {fact}")

        messages = self._usable_messages(channel_id, str(current_id) if current_id is not None else "")
        positions = self._select(messages, asker_id, session_start)
        shown = [messages[position] for position in positions]

        if user_data:
            past = self._past_exchanges(user_data, shown)
            if past:
                context_parts.append("\nEARLIER CHATS WITH THEM (other days or channels, LOW priority, only if they bring it up):")
                context_parts.extend(past)

        if shown:
            context_parts.append("\nCONVERSATION HISTORY:")
            total = len(messages)
            previous = None
            for position in positions:
                msg = messages[position]
                if previous is not None and position != previous + 1:
                    context_parts.append("(other messages in between left out)")
                previous = position
                position_from_end = total - position  # 1 = most recent
                age = self._message_age(msg.get("timestamp"))

                # Old messages are never top priority, even when they are the
                # last ones in a quiet channel
                if age.days >= 1:
                    day_label = "yesterday" if age.days == 1 else f"{age.days} days ago"
                    prefix = ""
                    age_tag = f"[{day_label}, LOW priority] "
                elif position_from_end <= 5 and age <= self._FRESH_WINDOW:
                    prefix = ">>> "
                    age_tag = ""
                else:
                    prefix = ""
                    age_tag = f"[{position_from_end} msgs ago] "

                speaker = "YOU (ChronoChunk)" if msg.get("is_bot") else msg["author_name"]
                if msg.get("reply_to"):
                    speaker += f" (replying to {msg['reply_to']})"
                content = msg["content"]
                if position_from_end > WHOLE_MESSAGES:
                    content = _cut(content, OLDER_MESSAGE_CHARS)
                context_parts.append(f"{prefix}{age_tag}{speaker}: {content}")

        # Short-reply hint: a message of a few words is about what the bot said last
        bot_msgs = [m for m in shown[-6:] if m.get("is_bot")]
        last_said = query or next((m["content"] for m in reversed(shown[-6:]) if not m.get("is_bot")), "")
        if bot_msgs and last_said and len(last_said.lstrip("/").split()) <= 4:
            stopwords = {
                'like', 'dont', 'just', 'with', 'that', 'this', 'have', 'about',
                'what', 'when', 'where', 'from', 'your', 'been', 'would', 'could',
                'since', 'them', 'they', 'than', 'then', 'some', 'into', 'after',
            }
            topic_words = []
            for word in re.findall(r'\b[a-z]{4,}\b', bot_msgs[-1]["content"].lower()):
                if word not in stopwords and word not in topic_words:
                    topic_words.append(word)
            if topic_words:
                context_parts.append(f"\n(short reply context hint — likely topic: {', '.join(topic_words[:5])})")

        # Wording the bot has leaned on lately. It keeps the name the system prompt knows it by.
        banned = self._get_banned_phrases(channel_id)
        if banned:
            quoted = ", ".join(f'"{p}"' for p in banned)
            context_parts.append(f"\n(BANNED this response — already used recently, find fresh angles: {quoted}. "
                                 "this is about wording only: never change or dodge a fact, a name or a number because of it)")

        return "\n".join(context_parts)
