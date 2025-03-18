import logging
import discord
from typing import Optional, Tuple
from src.command_handler import RateLimitError
from discord.ext.commands.errors import CommandNotFound

# Setup logging
logger = logging.getLogger(__name__)

class MessageProcessor:
    """Processes incoming Discord messages and handles routing them correctly"""
    
    def __init__(self, bot, command_handler, rate_limiter, user_data_manager, 
                 intent_detector, message_handler, ai_handler):
        """Initialize with required components"""
        self.bot = bot
        self.command_handler = command_handler
        self.rate_limiter = rate_limiter
        self.user_data_manager = user_data_manager
        self.intent_detector = intent_detector
        self.message_handler = message_handler
        self.ai_handler = ai_handler
    
    async def process_message(self, message: discord.Message) -> None:
        """Process an incoming Discord message"""
        # Ignore our own messages
        if message.author == self.bot.user:
            return
            
        try:
            # Get permanent discord user ID
            user_id = str(message.author.id)
            channel_id = str(message.channel.id)
            username = message.author.display_name
            
            # Log message
            logger.info(f"Message from {message.author.name} ({user_id}): {message.content[:50]}...")
            
            # Store message in channel history
            is_command = message.content.startswith('/') and not message.content.startswith('//')
            self.message_handler.update_channel_history(
                channel_id=channel_id,
                user_id=user_id,
                username=username,
                content=message.content,
                is_bot=False,
                is_command=is_command
            )
            
            # Load user data with username
            user_data = self.user_data_manager.load_user_data(user_id, username)
            
            # Check if message is a correction using AI
            is_correction = await self.intent_detector.detect_correction_intent(message.content)
            
            # Check if this is a reply to one of our messages
            is_reply_to_bot = await self._check_if_reply_to_bot(message)
            if is_reply_to_bot and not message.content.startswith('/'):
                try:
                    self.rate_limiter.check_rate_limit(user_id, "chat")
                    conversation_history = await self.message_handler.build_conversation_context(channel_id, user_data, is_correction)
                    await self._handle_ai_response(message, user_id, username, channel_id, message.content, conversation_history)
                    return
                except RateLimitError as e:
                    await self.message_handler.send_response(message.channel, str(e), user_mention=message.author.mention)
                    return
            
            # Process commands first (standard Discord command processing)
            try:
                await self.bot.process_commands(message)
            except CommandNotFound:
                # Silently ignore command not found errors
                pass
            
            # Handle slash-prefixed messages
            if message.content.startswith('/'):
                await self._handle_command_message(message, user_id, username, channel_id, user_data, is_correction)
            else:
                # Only extract facts from regular messages in non-DM channels
                # AND only if the message is likely to contain facts (longer than a few words)
                if not isinstance(message.channel, discord.DMChannel) and len(message.content.split()) > 3:
                    try:
                        self.rate_limiter.check_rate_limit(user_id, "chat")
                        await self.user_data_manager.extract_and_save_facts(user_id, message.content, username)
                    except RateLimitError:
                        pass  # Silently ignore rate limit errors for passive fact extraction
        
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    async def _check_if_reply_to_bot(self, message: discord.Message) -> bool:
        """Check if the message is a reply to one of the bot's messages"""
        if message.reference and message.reference.message_id:
            try:
                # Try to fetch the message being replied to
                referenced_msg = await message.channel.fetch_message(message.reference.message_id)
                return referenced_msg.author.id == self.bot.user.id
            except discord.NotFound:
                pass  # Message not found
        return False
    
    async def _handle_command_message(self, message: discord.Message, user_id: str, 
                                    username: str, channel_id: str, user_data: dict,
                                    is_correction: bool) -> None:
        """Handle messages that begin with a slash (potential commands)"""
        # Extract command and args
        parts = message.content.split()
        command = parts[0][1:]  # Remove the slash
        args = parts[1:] if len(parts) > 1 else []
        
        # Try to run registered command
        cmd_response = None
        try:
            cmd_response = await self.command_handler.handle_command(command, args, message, user_id)
        except RateLimitError as e:
            await self.message_handler.send_response(message.channel, str(e), user_mention=message.author.mention)
            return
            
        if cmd_response:
            await self.message_handler.send_response(message.channel, cmd_response, user_mention=message.author.mention)
            return
            
        # For unrecognized commands, process as AI query with rate limit check
        try:
            self.rate_limiter.check_rate_limit(user_id, "chat")
        except RateLimitError as e:
            await self.message_handler.send_response(message.channel, str(e), user_mention=message.author.mention)
            return
            
        # Process with original form to maintain context
        conversation_history = await self.message_handler.build_conversation_context(channel_id, user_data, is_correction)
        await self._handle_ai_response(message, user_id, username, channel_id, message.content, conversation_history)
    
    async def _handle_ai_response(self, message: discord.Message, user_id: str, 
                                username: str, channel_id: str, query: str,
                                conversation_history: str) -> None:
        """Generate and send an AI response to a message"""
        try:
            # Notify that we're "typing"
            async with message.channel.typing():
                # Generate AI response
                ai_response = await self.ai_handler.generate_response(query, conversation_history, username)
                
                # Send the response
                sent_message = await self.message_handler.send_response(
                    message.channel, 
                    ai_response, 
                    user_mention=message.author.mention
                )
                
                # Update bot's message in channel history
                self.message_handler.update_channel_history(
                    channel_id=channel_id,
                    user_id=str(self.bot.user.id),
                    username="ChronoChunk",
                    content=ai_response,
                    is_bot=True
                )
                
                # Store in conversation memory
                is_command = query.startswith('/')
                self.message_handler.update_conversation_memory(
                    channel_id=channel_id,
                    username=username,
                    user_message=query,
                    bot_response=ai_response,
                    is_command=is_command
                )
                
                # Save to user data manager and only extract facts for non-slash messages
                await self.user_data_manager.add_conversation(user_id, query, ai_response, username)
                
                # Only attempt to extract facts from messages that aren't command-like
                # and have reasonable length to contain facts
                if not query.startswith('/') and len(query.split()) > 3:
                    await self.user_data_manager.extract_and_save_facts(user_id, query, username)
                
        except Exception as e:
            logger.error(f"Error generating AI response: {e}")
            await self.message_handler.send_response(
                message.channel,
                "ahh shit, my brain short-circuited for a sec. wanna try again?",
                user_mention=message.author.mention
            ) 