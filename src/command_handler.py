import discord
from datetime import date
from typing import List, Optional

from src.ai_response_handler import Reply
from src.exceptions import RateLimitError
from src.help_menu import build_help_embed
from src.rate_limiter import RateLimiter
from src.setup_info import setup_summary
from src.stats import stats_summary
from src.tools.courses import courses_summary
from src.message_context import read_channel_messages
from src.usage_guard import get_usage_guard
from src.logger import logger
from src.version import __version__
from config.config import Config

# Sits right under the channel messages, where the model reads it last. In the system
# prompt alone the same rule was not enough to stop it adding steps and advice of its own.
CHANNEL_ANSWER_RULE = (
    "(their request below is about the channel messages above. answer from those messages only. "
    "no facts, steps, tasks, dates or advice that nobody there wrote, unless they ask for ur own take. "
    "if the messages dont say, say that. keep it shorter than the chat it covers.)"
)


class CommandHandler:
    """
    Handles the commands that also work as typed messages: /tldr, /usage, /model, /setup, /stats, /courses and /help.
    Every other command is a slash command, in src/slash_commands.py.
    """

    def __init__(self, bot=None, rate_limiter: Optional[RateLimiter] = None):
        self.bot = bot  # can be None in tests
        self.rate_limiter = rate_limiter if rate_limiter else RateLimiter()
        self.command_handlers = {
            "tldr": self._handle_tldr,
            "usage": self._handle_usage,
            "model": self._handle_model,
            "setup": self._handle_setup,
            "stats": self._handle_stats,
            "courses": self._handle_courses,
            "help": self._handle_help,
        }

    async def handle_command(self, command: str, args: List[str], message: discord.Message, user_id: str):
        """Run a typed command. Returns None for a word that is not one, so the caller treats it as chat."""
        handler = self.command_handlers.get(command)
        if handler:
            return await handler(args, message, user_id)
        return None

    async def _handle_tldr(self, args: List[str], message: discord.Message, user_id: str) -> Optional[Reply]:
        """
        Sum up the recent messages in this channel, like '/tldr 100'. Words after the
        count are a request about those messages instead, like '/tldr 60 what did we
        decide' or '/tldr 60 make a pdf of the design'.
        """
        # As a reply, /tldr means "sum up that message", which the normal chat path does
        if message.reference:
            return None

        # The message is "/tldr", then the count if they gave one, then the request
        skip = 2 if args and args[0].isdecimal() else 1
        count = int(args[0]) if skip == 2 else 50
        pieces = message.content.split(None, skip)
        prompt = pieces[skip].strip() if len(pieces) > skip else ""

        return await self.summarize_channel(
            message.channel, message.author.display_name, user_id, count, before=message, prompt=prompt
        )

    async def summarize_channel(self, channel, username: str, user_id: str, count: int = 50,
                                before=None, prompt: str = "") -> Reply:
        """
        Have the AI read the last count messages in a channel (5 to 200). Without a
        prompt it sums them up. With one it answers the prompt from those messages,
        which can be a normal chat answer or a document or diagram.
        """
        ai_handler = getattr(self.bot, "ai_handler", None)
        if not ai_handler:
            return Reply("that aint wired up yet")

        try:
            self.rate_limiter.check_rate_limit(user_id, "tldr")
        except RateLimitError as e:
            minutes = max(1, round(e.retry_after / 60))
            return Reply(f"u already got a bunch of recaps, try again in about {minutes} min")

        count = max(5, min(count, 200))
        try:
            messages = [msg async for msg in channel.history(limit=count, before=before)]
        except discord.HTTPException as e:
            logger.error(f"Error fetching history for tldr: {e}")
            return Reply("cant read the history in this channel, no perms")

        messages.reverse()  # Discord returns newest first
        # Reads the pictures and files posted in those messages too
        channel_messages = await read_channel_messages(messages)
        if not channel_messages.text:
            return Reply("nothing in here to catch up on")
        # A plain recap is chat text. Files are only possible when they asked for something.
        return await ai_handler.generate_reply(
            prompt or "tldr of those channel messages, what did i miss",
            "", username, user_id,
            attached_context=f"{channel_messages.text}\n{CHANNEL_ANSWER_RULE}",
            images=channel_messages.images,
            allow_files=bool(prompt),
            file_gate=lambda: self.rate_limiter.check_rate_limit(user_id, "file"),
            web_gate=lambda: self.rate_limiter.check_rate_limit(user_id, "web"),
            course_gate=lambda: self.rate_limiter.check_rate_limit(user_id, "course"),
        )

    async def _handle_help(self, args: List[str], message: discord.Message, user_id: str) -> Optional[discord.Embed]:
        """Show the help menu for a bare '/help'. With more words, like '/help me write this', it is chat."""
        return None if args else build_help_embed()

    async def _handle_usage(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """Show how much of the AI budget is used"""
        return self.usage_summary(user_id)

    async def _handle_model(self, args: List[str], message: discord.Message, user_id: str) -> Optional[str]:
        """Show the AI models for a bare '/model'. With more words, like '/model this for me', it is chat."""
        return None if args else self.model_summary()

    async def _handle_setup(self, args: List[str], message: discord.Message, user_id: str) -> Optional[str]:
        """Show where and how the bot runs for a bare '/setup'. With more words it is chat."""
        return None if args else self.setup_summary()

    async def _handle_stats(self, args: List[str], message: discord.Message, user_id: str) -> Optional[str]:
        """Show everything the bot has used so far for a bare '/stats'. With more words it is chat."""
        return None if args else stats_summary()

    async def _handle_courses(self, args: List[str], message: discord.Message, user_id: str) -> Optional[str]:
        """Show the course material the bot can search for a bare '/courses'. With more words it is chat."""
        return None if args else courses_summary()

    def setup_summary(self) -> str:
        """Where the bot runs and the settings it runs with, for other developers."""
        ai_handler = getattr(self.bot, "ai_handler", None)
        return setup_summary(getattr(ai_handler, "deployment", None) or "unknown")

    def model_summary(self) -> str:
        """One line on which AI models the bot is running on right now."""
        ai_handler = getattr(self.bot, "ai_handler", None)
        chat_model = getattr(ai_handler, "deployment", None) or "unknown"
        background = Config.AI_BACKGROUND_DEPLOYMENT or chat_model
        return (
            f"chat model: `{chat_model}` (reasoning: {Config.AI_CHAT_REASONING_EFFORT}). "
            f"background model for memory: `{background}`. bot version {__version__}"
        )

    def usage_summary(self, user_id: str) -> str:
        """What one person has left of each allowance, and how much of their AI limit and the bot's is used."""
        lines = ["left right now"]
        for label, action in USAGE_ROWS:
            left, limit, window, wait = self.rate_limiter.remaining(user_id, action)
            line = f"{label:<12} {left:>3} of {limit:<3}  {_window_words(window)}"
            if left == 0:
                line += f", more in {_duration(wait)}"
            lines.append(line)

        guard = get_usage_guard()
        mine, bot = guard.user_summary(user_id), guard.summary()
        lines += ["", "ai overall"]
        if mine["unlimited"]:
            lines.append("today    no limit for u")
        else:
            lines.append(f"today    {_bar(mine['day_cost'], mine['daily_budget'])}   resets in {_duration(mine['seconds_to_reset'])}")
            lines.append(f"7 days   {_bar(mine['week_cost'], mine['weekly_budget'])}")
        lines.append(f"bot      {_bar(bot['cycle_cost'], bot['monthly_budget'])}   this month, resets {_short_date(bot['resets_on'])}")
        return "**ur usage**\n```\n" + "\n".join(lines) + "\n```"


# The allowances /usage lists, as (label, key in Config.RATE_LIMITS)
USAGE_ROWS = (
    ("messages", "chat"),
    ("pdfs read", "pdf"),
    ("files made", "file"),
    ("web lookups", "web"),
    ("course asks", "course"),
    ("recaps", "tldr"),
)


def _window_words(seconds: float) -> str:
    if seconds >= 86400:
        return "per day"
    if seconds >= 3600:
        return "per hour"
    return f"per {int(seconds // 60)} min"


def _duration(seconds: float) -> str:
    """A wait in the two largest units that matter, like "6h 10m" or "4m"."""
    minutes = max(1, int(round(seconds / 60)))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _bar(used: float, limit: float) -> str:
    """A ten-step bar and a percentage."""
    share = min(1.0, used / limit) if limit > 0 else 1.0
    filled = int(round(share * 10))
    # Any use at all shows as one step, so the bar never reads as untouched
    if used > 0 and filled == 0:
        filled = 1
    return f"{'▓' * filled}{'░' * (10 - filled)} {int(round(share * 100)):>3}%"


def _short_date(iso_day: str) -> str:
    day = date.fromisoformat(iso_day)
    return f"{day:%b} {day.day}".lower()
