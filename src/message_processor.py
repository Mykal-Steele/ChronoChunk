import logging
import re
import discord
from typing import Optional, Tuple
from src.command_handler import RateLimitError
from discord.ext.commands.errors import CommandNotFound
import asyncio
import sys
import traceback
from typing import Dict, Any, List, Optional

# Setup logging
logger = logging.getLogger(__name__)

class MessageProcessor:
    """Processes incoming Discord messages and handles routing them correctly"""
    
    def __init__(self, bot, message_handler, ai_response_handler=None, user_data_manager=None, 
                 game_manager=None, command_handler=None):
        """Initialize message processor with required components"""
        self.bot = bot
        self.message_handler = message_handler
        self.ai_handler = ai_response_handler  # Store with internal name ai_handler
        self.user_data_manager = user_data_manager
        self.game_manager = game_manager
        self.command_handler = command_handler  # Add this line to store the command handler
    
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
            
            # IMPORTANT: Always capture message history for EVERY message in the channel
            # This ensures we have context even for non-command messages
            recent_messages = [msg async for msg in message.channel.history(limit=15)]
            
            # Store ALL recent messages from the channel for better context
            for msg in recent_messages:
                if msg.id != message.id:  # Skip current message as it's handled below
                    self.message_handler.update_channel_history(
                        channel_id=channel_id,
                        user_id=str(msg.author.id),
                        username=msg.author.display_name,
                        content=msg.content,
                        is_bot=msg.author.bot,
                        is_command=msg.content.startswith('/')
                    )
            
            # Now process the current message
            self.message_handler.update_channel_history(
                channel_id=channel_id,
                user_id=user_id,
                username=username,
                content=content,
                is_bot=False,
                is_command=content.startswith('/')
            )
                
            # Now handle command processing
            if content.startswith('/'):
                # Load user data
                user_data = self.user_data_manager.load_user_data(user_id, username)
                
                # Process as command
                await self._handle_command_message(message, user_id, username, channel_id, user_data, False)
                return
                
            # Check if this is a reply to the bot
            is_reply_to_bot = await self._check_if_reply_to_bot(message)
            if is_reply_to_bot:
                # Handle as a reply
                user_data = self.user_data_manager.load_user_data(user_id, username)
                conversation_history = self.message_handler.build_conversation_context(
                    channel_id=channel_id,
                    user_data=user_data,
                    is_correction=False
                )
                await self._handle_ai_response(message, user_id, username, channel_id, content, conversation_history)
                return
                
            # If we get here, message is neither a command nor a reply to the bot
            # Ignore for processing but we've already captured it in history

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            logger.error(traceback.format_exc())
    
    async def _check_if_reply_to_bot(self, message: discord.Message) -> bool:
        """Check if the message is a reply to one of the bot's messages"""
        if message.reference and message.reference.message_id:
            try:
                referenced_msg = await message.channel.fetch_message(message.reference.message_id)
                return referenced_msg.author.id == self.bot.user.id
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                logger.warning(f"Could not fetch referenced message (code {e.code}): {e}")
        return False
    
    async def _handle_command_message(self, message: discord.Message, user_id: str, 
                              username: str, channel_id: str, user_data: dict,
is_correction: bool) -> None:
        """Handle messages that begin with a slash (potential commands)"""
        try:
            # Split into command and args
            parts = message.content.split()
            command = parts[0][1:] if len(parts[0]) > 1 else ""  # Remove the slash
            args = parts[1:] if len(parts) > 1 else []
            
            # Check if we actually have a command handler
            if not self.command_handler:
                conversation_history = self.message_handler.build_conversation_context(
                    channel_id=channel_id, user_data=user_data, is_correction=False
                )
                await self._handle_ai_response(message, user_id, username, channel_id, message.content, conversation_history)
                return

            # Pass to command handler
            cmd_response = await self.command_handler.handle_command(command, args, message, user_id)

            # If the command was recognized and handled, send the response
            if cmd_response:
                await message.channel.send(cmd_response)
            else:
                # Unrecognized slash — treat as chat, but now WITH conversation history
                conversation_history = self.message_handler.build_conversation_context(
                    channel_id=channel_id, user_data=user_data, is_correction=False
                )
                await self._handle_ai_response(message, user_id, username, channel_id, message.content, conversation_history)
                
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
                              conversation_history: str) -> None:
        original_query = query  # preserve clean version for history/user data storage
        try:
            enriched_query = await self._resolve_message_link(query)
            async with message.channel.typing():
                ai_response = await self.ai_handler.generate_response(
                    enriched_query, conversation_history, username, user_id
                )

            await self._safe_send(message.channel, ai_response)
            await self._maybe_assign_chrono_role(message)

            # Store the original (clean) query so notes/embeds don't pollute history
            self.message_handler.update_channel_history(
                channel_id=channel_id, user_id=user_id,
                username=username, content=original_query, is_bot=False
            )
            self.message_handler.update_channel_history(
                channel_id=channel_id, user_id=str(self.bot.user.id),
                username="ChronoChunk", content=ai_response, is_bot=True
            )

            if self.user_data_manager:
                await self.user_data_manager.add_conversation(user_id, original_query, ai_response, username)
                if not original_query.startswith('/') and len(original_query.split()) > 2:
                    await self.user_data_manager.extract_and_save_facts(user_id, original_query, username)

        except discord.HTTPException as e:
            logger.error(f"Discord HTTP error sending response: {e} (code {e.code})")
            await self._discord_error_response(message.channel, e)
        except Exception as e:
            logger.error(f"Error generating AI response: {e}")
            await self._safe_send(message.channel, "my brain just glitched fr, try again in a sec")

    async def _safe_send(self, channel, text: str) -> None:
        """Send text to Discord, splitting at 1900 chars if needed."""
        limit = 1900
        if len(text) <= limit:
            await channel.send(text)
            return
        # Split at last word boundary before the limit
        chunks = []
        while len(text) > limit:
            split_at = text.rfind(' ', 0, limit)
            if split_at == -1:
                split_at = limit
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip()
        if text:
            chunks.append(text)
        for chunk in chunks:
            await channel.send(chunk)

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
    
