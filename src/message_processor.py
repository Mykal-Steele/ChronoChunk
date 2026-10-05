import io
import logging
import re
import discord
from typing import Optional, Set, Tuple
from src.command_handler import RateLimitError
from src.message_context import build_attached_context, history_text
from discord.ext.commands.errors import CommandNotFound
import asyncio
import sys
import traceback
from typing import Dict, Any, List, Optional

# Setup logging
logger = logging.getLogger(__name__)

# How many channel messages to load as context when the bot answers
HISTORY_FETCH_LIMIT = 15

# The bot quotes and sums up other people's messages, so what it sends must never ping anyone
NO_PINGS = discord.AllowedMentions.none()

# Stands in for the text when someone only pings the bot or types a bare /chat
NO_TEXT = "(no text, they just want u to look at what they replied to or attached)"


class MessageProcessor:
    """Processes incoming Discord messages and handles routing them correctly"""
    
    def __init__(self, bot, message_handler, ai_response_handler=None, user_data_manager=None, 
                 game_manager=None, command_handler=None, rate_limiter=None,
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
    
    async def process_message(self, message: discord.Message) -> None:
        """Process an incoming Discord message - Properly capture ALL channel messages"""
        try:
            # Skip bot messages
            if message.author.bot:
                return
                
            # Get basic info
            content = message.content
            user_id = str(message.author.id)
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
                timestamp=getattr(message, "created_at", None)
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
                
            # If we get here, message is neither a command, a reply to the bot nor a ping
            # Ignore for processing but we've already captured it in history

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            logger.error(traceback.format_exc())
    
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

    async def sync_channel_history(self, channel) -> None:
        """Replace the stored history with what is really in the channel right now, oldest first."""
        try:
            recent = [msg async for msg in channel.history(limit=HISTORY_FETCH_LIMIT)]
        except discord.HTTPException as e:
            logger.warning(f"Could not fetch channel history (code {e.code}): {e}")
            return
        if not recent:
            return

        recent.reverse()  # Discord returns newest first
        self.message_handler.replace_channel_history(str(channel.id), [
            {
                "user_id": str(msg.author.id),
                "username": msg.author.display_name,
                "content": history_text(msg),
                # Only our own messages count as the bot. Other bots show up under their own name.
                "is_bot": msg.author.id == self.bot.user.id,
                "is_command": isinstance(msg.content, str) and msg.content.startswith('/'),
                "timestamp": msg.created_at,
            }
            for msg in recent
        ])

    async def _respond(self, message: discord.Message, user_id: str, username: str,
                       channel_id: str, query: str, user_data: dict,
                       referenced: Optional[discord.Message] = None) -> None:
        """Answer a message with the AI, using fresh channel history and whatever the message points at."""
        if self.rate_limiter:
            try:
                self.rate_limiter.check_rate_limit(user_id, "chat")
            except RateLimitError as e:
                minutes = max(1, round(e.retry_after / 60))
                await self._safe_send(message.channel, f"slow down, u hit the message limit. try again in about {minutes} min")
                return

        await self.sync_channel_history(message.channel)
        conversation_history = self.message_handler.build_conversation_context(
            channel_id=channel_id, user_data=user_data, is_correction=False
        )
        await self._handle_ai_response(message, user_id, username, channel_id, query,
                                       conversation_history, referenced)

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

            # Pass to command handler
            cmd_response = await self.command_handler.handle_command(command, args, message, user_id)

            # If the command was recognized and handled, send the response
            if cmd_response:
                await self._safe_send(message.channel, cmd_response)
            else:
                # Unrecognized slash: treat as chat, with the channel history and the
                # message they replied to (if any)
                referenced = await self._get_referenced_message(message)
                await self._respond(message, user_id, username, channel_id, message.content, user_data, referenced)
                
        except Exception as e:
            logger.error(f"Error handling command: {e}")
            await self._safe_send(message.channel, "yo something broke on my end, try again")
    
    _MSG_LINK_RE = re.compile(r'https://discord\.com/channels/(\d+)/(\d+)/(\d+)')

    async def _resolve_message_link(self, content: str) -> str:
        """
        Detect a Discord message link in content, fetch it, and return an enhanced
        query with the referenced message embedded. On fetch failure, appends a note
        so the AI can react naturally to both the user's text and the broken link.
        """
        match = self._MSG_LINK_RE.search(content)
        if not match:
            return content

        _, channel_id_str, message_id_str = match.groups()
        try:
            channel = self.bot.get_channel(int(channel_id_str))
            if channel is None:
                channel = await self.bot.fetch_channel(int(channel_id_str))
            ref_msg = await channel.fetch_message(int(message_id_str))

            author = ref_msg.author.display_name
            ref_content = ref_msg.content or ""
            if ref_msg.attachments:
                filenames = ", ".join(a.filename for a in ref_msg.attachments)
                ref_content = (ref_content + f" [{filenames}]").strip() if ref_content else f"[{filenames}]"
            if not ref_content:
                ref_content = "[no text]"

            embedded = f'[message already fetched — {author} said: "{ref_content}"]'
            return self._MSG_LINK_RE.sub(embedded, content, count=1)

        except Exception as e:
            logger.warning(f"Could not fetch message link: {e}")
            fail_note = (
                "\n(heads up: user shared a discord link but it couldnt be loaded —"
                " react to whatever else they said and drop naturally that u cant see the link, stay in ur personality)"
            )
            return content + fail_note

    async def _maybe_assign_chrono_role(self, message: discord.Message) -> None:
        """Give the 'I Love Chrono <3' role on a user's first AI interaction."""
        guild = message.guild
        if not guild:
            return
        role = discord.utils.get(guild.roles, name="I Love Chrono <3")
        if not role:
            logger.warning("'I Love Chrono <3' role not found in guild — check the role name matches exactly")
            return
        # Resolve to a full Member object so .roles is populated
        member = guild.get_member(message.author.id)
        if not member:
            logger.warning(f"Could not resolve member {message.author.id} from guild cache")
            return
        if role not in member.roles:
            try:
                await member.add_roles(role)
                logger.info(f"Assigned 'I Love Chrono <3' role to {member.display_name}")
            except discord.Forbidden:
                logger.warning("Missing permission to assign 'I Love Chrono <3' role — bot role must be above it in hierarchy")
            except Exception as e:
                logger.error(f"Error assigning Chrono role: {e}")

    async def _handle_ai_response(self, message: discord.Message, user_id: str,
                              username: str, channel_id: str, query: str,
                              conversation_history: str,
                              referenced: Optional[discord.Message] = None) -> None:
        original_query = query  # preserve clean version for history/user data storage
        try:
            async with message.channel.typing():
                enriched_query = await self._resolve_message_link(query)
                # Read the message they replied to and any files or images involved
                attached = await build_attached_context(message, referenced, self.bot.user.id)
                # Files (documents, diagrams) count against a per-user daily limit
                file_gate = None
                if self.rate_limiter:
                    file_gate = lambda: self.rate_limiter.check_rate_limit(user_id, "file")
                reply = await self.ai_handler.generate_reply(
                    enriched_query, conversation_history, username, user_id,
                    attached_context=attached.text, images=attached.images,
                    allow_files=True, file_gate=file_gate
                )
                ai_response = reply.text

            await self._send_reply(message, ai_response, reply.files)
            await self._maybe_assign_chrono_role(message)

            # The user's message is already in the history, so only the answer is added
            self.message_handler.update_channel_history(
                channel_id=channel_id, user_id=str(self.bot.user.id),
                username="ChronoChunk", content=ai_response, is_bot=True
            )

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

    async def _send_reply(self, message: discord.Message, text: str, files=None) -> None:
        """
        Send the answer as a Discord reply to the message that asked, splitting long
        text. files are BuiltFile objects, uploaded with the first chunk.
        """
        chunks = self._split_text(text)
        if not chunks and not files:
            return
        first = chunks[0] if chunks else None

        def extras() -> dict:
            # Upload objects can only be sent once, so they are rebuilt for the fallback
            if not files:
                return {}
            return {"files": [discord.File(io.BytesIO(f.data), filename=f.filename) for f in files]}

        try:
            await message.reply(first, mention_author=False, allowed_mentions=NO_PINGS, **extras())
        except discord.HTTPException:
            # Replying failed (message deleted, or no permission), so send it as a plain message
            await message.channel.send(first, allowed_mentions=NO_PINGS, **extras())
        for chunk in chunks[1:]:
            await message.channel.send(chunk, allowed_mentions=NO_PINGS)

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
    
