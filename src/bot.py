import asyncio
import discord
from discord.ext import commands
import google.generativeai as genai
import json
import logging
import os
import re
import sys
import random
from datetime import datetime
from dotenv import load_dotenv
from aiohttp import web

# Add the project root to the Python path for proper imports
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
    
# Import local modules
from config.ai_config import PERSONALITY_PROMPT
from config.config import Config
from src.command_handler import CommandHandler, RateLimitError
from src.game_manager import GameManager
from src.intent_detector import IntentDetector
from src.rate_limiter import RateLimiter
from src.user_data_manager import UserDataManager
from typing import List, Dict, Any, Optional

# Setup logging
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Configure Gemini API
genai.configure(api_key=GEMINI_API_KEY)

class ChronoChunk(commands.Bot):
    """main bot class that ties everything together"""
    
    def __init__(self):
        """Initialize the bot with all required components"""
        # Init Discord stuff with needed perms
        intents = discord.Intents.default()
        intents.message_content = True  # Need to read messages
        intents.members = True  # Need to see server members
        super().__init__(command_prefix='/', intents=intents)
        
        # Initialize API connections
        genai.configure(api_key=GEMINI_API_KEY)
        self.ai_model = genai.GenerativeModel("gemini-1.5-flash-latest")
        
        # Initialize components that depend on API 
        self.user_data_manager = UserDataManager()
        self.intent_detector = IntentDetector()
        
        # Other components
        self.rate_limiter = RateLimiter()
        self.game_manager = GameManager()
        self.command_handler = CommandHandler(
            bot=self,
            game_manager=self.game_manager,
            user_data_manager=self.user_data_manager,
            rate_limiter=self.rate_limiter,
            intent_detector=self.intent_detector
        )
        
        # Set up properties for tracking conversation state
        self.conversation_memory = {}  # channel_id -> list of messages
        self.last_channel_messages = {}  # channel_id -> list of recent messages
        self.important_topics = Config.IMPORTANT_TOPICS
        
        logger.info("Bot initialized")
        
    async def setup_hook(self) -> None:
        """set up stuff that needs to run before bot starts"""
        try:
            # set up health check endpoint for hosting
            app = self.setup_web_server()
            runner = web.AppRunner(app)
            await runner.setup()
            
            # Default web server settings if not in Config
            web_host = getattr(Config, 'WEB_HOST', '0.0.0.0')
            web_port = getattr(Config, 'WEB_PORT', 10000)
            
            site = web.TCPSite(runner, host=web_host, port=web_port)
            await site.start()
            logger.info(f"Health check server running on port {web_port}")
            
        except Exception as e:
            logger.error(f"Health check setup failed: {e}")
            # don't raise here, bot can work without web server
            
    def setup_web_server(self) -> web.Application:
        """set up health check endpoint"""
        app = web.Application()
        app.router.add_get('/health', self.health_check)
        return app
        
    async def health_check(self, request: web.Request) -> web.Response:
        """endpoint for hosting service to check if we alive"""
        return web.Response(text="OK")
            
    async def on_ready(self):
        """Called when the bot is ready to receive events"""
        try:
            logger.info(f"Logged in as {self.user.name} ({self.user.id})")
            logger.info(f"Connected to {len(self.guilds)} servers")
            
            # Setup status
            activity = discord.Activity(
                type=discord.ActivityType.listening, 
                name="your deepest thoughts"
            )
            await self.change_presence(activity=activity, status=discord.Status.online)
            
            # Log bot details
            for guild in self.guilds:
                logger.info(f"Connected to server: {guild.name} ({guild.id}) with {len(guild.members)} members")
                
        except Exception as e:
            logger.error(f"Error in on_ready: {e}")
        
    async def handle_command_error(self, ctx, error):
        """Handle errors from command processing"""
        try:
            if isinstance(error, discord.app_commands.CommandNotFound):
                # Simply ignore command not found errors
                return
                
            # Log the error
            logger.error(f"Command error: {error}")
            
            # Send a friendly error message
            await ctx.channel.send("damn, something went wrong with that command")
            
        except Exception as e:
            logger.error(f"Error handling command error: {e}")
            
    async def on_message(self, message: discord.Message) -> None:
        """handle incoming messages"""
        # ignore our own messages
        if message.author == self.user:
            return
            
        try:
            # get permanent discord user ID
            user_id = str(message.author.id)
            channel_id = str(message.channel.id)
            username = message.author.display_name
            
            # log message
            logger.info(f"Message from {message.author.name} ({user_id}): {message.content[:50]}...")
            
            # Store message in channel history for context
            if channel_id not in self.last_channel_messages:
                self.last_channel_messages[channel_id] = []
                
            # Add this message to the channel history with clear author identification
            if message.content.startswith('/') and not message.content.startswith('//'):
                # Store command-like messages without the slash for more natural conversation flow
                content_to_store = message.content[1:] if len(message.content) > 1 else message.content
                self.last_channel_messages[channel_id].append({
                    "author_id": user_id,
                    "author_name": username,
                    "is_bot": False,
                    "content": content_to_store,
                    "timestamp": datetime.now().isoformat()
                })
            else:
                self.last_channel_messages[channel_id].append({
                    "author_id": user_id,
                    "author_name": username,
                    "is_bot": False,  # Explicitly mark as not from the bot
                    "content": message.content,
                    "timestamp": datetime.now().isoformat()
                })
            
            # Keep only last messages based on config
            if len(self.last_channel_messages[channel_id]) > Config.CHANNEL_HISTORY_SIZE:
                self.last_channel_messages[channel_id] = self.last_channel_messages[channel_id][-Config.CHANNEL_HISTORY_SIZE:]
            
            # load user data with username
            user_data = self.user_data_manager.load_user_data(user_id, username)
            
            # Check if message is a correction using AI instead of fixed word list
            is_correction = await self.intent_detector.detect_correction_intent(message.content)
            
            # Check if this is a reply to one of our messages
            is_reply_to_bot = False
            if message.reference and message.reference.message_id:
                try:
                    # Try to fetch the message being replied to
                    referenced_msg = await message.channel.fetch_message(message.reference.message_id)
                    if referenced_msg.author.id == self.user.id:
                        is_reply_to_bot = True
                        
                        # If it's a reply to the bot and not a command, treat as a chat query
                        if not message.content.startswith('/'):
                            try:
                                self.rate_limiter.check_rate_limit(user_id, "chat")
                                conversation_history = await self.build_conversation_context(channel_id, user_data, is_correction)
                                await self.process_ai_message(message, message.content, conversation_history)
                                return
                            except RateLimitError as e:
                                await self.send_response(message.channel, str(e), user_mention=message.author.mention)
                                return
                except discord.NotFound:
                    pass  # Message not found, continue normally
            
            # process commands first
            await self.process_commands(message)
            
            # handle special commands
            if message.content.startswith('/'):
                # extract command
                parts = message.content.split()
                command = parts[0][1:]  # remove the slash
                args = parts[1:] if len(parts) > 1 else []
                
                # run registered command if exists
                cmd_response = None
                try:
                    cmd_response = await self.command_handler.handle_command(command, args, message, user_id)
                except RateLimitError as e:
                    await self.send_response(message.channel, str(e), user_mention=message.author.mention)
                    return
                    
                if cmd_response:
                    await self.send_response(message.channel, cmd_response, user_mention=message.author.mention)
                    return
                    
                # For unrecognized commands, keep the slash to maintain the raw message
                # This makes sure the AI knows they tried a command, keeping context better
                try:
                    self.rate_limiter.check_rate_limit(user_id, "chat")
                except RateLimitError as e:
                    await self.send_response(message.channel, str(e), user_mention=message.author.mention)
                    return
                    
                # Process as AI query but keep the original form to maintain slang
                query = message.content
                conversation_history = await self.build_conversation_context(channel_id, user_data, is_correction)
                await self.process_ai_message(message, query, conversation_history)
            else:
                # don't process regular messages in DMs
                if isinstance(message.channel, discord.DMChannel):
                    return
                    
                # check rate limit for chat/AI stuff
                try:
                    self.rate_limiter.check_rate_limit(user_id, "chat")
                except RateLimitError as e:
                    return
                    
                # extract facts n stuff from message (pass username)
                await self.user_data_manager.extract_and_save_facts(
                    user_id,
                    message.content,
                    username
                )
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            
    def extract_important_topics(self, message_content: str) -> List[str]:
        """Find sensitive topics in messages more efficiently"""
        # Early return for empty messages
        if not message_content:
            return []
            
        # Convert to lowercase once for efficiency
        message_lower = message_content.lower()
        
        # Use set comprehension for faster lookup
        return [topic for topic in self.important_topics 
                if topic in message_lower]
        
    async def build_conversation_context(self, channel_id: str, user_data: Dict[str, Any], is_correction: bool = False) -> str:
        """build context from past conversations"""
        # Get recent channel messages for context (who said what)
        context_parts = []
        
        # Add channel context first - use configured size
        if channel_id in self.last_channel_messages and self.last_channel_messages[channel_id]:
            recent_msgs = self.last_channel_messages[channel_id][-Config.DISPLAY_CONTEXT_SIZE:]  # Use configured context size
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
        
        # init memory for channel conversations
        if channel_id not in self.conversation_memory:
            self.conversation_memory[channel_id] = []
            
        # get recent bot conversation memory
        memory = self.conversation_memory[channel_id]
        
        # add bot conversation history - more structured format
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
            
        # make it plain text
        context = "\n".join(context_parts)
        
        return context
    
    async def send_response(self, channel, content, user_mention=None, should_mention=True):
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
        
        # Convert Discord emoji codes to actual emojis - only if needed
        if ":" in content and hasattr(channel, "guild") and channel.guild:
            # Pre-compile the regex for efficiency
            emoji_pattern = re.compile(r":([a-zA-Z0-9_]+):")
            
            # discord's custom emoji system is a bit nuts - they use :emoji_name: format in text
            # but need to be converted to <:emoji_name:emoji_id> format to actually display
            # we're creating a lookup dict to avoid searching the whole list for each emoji
            if channel.guild.emojis:  # Only process if guild has emojis
                guild_emojis = {emoji.name.lower(): str(emoji) for emoji in channel.guild.emojis}
                
                for match in emoji_pattern.finditer(content):
                    emoji_name = match.group(1)
                    # Use a dictionary lookup for guild emojis if available
                    if emoji_name.lower() in guild_emojis:
                        content = content.replace(f":{emoji_name}:", guild_emojis[emoji_name.lower()])
        
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
            
    async def process_ai_message(self, message: discord.Message, query: str, conversation_history: str) -> None:
        """process an AI query and send response"""
        try:
            channel_id = str(message.channel.id)
            user_id = str(message.author.id)
            username = message.author.display_name
            
            # Check for argumentative intent
            is_argumentative, arg_type = await self.intent_detector.detect_argumentative_intent(query)
            
            # Check for sensitive topics
            important_topics = self.extract_important_topics(query)
            
            # notify that we're "typing"
            async with message.channel.typing():
                # Start building the prompt with personality and instructions first
                prompt_parts = []
                
                # prompt engineering 101: order matters a ton with these LLMs
                # personality stuff goes first so it colors everything else
                # then instructions, then context, then the actual query last
                # this makes the model focus on the latest message while keeping personality consistent
                prompt_parts.append(PERSONALITY_PROMPT.replace("{query}", "").replace("{conversation_history}", ""))
                
                # Add additional instructions for clarity and context-awareness
                prompt_parts.append("""
                IMPORTANT INSTRUCTIONS:
                1. MESSAGES MUST BE SHORT - MAX 1-2 SENTENCES, NEVER EXCEED 3 SENTENCES TOTAL
                2. TYPING STYLE IS CRITICAL - type like a real Gen Z teen in Discord:
                   - almost never use capital letters
                   - rarely use periods at end of sentences
                   - use "u" not "you", "ur" not "your", "n" not "and" consistently
                   - drop apostrophes in contractions (dont, cant, wont)
                   - make occasional typos like "teh" instead of "the"
                   - DON'T put sentences on separate lines - keep as continuous text
                   - use multiple question marks or exclamation marks (???)
                3. Use emojis VERY SPARINGLY - maximum 1 emoji per message
                4. Use a variety of slang expressions and casual language
                5. NEVER use proper grammar or formal writing style
                6. Don't end messages asking the same questions every time
                7. CRITICAL: Pay attention to who said what in the conversation history
                8. Remember everything that was previously discussed in the conversation
                9. Don't confuse what you said with what the user said
                10. Use shortened words like "u" instead of "you", "rn" instead of "right now"
                11. NEVER capitalize first words of sentences
                12. Be unpredictable and natural - vary your style slightly
                13. IMPORTANT: ONLY be ridiculous/nonsensical about 50% of the time, be more grounded the rest of the time
                14. NEVER WRITE MORE THAN 1-2 SENTENCES. MESSAGES MUST BE SHORT.
                15. DON'T BE OVERLY DISMISSIVE - if the user asks a question, give an actual answer at least half the time(if the user is being a bitch, you can ignore this just give them what they give you)
                16. DON'T overuse phrases like "wtf u care" or similar dismissive phrases (but still do it sometimes since it is the personality)
                17. Don't constantly talk about the same random topics (like time travel) in every message (if it gets annoying stop else dont)
                18. If the user seems confused or annoyed, tone down the nonsense and be more responsive or else go wild
                """)
                
                # Extra instruction for command-like messages
                if query.startswith('/'):
                    command_style = """
                    EXTRA IMPORTANT - FAILED COMMAND TYPING STYLE:
                    - KEEP IT SHORT! 1-2 SENTENCES MAX, NEVER MORE THAN 3 SENTENCES TOTAL
                    - DON'T split sentences onto separate lines - one continuous paragraph
                    - respond EXTERME in casual style
                    - NEVER capitalize anything, especially not first words of sentences
                    - Drop all apostrophes (dont instead of don't, etc)
                    - Use "u" not "you", "ur" not "your", "n" instead of "and" 
                    - Use phrases like "bro", "fr", "ong", "no cap"naturally but not excessively
                    - NEVER sound like a chatbot or assistant
                    - Respond like a friend in a Discord chat, not a helper
                    - Use shortened/abbreviated words throughout 
                    - Drop unnecessary words that aren't needed
                    - Use run-on sentences with almost no punctuation
                    - Use multiple question marks (???) not just one
                    - Use AT MOST one emoji in the entire message or none at all (can use emoticon as well but not more than one or none at all)
                    - DON'T be overly dismissive - respond naturally to what they're asking (if they are being a bitch, you can ignore this just give them what they give you)
                    - SOMETIMES be helpful/informative 
                    
                    EXAMPLE STYLE TO COPY:
                    "bruh u fr tried that command lmaoo that shit dont even exist like what u even tryna do rn??? im confused asf"
                    """
                    prompt_parts.append(command_style)
                
                # Add argumentative instructions if needed - only when users are argumentative
                if is_argumentative:
                    # Random chance to argue back (40% of the time) - makes it feel more natural
                    if random.random() < 0.4:  
                        arg_instructions = f"""
                        ARGUMENT DETECTED - TYPE: {arg_type}
                        The user is being argumentative or insulting. As a real friend would:
                        - Match their energy but with personality
                        - Defend yourself with wit and bite
                        - Don't back down - throw it back at them
                        - Use comebacks appropriate to their level of intensity
                        - Include casual insults like "clown", "bro you trippin", "get outta here with that"
                        - Call them out on their BS
                        - If they insult you, insult them back but cleverly
                        - Be quick-witted and sharp with the comebacks
                        - No need to be polite - be real and authentic
                        - Do NOT apologize for having an opinion
                        - Occasionally use their own logic against them
                        - BUT YOU GOTTA GO BACK TO NORMAL IF YOU FORGIVE THEM which could be like 60% forgivness, 40% is just waiting for that 60% to hit and forgive. but still be bitchy tho and in a realicsic way to forgive them like u would in a real conversation
                        """
                        prompt_parts.append(arg_instructions)
                
                # Add sensitive topic warnings if needed
                if important_topics:
                    sensitive_instructions = f"""
                    ATTENTION: This message contains sensitive topics: {', '.join(important_topics)}
                    - Be mindful of these topics in your response
                    - Don't make jokes about these topics (light one or personalized one are fine to lighten the mood)
                    - Be supportive but not condescending
                    """
                    prompt_parts.append(sensitive_instructions)
                
                # Now add conversation history AFTER instructions but BEFORE the query
                if conversation_history:
                    prompt_parts.append("CONVERSATION CONTEXT:\n" + conversation_history)
                
                # Add user query with name as the LAST element
                if query.startswith('/'):
                    # If it was an unrecognized command, remove the slash to make it feel like a normal chat
                    clean_query = query[1:] if len(query) > 1 else query
                    prompt_parts.append(f"User ({username}) just said: \"{clean_query}\"")
                    # Add context about attempted command
                    prompt_parts.append(f"NOTE: The user tried to use a command that doesn't exist.")
                else:
                    prompt_parts.append(f"User ({username}) just said: \"{query}\"")
                
                # Ask AI to respond
                prompt_parts.append("""
                Your response as ChronoChunk:
                
                REMEMBER:
                - KEEP IT SHORT! 1-2 SENTENCES ONLY, NEVER MORE THAN 3
                - DON'T put sentences on separate lines - keep everything in one continuous paragraph
                - almost NEVER use capital letters
                - use "u" not "you", "ur" not "your", etc. 
                - rarely use periods at end of sentences
                - drop apostrophes (dont, wont, cant)
                - use emojis VERY SPARINGLY (only 1 emoji max if any)
                - use multiple question/exclamation marks for emphasis
                - Be natural and unpredictable like a real person would
                - Only be random/nonsensical about 10% of the time
                - When user asks a question, give a real answer at least half the time
                - Don't be overly dismissive or use "wtf u care" type phrases too much
                - Vary your topics instead of repeating the same themes (like time travel)
                """.strip())
                
                # Randomly adjust how bizarre the bot will be for this particular message
                # This creates more variation in responses - sometimes normal, sometimes weird
                # The prompt already has instructions for 50/50, but this adds real randomness
                nonsense_factor = random.random()
                if nonsense_factor < 0.3:  # 30% chance to be extra normal/coherent
                    prompt_parts.append("SPECIAL INSTRUCTION: Be very coherent and logical in this response. No nonsense or random topics.")
                elif nonsense_factor > 0.9:  # 20% chance to be extra weird
                    prompt_parts.append("SPECIAL INSTRUCTION: Be extra bizarre and random in this response. Mention something totally unexpected.")
                
                # Combine all parts
                prompt = "\n\n".join(prompt_parts)
                
                try:
                    response = await self.ai_model.generate_content_async(prompt)
                    ai_response = response.text.strip()
                    
                    # For some AIs, we need to strip off prefixes if returned
                    ai_response = re.sub(r'^(You:|Your response as ChronoChunk:|ChronoChunk:)\s*', '', ai_response)
                    
                    # Fix newlines - ensure there are no consecutive empty lines
                    ai_response = re.sub(r'\n\s*\n', '\n', ai_response)
                    
                    # Replace all newlines between sentences with spaces to make it look like natural typing
                    ai_response = re.sub(r'([.!?])\s*\n', r'\1 ', ai_response)
                    
                    # Fix multiple spaces in a row
                    ai_response = re.sub(r' +', ' ', ai_response)
                    
                    # Force shorter responses by truncating if necessary
                    sentences = re.split(r'(?<=[.!?])\s+', ai_response)
                    if len(sentences) > 3:
                        ai_response = ' '.join(sentences[:3])
                    
                    # Reduce emoji usage (if more than one emoji in the response, randomly remove some)
                    emoji_pattern = re.compile(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002702-\U000027B0\U000024C2-\U0001F251]+')
                    emojis = emoji_pattern.findall(ai_response)
                    if len(emojis) > 1:
                        # Keep only one random emoji from those found
                        emoji_to_keep = random.choice(emojis)
                        for emoji in emojis:
                            if emoji != emoji_to_keep:
                                ai_response = ai_response.replace(emoji, '', 1)
                    
                    # DON'T fix line endings with periods - it makes the casual style too formal
                    # ai_response = re.sub(r'([^.!?])\n', r'\1. ', ai_response)
                    
                    # Send response
                    sent_message = await self.send_response(message.channel, ai_response, user_mention=message.author.mention)
                    
                    # Also store the bot's response in the channel history
                    if channel_id not in self.last_channel_messages:
                        self.last_channel_messages[channel_id] = []
                        
                    self.last_channel_messages[channel_id].append({
                        "author_id": self.user.id,
                        "author_name": "ChronoChunk",
                        "is_bot": True,  # Explicitly mark as from the bot
                        "content": ai_response,
                        "timestamp": datetime.now().isoformat()
                    })
                    
                    # Keep only last messages according to config
                    if len(self.last_channel_messages[channel_id]) > Config.CHANNEL_HISTORY_SIZE:
                        self.last_channel_messages[channel_id] = self.last_channel_messages[channel_id][-Config.CHANNEL_HISTORY_SIZE:]
                    
                    # Store the message and response for context memory with clear attribution
                    if channel_id not in self.conversation_memory:
                        self.conversation_memory[channel_id] = []
                        
                    # Format messages to clearly indicate who is speaking
                    if query.startswith('/'):
                        # Store without the slash prefix for better context
                        clean_query = query[1:] if len(query) > 1 else query
                        self.conversation_memory[channel_id].append(f"USER ({username}): {clean_query}")
                    else:
                        self.conversation_memory[channel_id].append(f"USER ({username}): {query}")
                    self.conversation_memory[channel_id].append(f"BOT (ChronoChunk): {ai_response}")
                    
                    # Keep memory size manageable but sufficient for context
                    if len(self.conversation_memory[channel_id]) > 2 * Config.MEMORY_SIZE:
                        self.conversation_memory[channel_id] = self.conversation_memory[channel_id][-Config.MEMORY_SIZE*2:]
                    
                    # Save the conversation to user data
                    await self.user_data_manager.add_conversation(
                        user_id, 
                        query,
                        ai_response,
                        username
                    )
                    
                except Exception as e:
                    logger.error(f"Error generating AI response: {e}")
                    
                    # Fall back to a generic error message with personality
                    await self.send_response(message.channel, "ahh shit, my brain short-circuited for a sec. wanna try again?", user_mention=message.author.mention)
                    
        except Exception as e:
            logger.error(f"Error in process_ai_message: {e}")
            
def main() -> None:
    """start up the bot"""
    try:
        bot = ChronoChunk()
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        raise

if __name__ == "__main__":
    main() 