import asyncio
import re
import logging
import discord
from datetime import datetime
from typing import Dict, Any, List, Optional, Union
from config.config import Config

# Setup logging
logger = logging.getLogger(__name__)

class MessageHandler:
    """Handles processing messages and managing conversation history"""
    
    def __init__(self):
        """Initialize message handler"""
        self.conversation_memory = {}  # channel_id -> list of messages
        self.last_channel_messages = {}  # channel_id -> list of recent messages
    
    def update_channel_history(self, channel_id: str, user_id: str, username: str, 
                               content: str, is_bot: bool, is_command: bool = False):
        """Update the channel history with a new message"""
        if channel_id not in self.last_channel_messages:
            self.last_channel_messages[channel_id] = []
        
        # For command-like messages, store without the slash for more natural flow
        content_to_store = content
        if is_command and content.startswith('/') and not content.startswith('//'):
            content_to_store = content[1:] if len(content) > 1 else content
        
        # Add this message to the channel history
        self.last_channel_messages[channel_id].append({
            "author_id": user_id,
            "author_name": username,
            "is_bot": is_bot,
            "content": content_to_store,
            "timestamp": datetime.now().isoformat()
        })
        
        # Keep only last messages based on config
        if len(self.last_channel_messages[channel_id]) > Config.CHANNEL_HISTORY_SIZE:
            self.last_channel_messages[channel_id] = self.last_channel_messages[channel_id][-Config.CHANNEL_HISTORY_SIZE:]
    
    def update_conversation_memory(self, channel_id: str, username: str, 
                                  user_message: str, bot_response: str, is_command: bool = False):
        """Update the conversation memory with user and bot messages"""
        if channel_id not in self.conversation_memory:
            self.conversation_memory[channel_id] = []
        
        # Format messages to clearly indicate who is speaking
        if is_command and user_message.startswith('/'):
            # Store without the slash prefix for better context
            clean_message = user_message[1:] if len(user_message) > 1 else user_message
            self.conversation_memory[channel_id].append(f"USER ({username}): {clean_message}")
        else:
            self.conversation_memory[channel_id].append(f"USER ({username}): {user_message}")
        
        self.conversation_memory[channel_id].append(f"BOT (ChronoChunk): {bot_response}")
        
        # Keep memory size manageable but sufficient for context
        if len(self.conversation_memory[channel_id]) > 2 * Config.MEMORY_SIZE:
            self.conversation_memory[channel_id] = self.conversation_memory[channel_id][-Config.MEMORY_SIZE*2:]
    
    async def build_conversation_context(self, channel_id: str, user_data: Dict[str, Any], 
                                        is_correction: bool = False) -> str:
        """Build context from past conversations"""
        # Get recent channel messages for context (who said what)
        context_parts = []
        
        # Add channel context first - use configured size
        if channel_id in self.last_channel_messages and self.last_channel_messages[channel_id]:
            recent_msgs = self.last_channel_messages[channel_id][-Config.DISPLAY_CONTEXT_SIZE:]  
            context_parts.append("RECENT CHANNEL MESSAGES (IN ORDER):")
            for msg in recent_msgs:
                author_name = msg["author_name"]
                content = msg["content"]
                is_bot = msg.get("is_bot", False)
                
                if content and len(content) > 0:
                    # Format clearly with user/bot identification
                    if is_bot:
                        context_parts.append(f"BOT ({author_name}): \"{content}\"")
                    else:
                        context_parts.append(f"USER ({author_name}): \"{content}\"")
        
        # Initialize memory for channel conversations if it doesn't exist
        if channel_id not in self.conversation_memory:
            self.conversation_memory[channel_id] = []
        
        # Get recent bot conversation memory
        memory = self.conversation_memory[channel_id]
        
        # Add bot conversation history - more structured format
        if memory:
            context_parts.append("\nCONVERSATION HISTORY:")
            # Include all available memory for better continuity
            for entry in memory[-Config.MEMORY_SIZE*2:]:
                context_parts.append(entry)
        
        # Add facts about the user
        if user_data.get("facts"):
            context_parts.append("\nFACTS ABOUT THIS USER:")
            for fact in user_data["facts"][-15:]:  # Include more facts (up from 10)
                content = fact["content"] if isinstance(fact, dict) and "content" in fact else fact
                context_parts.append(f"- {content}")
        
        # Add topics of interest
        if user_data.get("topics_of_interest"):
            interests = ", ".join(user_data["topics_of_interest"])
            context_parts.append(f"\nUSER INTERESTS: {interests}")
        
        # Make it plain text
        context = "\n".join(context_parts)
        
        return context
    
    async def send_response(self, channel, content: str, user_mention: Optional[str] = None, 
                           should_mention: bool = True) -> Optional[discord.Message]:
        """Send a response to a channel, with smart message splitting and mentions"""
        # Handle empty content case
        if not content:
            return await channel.send("...")
        
        # Skip mention logic for DMs
        is_dm = isinstance(channel, discord.DMChannel)
        if is_dm:
            should_mention = False
        
        # Add user mention if needed in group chat with multiple active users
        if user_mention and should_mention and not is_dm:
            # Check for multiple active users in one step
            recent_messages = [msg async for msg in channel.history(limit=5)]
            unique_authors = {msg.author.id for msg in recent_messages if not msg.author.bot}
            
            # Only add mention if multiple people are talking
            if len(unique_authors) > 1:
                content = f"{user_mention} {content}"
        
        # Convert Discord emoji codes to actual emojis - improved version
        if ":" in content and hasattr(channel, "guild") and channel.guild:
            # More accurate regex that handles emoji patterns at word boundaries
            emoji_pattern = re.compile(r'(?<!\S):([a-zA-Z0-9_]+):(?!\S)')
            
            # Create emoji lookup dictionary
            if channel.guild.emojis:  # Only process if guild has emojis
                # Case-insensitive matching for better reliability
                guild_emojis = {emoji.name.lower(): str(emoji) for emoji in channel.guild.emojis}
                
                # First pass: Try exact matches
                for match in emoji_pattern.finditer(content):
                    emoji_name = match.group(1)
                    if emoji_name.lower() in guild_emojis:
                        content = content.replace(f":{emoji_name}:", guild_emojis[emoji_name.lower()])
                
                # Second pass: Try fuzzy matching for any remaining emoji codes
                remaining_emoji_pattern = re.compile(r':([a-zA-Z0-9_]+):')
                for match in remaining_emoji_pattern.finditer(content):
                    emoji_name = match.group(1)
                    # Try to find closest match if not exact
                    closest_match = None
                    for guild_emoji in guild_emojis:
                        if emoji_name.lower() in guild_emoji or guild_emoji in emoji_name.lower():
                            closest_match = guild_emoji
                            break
                    
                    if closest_match:
                        content = content.replace(f":{emoji_name}:", guild_emojis[closest_match])
        
        # Fix newlines - remove empty lines
        content = re.sub(r'\n\s*\n', '\n', content)
        
        # Fix multiple spaces - replace with single space
        content = re.sub(r' +', ' ', content)
        
        try:
            # Split content for longer messages
            if len(content) <= 1900:  # Discord limit is 2000, leave some room for safety
                return await channel.send(content)
            
            # Intelligently split the message
            sentences = re.split(r'(?<=[.!?])\s+', content)
            messages = []
            current_message = ""
            
            # Handle specially for single huge sentences - very rare but possible
            if len(sentences) == 1 and len(sentences[0]) > 1900:
                # Split by chunks directly
                chunks = [sentences[0][i:i+1900] for i in range(0, len(sentences[0]), 1900)]
                first_message = None
                
                for chunk in chunks:
                    sent = await channel.send(chunk)
                    if not first_message:
                        first_message = sent
                
                return first_message
            
            for sentence in sentences:
                # If adding this sentence would make the message too long, send current message
                if len(current_message) + len(sentence) > 1500:
                    if current_message:  # Only append non-empty messages
                        messages.append(current_message)
                    current_message = sentence
                else:
                    # Append with space only if current_message isn't empty
                    current_message = f"{current_message} {sentence}" if current_message else sentence
            
            # Add the last message
            if current_message:
                messages.append(current_message)
            
            # Send messages and return the first one
            first_message = None
            for message_content in messages:
                try:
                    sent_message = await channel.send(message_content)
                    if not first_message:
                        first_message = sent_message
                except Exception as e:
                    logger.error(f"Error sending message: {e}")
            
            return first_message
        
        except Exception as e:
            logger.error(f"Error in send_response: {e}")
            try:
                # Last resort fallback
                return await channel.send("shit, something went wrong sending that message")
            except:
                pass  # If even this fails, just give up
            
            return None