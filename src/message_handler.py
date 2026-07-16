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
    
    def __init__(self, bot=None):
        """Initialize message handler"""
        from config.config import Config  # Import at method level to avoid circular imports
        self.conversation_memory = {}  # channel_id -> list of messages
        self.last_channel_messages = {}  # channel_id -> list of recent messages
        self.config = Config  # Add direct reference to Config class
        self.bot = bot  # Store the bot reference if provided
    
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
    
    def update_conversation_memory(self, channel_id: str, username: str, user_message: str, bot_response: str, is_command: bool = False) -> None:
        """Update the conversation memory for a channel with proper attribution"""
        # Create conversation memory for this channel if it doesn't exist
        if channel_id not in self.conversation_memory:
            self.conversation_memory[channel_id] = []
        
        # Don't store slash commands in conversation memory
        if is_command and user_message.startswith('/'):
            # Strip the slash for better context
            clean_message = user_message[1:] if len(user_message) > 1 else user_message
            self.conversation_memory[channel_id].append(f"USER ({username}): {clean_message}")
        else:
            self.conversation_memory[channel_id].append(f"USER ({username}): {user_message}")
        
        # Add bot response to memory with clear attribution
        self.conversation_memory[channel_id].append(f"BOT (ChronoChunk): {bot_response}")
        
        # CRITICAL FIX: Keep more context history - at least 20 messages
        memory_size = max(20, Config.MEMORY_SIZE * 2)
        if len(self.conversation_memory[channel_id]) > memory_size:
            # Keep at least the last 20 messages for better context
            self.conversation_memory[channel_id] = self.conversation_memory[channel_id][-memory_size:]
    
    def _extract_recent_topics(self, channel_id: str) -> List[str]:
        """Extract topics from recent messages for relevance filtering"""
        # Simple implementation - extract words from recent messages
        topics = set()
        if channel_id in self.last_channel_messages:
            for msg in self.last_channel_messages[channel_id][-5:]:  # Last 5 messages
                content = msg.get("content", "").lower()
                # Get meaningful words (exclude common stopwords)
                words = [w for w in re.findall(r'\b\w+\b', content) if len(w) > 3]
                topics.update(words)
        return list(topics)
    
    # Patterns that identify game/command bot responses — excluded from AI chat context
    _GAME_RESPONSE_PATTERNS = (
        "tries left",
        "i'm thinking of a number",
        "start guessing with /guess",
        "the number is higher",
        "the number is lower",
        "game started",
        "game over",
        "yooo you got it",
        "you already got a game going",
        "you don't have a game going",
        "you don't even have a game going",
    )

    def _is_game_response(self, content: str) -> bool:
        """Return True if this bot message is a game command response, not AI chat."""
        lower = content.lower()
        return any(p in lower for p in self._GAME_RESPONSE_PATTERNS)

    async def build_conversation_context(self, channel_id: str, user_data: Dict[str, Any], is_correction: bool = False) -> str:
        """Build context for conversation"""
        context_parts = []

        # Who the bot is currently responding to
        if user_data:
            speaker = user_data.get("username", "")
            if speaker:
                context_parts.append(f"RESPONDING TO: {speaker} (address them as 'u'/'ur', never in third person)")

        # Known facts about this person
        if user_data:
            facts = user_data.get("facts", [])
            if facts:
                context_parts.append("\nWHAT YOU KNOW ABOUT THEM:")
                for fact in facts[-15:]:
                    context_parts.append(f"  {fact}")

        # Recent channel conversation (includes all users if group chat)
        # Game/command bot responses are excluded so they don't bleed into casual chat
        context_size = min(30, max(20, Config.DISPLAY_CONTEXT_SIZE))
        if channel_id in self.last_channel_messages and self.last_channel_messages[channel_id]:
            recent_msgs = self.last_channel_messages[channel_id][-context_size:]
            context_parts.append("\nRECENT CONVERSATION (NEWEST LAST):")

            for i, msg in enumerate(recent_msgs):
                author_name = msg["author_name"]
                content = msg["content"]
                is_bot = msg.get("is_bot", False)

                if not content:
                    continue
                # Skip game/command bot responses — they must not leak into AI chat context
                if is_bot and self._is_game_response(content):
                    continue

                prefix = ">>> " if i >= len(recent_msgs) - 3 else ""
                if is_bot:
                    context_parts.append(f"{prefix}YOU (ChronoChunk): {content}")
                else:
                    context_parts.append(f"{prefix}{author_name}: {content}")

        # Follow-up detection — inject topic hint for short replies
        if channel_id in self.last_channel_messages and len(self.last_channel_messages[channel_id]) >= 2:
            all_msgs = self.last_channel_messages[channel_id]
            bot_msgs   = [m for m in all_msgs[-5:] if m.get("is_bot")]
            user_msgs  = [m for m in all_msgs[-5:] if not m.get("is_bot")]

            if bot_msgs and user_msgs:
                last_bot_msg  = bot_msgs[-1].get("content", "").lower()
                last_user_msg = user_msgs[-1].get("content", "").lower()

                if len(last_user_msg.split()) <= 5:
                    context_parts.append("\nNOTE: Short reply — this is a FOLLOW-UP to your last message. Stay on the same topic.")
                    topic_words = {
                        w for w in re.findall(r'\b[a-z]{4,}\b', last_bot_msg)
                        if w not in {'like','dont','just','with','that','this','have','about',
                                     'what','when','where','from','your','been','would','could',
                                     'since','them','they','than','then','some'}
                    }
                    if topic_words:
                        context_parts.append(f"CURRENT TOPIC: {', '.join(topic_words)}")

        return "\n".join(context_parts)
    
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