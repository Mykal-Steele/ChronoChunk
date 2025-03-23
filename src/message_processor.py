import logging
import discord
from typing import Optional, Tuple
from src.command_handler import RateLimitError
from discord.ext.commands.errors import CommandNotFound
import asyncio
import sys  # Add this import
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
        """Process an incoming Discord message - ONLY commands and replies"""
        try:
            # Skip bot messages
            if message.author.bot:
                return
                
            # Get basic info
            content = message.content
            user_id = str(message.author.id)
            username = message.author.display_name
            channel_id = str(message.channel.id)
            
            # Only process these two types of messages:
            # 1. Messages that start with / (commands)
            # 2. Direct replies to the bot
            
            # Check if this is a command
            if content.startswith('/'):
                await self._handle_command_message(message, user_id, username, channel_id, {}, False)
                return
                
            # Check if this is a reply to the bot
            is_reply_to_bot = await self._check_if_reply_to_bot(message)
            if is_reply_to_bot:
                # Handle as a reply
                await self._handle_ai_response(message, user_id, username, channel_id, content, None)
                return
                
            # If we get here, message is neither a command nor a reply to the bot
            # DO NOTHING - this is the key change
            
        except Exception as e:
            # Error reporting
            import traceback, sys
            error_type = type(e).__name__
            tb = traceback.extract_tb(sys.exc_info()[2])
            error_file = tb[-1].filename
            error_line = tb[-1].lineno
            error_func = tb[-1].name
            
            error_msg = f"Error processing message: {error_type}: {e} in {error_file}, line {error_line}, function {error_func}"
            logger.error(error_msg)
            logger.error("Full traceback:")
            logger.error(traceback.format_exc())
    
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
        try:
            # Split into command and args
            parts = message.content.split()
            command = parts[0][1:] if len(parts[0]) > 1 else ""  # Remove the slash
            args = parts[1:] if len(parts) > 1 else []
            
            # Check if we actually have a command handler
            if not self.command_handler:
                # If no command handler, treat as AI query
                await self._handle_ai_response(message, user_id, username, channel_id, message.content, None)
                return
                
            # Pass to command handler
            cmd_response = await self.command_handler.handle_command(command, args, message, user_id)
            
            # If the command was recognized and handled, send the response
            if cmd_response:
                await message.channel.send(cmd_response)
            else:
                # Handle unrecognized commands as AI queries
                # This preserves your requirement to handle "/yoo bro" as an AI query
                await self._handle_ai_response(message, user_id, username, channel_id, message.content, None)
                
        except Exception as e:
            logger.error(f"Error handling command: {e}")
            await message.channel.send("yo, something went wrong with that command 💀")
    
    async def _handle_ai_response(self, message: discord.Message, user_id: str, 
                              username: str, channel_id: str, query: str,
                              conversation_history: str) -> None:
        try:
            # Start typing indicator
            async with message.channel.typing():
                # Get AI response
                ai_response = await self.ai_handler.generate_response(
                    query, 
                    conversation_history,
                    username,
                    user_id
                )
                
                # Send response
                await message.channel.send(ai_response)
                
                # Update message history
                self.message_handler.update_channel_history(
                    channel_id=channel_id,
                    user_id=user_id,
                    username=username,
                    content=query,
                    is_bot=False
                )
                
                # Update message history with bot response
                self.message_handler.update_channel_history(
                    channel_id=channel_id,
                    user_id=self.bot.user.id,
                    username="ChronoChunk",
                    content=ai_response,
                    is_bot=True
                )
                
                # Save to user data
                if self.user_data_manager:
                    await self.user_data_manager.add_conversation(
                        user_id, 
                        query,
                        ai_response, 
                        username
                    )
                    
                    # CRITICAL FIX: Always extract facts for non-command messages
                    if not query.startswith('/') and len(query.split()) > 2:
                        await self.user_data_manager.extract_and_save_facts(user_id, query, username)
                        
                # Always try to extract facts for non-command, substantive messages
                if self.user_data_manager and not query.startswith('/') and len(query.split()) > 2:
                    # Explicitly log this attempt
                    logger.info(f"Attempting to extract facts from: {query[:30]}...")
                    await self.user_data_manager.extract_and_save_facts(user_id, query, username)
                        
        except Exception as e:
            logger.error(f"Error generating AI response: {e}")
            await message.channel.send("yo, my brain just glitched. try again?")
    
    def _enhance_command_history(self, conversation_history: str) -> str:
        """Enhance conversation history for slash commands"""
        # Add special note to help process commands better
        command_note = "\nNOTE: User tried using a slash command. Treat it as normal message but keep conversation natural.\n"
        
        # Add the note at the appropriate position
        if conversation_history:
            return conversation_history + command_note
        else:
            return command_note
    
    async def _handle_message(self, message: discord.Message) -> None:
        """Process incoming Discord messages"""
        # Skip bot messages to prevent loops
        if message.author.bot:
            return
        
        try:
            # Extract basic info
            content = message.content
            user_id = str(message.author.id)
            username = message.author.name
            channel_id = str(message.channel.id)
            
            # Strip command prefix for better context while keeping original for command detection
            is_command = content.startswith('/')
            query = content
            
            # Update channel history with the user's message
            self.message_handler.update_channel_history(
                channel_id=channel_id,
                user_id=user_id,
                username=username,
                content=content,
                is_bot=False,
                is_command=is_command
            )
            
            # Get user data to include in context
            user_data = await self.user_data_manager.get_user_data(user_id)
            
            # Get conversation context - critical for natural back and forth
            conversation_history = await self.message_handler.build_conversation_context(
                channel_id=channel_id,
                user_data=user_data,
                is_correction=False
            )
            
            # Process the message based on content
            if is_command:
                # Strip the command prefix for handlers
                command_content = content[1:] if len(content) > 1 else ""
                # Try to handle as a command first, fall back to AI if no command matches
                command_response = await self._try_handle_command(message, command_content)
                
                if command_response is None:
                    # No matching command, treat as normal message for AI but keep command context
                    # This is critical - process the command as a regular message but preserve context
                    await self._handle_ai_response(message, user_id, username, channel_id, query, conversation_history)
            else:
                # Regular message, handle with AI
                await self._handle_ai_response(message, user_id, username, channel_id, query, conversation_history)
        
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            traceback.print_exc()