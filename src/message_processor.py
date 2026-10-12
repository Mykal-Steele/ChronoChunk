import asyncio
import io
import logging
import re
import time
import discord
from contextlib import asynccontextmanager
from typing import Callable, AsyncIterator, Optional, Set
from src.ai_response_handler import AIResponseHandler, Reply
from config.config import Config
from src import usage_context
from src.command_handler import CommandHandler
from src.conversation import UNCALLED_NOTE, Conversation, stays_out
from src.exceptions import RateLimitError
from src.message_handler import MessageHandler
from src.rate_limiter import RateLimiter
from src.timing import Stopwatch
from src.user_data_manager import UserDataManager
from src.channel_settings import get_channel_settings
from src.message_context import build_attached_context, history_text, resolve_message_link
from src.server_emoji import (
    QUIET_REPLIES, REACTION_GAP_SECONDS, Rendered, asked_about_emoji, render, usable_emoji, used_custom_emoji,
)
import traceback
from typing import List

# Setup logging
logger = logging.getLogger(__name__)

# How many channel messages to load as context when the bot answers
HISTORY_FETCH_LIMIT = 50

# The bot quotes and sums up other people's messages, so what it sends must never ping anyone
NO_PINGS = discord.AllowedMentions.none()
# Pings the person a reply is for and nobody else. Used when several people are talking to the bot at once.
PING_AUTHOR = discord.AllowedMentions(everyone=False, users=False, roles=False, replied_user=True)

# Stands in for the text when someone only pings the bot or types a bare /chat
NO_TEXT = "(no text, they just want u to look at what they replied to or attached)"


@asynccontextmanager
async def typing_in_background(channel) -> AsyncIterator[None]:
    """
    Show the typing indicator for as long as the block runs, without waiting for Discord
    to confirm it. Starting it is a request of its own, about 0.3s on the server and now
    and then over 2s, and the answer does not depend on it.
    """
    async def keep_typing() -> None:
        try:
            async with channel.typing():
                await asyncio.Event().wait()  # until the block ends and cancels this
        except discord.HTTPException as e:
            # No indicator is not worth losing the answer over
            logger.warning(f"Could not show the typing indicator: {e}")

    task = asyncio.create_task(keep_typing())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# Given to a member the first time the bot answers them
FIRST_USE_ROLE = "I Love Chrono <3"


async def give_first_use_role(guild: Optional[discord.Guild], user_id: int) -> None:
    """Give the first-use role to a member who does not have it yet. Does nothing in DMs."""
    if not guild:
        return
    role = discord.utils.get(guild.roles, name=FIRST_USE_ROLE)
    if not role:
        logger.warning(f"'{FIRST_USE_ROLE}' role not found in guild, check the role name matches exactly")
        return
    # A full Member object is needed so .roles is filled in
    member = guild.get_member(user_id)
    if not member:
        logger.warning(f"Could not resolve member {user_id} from guild cache")
        return
    if role in member.roles:
        return
    try:
        await member.add_roles(role)
        logger.info(f"Assigned '{FIRST_USE_ROLE}' role to {member.display_name}")
    except discord.Forbidden:
        logger.warning(f"Missing permission to assign '{FIRST_USE_ROLE}', the bot's role must be above it")
    except Exception as e:
        # The role is a nicety. Failing to give it must never turn a sent reply into an error.
        logger.error(f"Error assigning '{FIRST_USE_ROLE}' role: {e}")


