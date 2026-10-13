"""
Who is talking to the bot in each channel right now.

Two things come from it. When several people are talking to the bot at once, an
answer pings the person it is for. And for a short while after someone called the
bot, it may answer a message nobody addressed to it, the way a person who is part
of a conversation would. Everything here is held in memory and is gone on a restart.
"""
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from config.config import Config

# Two or more people calling the bot within this many seconds is a crowd
CROWD_SECONDS = 60
# Calls no further apart than this are one conversation with the bot. A call after a longer quiet starts a new one.
SESSION_GAP_SECONDS = 300
# After one call the bot weighs exactly one uncalled message, and answers it or stays out.
# It gets another go when someone calls it again.
MAX_WEIGHED_PER_CALL = 1
MAX_UNCALLED_REPLIES_PER_CALL = 1

# What the model answers when it decides to stay out of it
STAY_OUT = "SKIP"

# Goes in front of a message nobody addressed to the bot. {name} is who wrote it.
UNCALLED_NOTE = (
    "(nobody called u for this one. {name} just said it in the channel while u are part of the conversation. "
    "answer it only if a friend sitting in the chat would naturally jump in: it is about what u were just "
    "talking about, it is about u, or it is an easy thing to react to. keep it short. if it is not for u, is "
    "between other people, or u have nothing worth adding, answer with exactly SKIP and nothing else. "
    "when in doubt, SKIP.)"
)


_BANG_CALL = re.compile(r"!\s?(?=[^\W_])")


def is_call(content: str) -> bool:
    """
    Whether a message calls the bot by how it starts: with a slash, or with an
    exclamation mark for servers where a slash sets off another bot's commands. An
    outcry such as "!!!" is not a call.
    """
    return content.startswith("/") or bool(_BANG_CALL.match(content))


def as_slash(content: str) -> str:
    """An exclamation-mark call written as the slash call it stands for. Anything else comes back as it is."""
    return _BANG_CALL.sub("/", content, count=1) if _BANG_CALL.match(content) else content


def stays_out(text: str) -> bool:
    """True when the model's answer to an uncalled message means it is not joining in."""
    words = text.strip().strip("\"'`.!*_ ").upper()
    return not words or words == STAY_OUT or words.startswith(STAY_OUT + " ") or words.startswith(STAY_OUT + "\n")


@dataclass
class _Channel:
    # user id -> when they last called the bot
    callers: Dict[str, float] = field(default_factory=dict)
    last_call: float = float("-inf")
    # When the conversation with the bot that is going on now began
    session_start: Optional[datetime] = None
    weighed: int = 0
    answered: int = 0
    # True while the bot is deciding about one uncalled message, so a burst of messages costs one model call
    deciding: bool = False
    # Whether the bot takes its chance to join in after the last call. None until the coin is flipped.
    joins: Optional[bool] = None


class Conversation:
    """Tracks, per channel, who called the bot and how often it has joined in since."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, chance: Callable[[], float] = random.random):
        self._clock = clock
        self._chance = chance
        self._channels: Dict[str, _Channel] = {}

    def _channel(self, channel_id) -> _Channel:
        return self._channels.setdefault(str(channel_id), _Channel())

    def called(self, channel_id, user_id, at: Optional[datetime] = None) -> None:
        """
        Someone called the bot here: a / or ! message, a ping, a reply to it or the /chat
        command. at is when their message was sent.
        """
        channel = self._channel(channel_id)
        now = self._clock()
        if now - channel.last_call > SESSION_GAP_SECONDS or channel.session_start is None:
            channel.session_start = at if isinstance(at, datetime) else datetime.now(timezone.utc)
        channel.callers = {user: at for user, at in channel.callers.items() if now - at <= CROWD_SECONDS}
        channel.callers[str(user_id)] = now
        channel.last_call = now
        channel.weighed = channel.answered = 0
        channel.joins = None

    def session_start(self, channel_id) -> Optional[datetime]:
        """When the conversation with the bot in this channel began, or None when none is going on."""
        channel = self._channel(channel_id)
        if self._clock() - channel.last_call > SESSION_GAP_SECONDS:
            return None
        return channel.session_start

    def is_crowd(self, channel_id) -> bool:
        """True when more than one person has called the bot here in the last minute."""
        now = self._clock()
        return sum(1 for at in self._channel(channel_id).callers.values() if now - at <= CROWD_SECONDS) > 1

    def may_join_in(self, channel_id) -> bool:
        """
        Whether the bot may weigh a message here that nobody addressed to it: someone
        called it a moment ago, it has not used up what one call allows, and the coin
        flipped for that call landed on joining in.
        """
        window = Config.JOIN_IN_SECONDS
        channel = self._channel(channel_id)
        open_now = (window > 0 and not channel.deciding
                    and self._clock() - channel.last_call <= window
                    and channel.weighed < MAX_WEIGHED_PER_CALL
                    and channel.answered < MAX_UNCALLED_REPLIES_PER_CALL)
        if not open_now:
            return False
        # One flip per call, made before anything is read or any model is asked
        if channel.joins is None:
            channel.joins = self._chance() < Config.JOIN_IN_CHANCE
        return channel.joins

    def start_deciding(self, channel_id) -> None:
        channel = self._channel(channel_id)
        channel.deciding = True
        channel.weighed += 1

    def done_deciding(self, channel_id, answered: bool) -> None:
        channel = self._channel(channel_id)
        channel.deciding = False
        channel.answered += answered