class MessageProcessor:
    """Processes incoming Discord messages and handles routing them correctly"""
    
    def __init__(self, bot, message_handler: MessageHandler, ai_response_handler: AIResponseHandler,
                 user_data_manager: UserDataManager, game_manager=None,
                 command_handler: Optional[CommandHandler] = None, rate_limiter: Optional[RateLimiter] = None,
                 text_commands: Optional[Set[str]] = None):
        """
        Initialize message processor with required components.

        text_commands limits which typed "/word" messages go to the command handler.
        Everything else typed with a slash is chat. None sends every known command there.
        """
        self.bot = bot
        self.message_handler = message_handler
        self.ai_handler = ai_response_handler  # Store with internal name ai_handler
        self.user_data_manager = user_data_manager
        self.game_manager = game_manager
        self.command_handler = command_handler  # Add this line to store the command handler
        self.rate_limiter = rate_limiter
        self.text_commands = text_commands
        # channel id -> when the bot last put a reaction on a message there
        self._last_reaction: dict = {}
        # Who is talking to the bot in each channel right now
        self.conversation = Conversation()
        self._channel_locks: dict = {}
    
    async def process_message(self, message: discord.Message) -> None:
        """Process an incoming Discord message - Properly capture ALL channel messages"""
        try:
            # Skip bot messages, except the test bot's in the test channel
            if message.author.bot and not self._is_test_message(message):
                return
                
            # Get basic info
            content = message.content
            user_id = str(message.author.id)
            # Every model call this message leads to is counted against its author
            usage_context.set_user(user_id)
            username = message.author.display_name
            channel_id = str(message.channel.id)
            
            # Keep a running history of the channel. It gets replaced by a fresh
            # snapshot from Discord right before the bot answers.
            self.message_handler.update_channel_history(
                channel_id=channel_id,
                user_id=user_id,
                username=username,
                content=history_text(message),
                is_bot=False,
                is_command=content.startswith('/'),
                timestamp=getattr(message, "created_at", None),
                message_id=getattr(message, "id", None)
            )

            # Now handle command processing
            if content.startswith('/'):
                # Load user data
                user_data = self.user_data_manager.load_user_data(user_id, username)
                
                # Process as command
                await self._handle_command_message(message, user_id, username, channel_id, user_data, False)
                return

            # Answer when they reply to one of the bot's messages or ping the bot
            referenced = await self._get_referenced_message(message)
            is_reply_to_bot = referenced is not None and referenced.author.id == self.bot.user.id
            if is_reply_to_bot or self._mentions_bot(message):
                user_data = self.user_data_manager.load_user_data(user_id, username)
                await self._respond(message, user_id, username, channel_id,
                                    self._strip_bot_mention(content), user_data, referenced)
                return
                
            # Neither a command, a reply to the bot nor a ping. The bot may still answer it when
            # someone was talking to it here a moment ago.
            await self._maybe_join_in(message, username, channel_id, content, referenced)

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            logger.error(traceback.format_exc())
    
    def _is_test_message(self, message: discord.Message) -> bool:
        """True for a message from the one bot the settings name, in the one channel they name."""
        if not Config.TEST_BOT_ID or not Config.TEST_CHANNEL_ID:
            return False
        # Never the bot itself, whatever the settings say, or it would answer its own answers
        if message.author.id == self.bot.user.id:
            return False
        return str(message.author.id) == Config.TEST_BOT_ID and str(message.channel.id) == Config.TEST_CHANNEL_ID

    async def _get_referenced_message(self, message: discord.Message) -> Optional[discord.Message]:
        """Return the message this one replies to, or None if it is not a reply or it is gone."""
        reference = message.reference
        if not reference or not reference.message_id:
            return None
        # Discord usually sends the replied-to message along, which saves a fetch
        if isinstance(reference.resolved, discord.Message):
            return reference.resolved
        try:
            return await message.channel.fetch_message(reference.message_id)
        except discord.NotFound:
            pass
        except discord.HTTPException as e:
            logger.warning(f"Could not fetch referenced message (code {e.code}): {e}")
        return None

    def _mentions_bot(self, message: discord.Message) -> bool:
        """True when the message pings the bot itself (not @everyone or a role)."""
        mentions = getattr(message, "mentions", None)
        return isinstance(mentions, list) and any(user.id == self.bot.user.id for user in mentions)

    def _strip_bot_mention(self, content: str) -> str:
        """Remove the bot's own ping from the text so the AI only sees what they said."""
        stripped = re.sub(rf'<@!?{self.bot.user.id}>', '', content).strip()
        return stripped or NO_TEXT

    async def sync_channel_history(self, channel) -> list:
        """
        Replace the stored history with what is really in the channel right now.
        Returns those messages, oldest first.
        """
        try:
            recent = [msg async for msg in channel.history(limit=HISTORY_FETCH_LIMIT)]
        except discord.HTTPException as e:
            logger.warning(f"Could not fetch channel history (code {e.code}): {e}")
            return []
        if not recent:
            return []

        recent.reverse()  # Discord returns newest first
        authors = {msg.id: msg.author for msg in recent}

        def answered(msg) -> tuple:
            """The name and id of whoever this message replied to, or was a slash command answer for."""
            reference = getattr(msg, "reference", None)
            target = getattr(reference, "resolved", None)
            author = getattr(target, "author", None) or authors.get(getattr(reference, "message_id", None))
            if author is None:
                author = getattr(getattr(msg, "interaction_metadata", None), "user", None)
            if author is None or not isinstance(getattr(author, "display_name", None), str):
                return "", ""
            name = "YOU" if author.id == self.bot.user.id else author.display_name
            return name, str(author.id)

        entries = []
        for msg in recent:
            reply_to, reply_to_id = answered(msg)
            entries.append({
                "user_id": str(msg.author.id),
                "username": msg.author.display_name,
                "content": history_text(msg),
                # Only our own messages count as the bot. Other bots show up under their own name.
                "is_bot": msg.author.id == self.bot.user.id,
                "is_command": isinstance(msg.content, str) and msg.content.startswith('/'),
                "timestamp": msg.created_at,
                "message_id": msg.id,
                "reply_to": reply_to,
                "reply_to_id": reply_to_id,
            })
        self.message_handler.replace_channel_history(str(channel.id), entries)
        return recent

    async def _respond(self, message: discord.Message, user_id: str, username: str,
                       channel_id: str, query: str, user_data: dict,
                       referenced: Optional[discord.Message] = None) -> None:
        """Answer a message with the AI, using fresh channel history and whatever the message points at."""
        watch = Stopwatch(getattr(message, "created_at", None))
        if self.rate_limiter:
            try:
                self.rate_limiter.check_rate_limit(user_id, "chat")
            except RateLimitError as e:
                minutes = max(1, round(e.retry_after / 60))
                await self._safe_send(message.channel, f"slow down, u hit the message limit. try again in about {minutes} min")
                return

        self.conversation.called(channel_id, user_id, getattr(message, "created_at", None))
        # One answer at a time in a channel, in the order the messages came. The second one
        # then reads the first answer, the way a person would have said it before going on.
        lock = self.channel_lock(channel_id)
        await lock.acquire()
        held = [True]

        def answer_is_out() -> None:
            """Let the next answer in this channel start. What is left of this one is bookkeeping."""
            if held[0]:
                held[0] = False
                lock.release()

        try:
            with watch.step("history"):
                recent = await self.sync_channel_history(message.channel)
                conversation_history = self.message_handler.build_conversation_context(
                    channel_id=channel_id, user_data=user_data, query=query,
                    session_start=self.conversation.session_start(channel_id), current_id=message.id
                )
            await self._handle_ai_response(message, user_id, username, channel_id, query,
                                           conversation_history, referenced, recent, watch,
                                           answer_is_out=answer_is_out)
        finally:
            answer_is_out()

    def channel_lock(self, channel_id: str) -> asyncio.Lock:
        """The lock that keeps the bot's answers in one channel in order."""
        return self._channel_locks.setdefault(channel_id, asyncio.Lock())

    async def _handle_command_message(self, message: discord.Message, user_id: str, 
                              username: str, channel_id: str, user_data: dict,
is_correction: bool) -> None:
        """Handle messages that begin with a slash (potential commands)"""
        try:
            # Split into command and args
            parts = message.content.split()
            command = parts[0][1:] if len(parts[0]) > 1 else ""  # Remove the slash
            args = parts[1:] if len(parts) > 1 else []
            
            # "/chat ..." is plain chat, so the word itself is dropped
            if command.lower() == "chat":
                rest = message.content[len(parts[0]):].strip()
                query = f"/{rest}" if rest else NO_TEXT
                referenced = await self._get_referenced_message(message)
                await self._respond(message, user_id, username, channel_id, query, user_data, referenced)
                return

            # Without a command handler, or for a word that is not a typed command, it is chat
            is_text_command = self.text_commands is None or command.lower() in self.text_commands
            if not self.command_handler or not is_text_command:
                referenced = await self._get_referenced_message(message)
                await self._respond(message, user_id, username, channel_id, message.content, user_data, referenced)
                return

            # Pass to command handler. /tldr reads the channel and calls the AI, which takes a while.
            async with typing_in_background(message.channel):
                cmd_response = await self.command_handler.handle_command(command, args, message, user_id)

            # If the command was recognized and handled, send the response
            if isinstance(cmd_response, discord.Embed):
                await message.channel.send(embed=cmd_response)
            elif isinstance(cmd_response, Reply):
                # An answer from the AI, which may come with files
                # A recap or an answer about the channel is not chat, so it gets no server emoji
                await self._send_reply(message, cmd_response.text, cmd_response.files, custom_emoji=False)
            elif cmd_response:
                await self._safe_send(message.channel, cmd_response)
            else:
                # Unrecognized slash: treat as chat, with the channel history and the
                # message they replied to (if any)
                referenced = await self._get_referenced_message(message)
                await self._respond(message, user_id, username, channel_id, message.content, user_data, referenced)
                
        except Exception as e:
            logger.error(f"Error handling command: {e}")
            await self._safe_send(message.channel, "yo something broke on my end, try again")
    
    async def _maybe_assign_chrono_role(self, message: discord.Message) -> None:
        """Give the first-use role to whoever just talked to the bot."""
        await give_first_use_role(message.guild, message.author.id)

    async def _handle_ai_response(self, message: discord.Message, user_id: str,
                              username: str, channel_id: str, query: str,
                              conversation_history: str,
                              referenced: Optional[discord.Message] = None,
                              recent: Optional[list] = None,
                              watch: Optional[Stopwatch] = None,
                              answer_is_out: Optional[Callable[[], None]] = None) -> None:
        """
        Build the answer to one message and send it. answer_is_out is called once the
        answer is sent and in the history, before the slower work that nobody waits for.
        """
        original_query = query  # preserve clean version for history/user data storage
        watch = watch or Stopwatch()
        try:
            async with typing_in_background(message.channel):
                # Files made, PDFs read in full and web lookups each count against a daily limit per user
                file_gate = pdf_gate = web_gate = None
                limiter = self.rate_limiter
                if limiter:
                    file_gate = lambda: limiter.check_rate_limit(user_id, "file")
                    pdf_gate = lambda: limiter.check_rate_limit(user_id, "pdf")
                    web_gate = lambda: limiter.check_rate_limit(user_id, "web")
                with watch.step("context"):
                    enriched_query, linked = await resolve_message_link(self.bot, query)
                    # Read the message they replied to or linked and any files or images involved.
                    # With none of those, what was posted just before their message is read,
                    # unless that was switched off for this channel.
                    if not get_channel_settings().reads_recent_posts(channel_id):
                        recent = None
                    attached = await build_attached_context(message, referenced, self.bot.user.id,
                                                            linked=linked, recent=recent, pdf_gate=pdf_gate)
                # The model always knows the server's emoji. Whether this reply may show one is settled
                # when it is sent: not while one of the bot's last few replies here had one.
                custom_emoji = self.custom_emoji_due(channel_id)
                may_react = self.reaction_due(channel_id)
                emoji = usable_emoji(message.channel, self.bot.guilds)
                with watch.step("model and tools"):
                    reply = await self.ai_handler.generate_reply(
                        enriched_query, conversation_history, username, user_id,
                        attached_context=attached.text, images=attached.images,
                        allow_files=True, file_gate=file_gate, web_gate=web_gate,
                        emoji_prompt=emoji.prompt(reactions=may_react)
                    )

            with watch.step("send"):
                # With several people talking to the bot at once, the answer pings the one it is for
                shown = await self._send_reply(message, reply.text, reply.files, custom_emoji=custom_emoji,
                                               listing=asked_about_emoji(query),
                                               ping=self.conversation.is_crowd(channel_id))
                # What was sent, with any emoji written :name:, is what the history keeps
                ai_response = shown.plain
            if may_react:
                await self._react(message, shown.reaction, channel_id)
            # Everything they waited for is done. What follows runs after the answer is out.
            logger.info(f"Answered {username} in {watch.summary()}")

            # A stand-in for an answer the model could not give must not come back as context
            if not reply.ok:
                self.message_handler.not_answers.append(ai_response)
            # The user's message is already in the history, so only the answer is added
            self.message_handler.update_channel_history(
                channel_id=channel_id, user_id=str(self.bot.user.id),
                username="ChronoChunk", content=ai_response, is_bot=True,
                reply_to=username, reply_to_id=user_id
            )
            # The next message in this channel can be answered while this one is saved
            if answer_is_out:
                answer_is_out()
            await self._maybe_assign_chrono_role(message)

            # add_conversation also extracts facts from the message
            if self.user_data_manager:
                await self.user_data_manager.add_conversation(user_id, original_query, ai_response, username)

        except discord.HTTPException as e:
            logger.error(f"Discord HTTP error sending response: {e} (code {e.code})")
            await self._discord_error_response(message.channel, e)
        except Exception as e:
            logger.error(f"Error generating AI response: {e}")
            await self._safe_send(message.channel, "my brain just glitched fr, try again in a sec")

    @staticmethod
    def _split_text(text: str, limit: int = 1900) -> List[str]:
        """Split text into chunks Discord accepts, breaking at a line or word boundary."""
        chunks = []
        while len(text) > limit:
            split_at = text.rfind('\n', 0, limit)
            if split_at <= 0:
                split_at = text.rfind(' ', 0, limit)
            if split_at <= 0:
                split_at = limit
            chunk, text = text[:split_at], text[split_at:].lstrip()
            # A split inside a code block closes it here and reopens it in the next chunk
            # A cut through the middle of a custom emoji would post it as broken text
            torn = re.search(r'<[\w:]{0,60}$', chunk)
            if torn and torn.start() > 0 and re.match(r'<a?:\w{2,32}:\d{15,22}>', chunk[torn.start():] + text[:60]):
                chunk, text = chunk[:torn.start()], chunk[torn.start():] + text
            if chunk.count('```') % 2 == 1:
                chunk += '\n```'
                text = '```\n' + text
            chunks.append(chunk)
        if text:
            chunks.append(text)
        return chunks

    async def _safe_send(self, channel, text: str) -> None:
        """Send text to Discord, splitting at 1900 chars if needed."""
        for chunk in self._split_text(text):
            await channel.send(chunk, allowed_mentions=NO_PINGS)

    def custom_emoji_due(self, channel_id: str) -> bool:
        """False while one of the bot's last few replies in the channel had a server emoji, so they stay rare."""
        return not any(used_custom_emoji(text)
                       for text in self.message_handler.recent_bot_replies(channel_id, QUIET_REPLIES))

    async def _maybe_join_in(self, message: discord.Message, username: str, channel_id: str,
                             content: str, referenced: Optional[discord.Message]) -> None:
        """
        Weigh a message nobody addressed to the bot, and answer it when the model thinks
        a person in the chat would. Only for a short while after someone called the bot
        here. Nothing is saved about the person, and no one's own AI limit pays for it.
        """
        if not content.strip() or not self.conversation.may_join_in(channel_id):
            return
        if not get_channel_settings().joins_in(channel_id):
            return
        # Someone answering another person, or pinging one, is talking to them
        if referenced is not None or getattr(message, "mentions", None) or getattr(message, "role_mentions", None):
            return
        self.conversation.start_deciding(channel_id)
        answered = False
        try:
            usage_context.set_user("")
            usage_context.label("join_in")
            # After any answer that is still being written, so this one has read it
            async with self.channel_lock(channel_id):
                await self.sync_channel_history(message.channel)
                history = self.message_handler.build_conversation_context(
                    channel_id=channel_id, user_data={}, query=content,
                    session_start=self.conversation.session_start(channel_id), current_id=message.id)
                custom_emoji = self.custom_emoji_due(channel_id)
                emoji = usable_emoji(message.channel, self.bot.guilds)
                reply = await self.ai_handler.generate_reply(
                    content, history, username, None,
                    attached_context=UNCALLED_NOTE.format(name=username), emoji_prompt=emoji.prompt())
                # A stand-in for a failed answer is for someone who asked. Nobody did.
                if not reply.ok or stays_out(reply.text):
                    return
                shown = await self._send_reply(message, reply.text, custom_emoji=custom_emoji)
                answered = True
                self.message_handler.update_channel_history(
                    channel_id=channel_id, user_id=str(self.bot.user.id),
                    username="ChronoChunk", content=shown.plain, is_bot=True,
                    reply_to=username, reply_to_id=str(message.author.id))
            logger.info(f"Joined in on a message from {username} in channel {channel_id}")
        except Exception as e:
            logger.warning(f"Could not weigh an uncalled message: {e!r}")
        finally:
            self.conversation.done_deciding(channel_id, answered)

    def reaction_due(self, channel_id: str) -> bool:
        """False for a while after the bot put a reaction on a message in the channel."""
        last = self._last_reaction.get(channel_id)
        return last is None or time.monotonic() - last >= REACTION_GAP_SECONDS

    async def _react(self, message: discord.Message, reaction: str, channel_id: str) -> None:
        """Put the emoji the model asked for on the message it answered. The reply is out already, so this may fail quietly."""
        # Two answers in one channel can finish together. Only the first one through here reacts.
        if not reaction or not self.reaction_due(channel_id):
            return
        before = self._last_reaction.get(channel_id)
        try:
            me = message.guild.me if message.guild else None
            if me is not None and message.channel.permissions_for(me).add_reactions is not True:
                return
            # Taken before the wait on Discord, so a second answer sees the channel as used
            self._last_reaction[channel_id] = time.monotonic()
            await message.add_reaction(discord.PartialEmoji.from_str(reaction))
        except Exception as e:
            logger.warning(f"Could not react to a message: {e!r}")
            # A reaction that did not happen does not use up the wait
            if before is None:
                self._last_reaction.pop(channel_id, None)
            else:
                self._last_reaction[channel_id] = before

    async def _send_reply(self, message: discord.Message, text: str, files=None,
                          custom_emoji: bool = True, listing: bool = False, ping: bool = False) -> Rendered:
        """
        Send the answer as a Discord reply to the message that asked, splitting long
        text. files are BuiltFile objects, uploaded with the first chunk. Emoji names
        in the text become server emoji the bot can use here, and any other is taken
        out. listing is for an answer to someone who asked about the emoji, which may
        show several. With ping the person who asked is notified, and nobody else ever
        is. Returns what was sent, which also has the text as the chat history should
        keep it and the reaction the model asked for.
        """
        shown = render(text, usable_emoji(message.channel, self.bot.guilds), custom_allowed=custom_emoji,
                       listing=listing)
        chunks = self._split_text(shown.text)
        if not chunks and not files:
            return shown
        first = chunks[0] if chunks else None

        def extras() -> dict:
            # Upload objects can only be sent once, so they are rebuilt for the fallback
            if not files:
                return {}
            return {"files": [discord.File(io.BytesIO(f.data), filename=f.filename) for f in files]}

        try:
            await message.reply(first, mention_author=ping, allowed_mentions=PING_AUTHOR if ping else NO_PINGS,
                                **extras())
        except discord.HTTPException:
            # Replying failed (message deleted, or no permission), so send it as a plain message
            await message.channel.send(first, allowed_mentions=NO_PINGS, **extras())
        for chunk in chunks[1:]:
            await message.channel.send(chunk, allowed_mentions=NO_PINGS)
        return shown

    async def _discord_error_response(self, channel, error: discord.HTTPException) -> None:
        """Natural in-character response for specific Discord API errors."""
        code = error.code
        if code == 50035:
            await channel.send("bro discord said my message was cooked, probably too long or smth weird in it — ask again n ill keep it shorter")
        elif code == 50013:
            await channel.send("cant send in here rn, no perms in this channel")
        elif code == 10003:
            # Unknown channel — silently ignore
            logger.warning("Channel not found, skipping response")
        else:
            await channel.send(f"discord threw a fit (error {code}), try again")
    
