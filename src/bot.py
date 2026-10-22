import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import logging
import os
import sys
import time
import traceback
from dotenv import load_dotenv
from discord.ext.commands.errors import CommandNotFound

# Add the project root to the Python path for proper imports
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
    
# Import local modules
from src.command_handler import CommandHandler
from src.game_manager import GameManager
from src.rate_limiter import RateLimiter
from src.user_data_manager import UserDataManager

# Import refactored modules
from src.ai_response_handler import AIResponseHandler
from src.reply_log import ReplyLog
from src.message_handler import MessageHandler
from src.slash_commands import SlashCommandManager
from src.version import __version__
from src.web_server import WebServer
from src.message_processor import MessageProcessor
from src.music_manager import MusicManager
from src import backups, emoji_notes, pdf_notes, setup_info

# Setup logging
logger = logging.getLogger(__name__)

# Suppress CommandNotFound error logging
logging.getLogger('discord.ext.commands.bot').setLevel(logging.WARNING)

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
AZURE_OPENAI_KEY = os.getenv('AZURE_OPENAI_KEY', '')
AZURE_OPENAI_ENDPOINT = os.getenv('AZURE_OPENAI_ENDPOINT', '')
AZURE_OPENAI_DEPLOYMENT = os.getenv('AZURE_OPENAI_DEPLOYMENT', 'gpt-5-mini')

class ChronoChunk(commands.Bot):
    """Main bot class that ties everything together"""
    
    def __init__(self):
        """Initialize the bot and wire its components together"""
        # Initialize Discord bot with required parameters
        intents = discord.Intents.default()
        intents.message_content = True  # This is a privileged intent
        intents.members = True  # This is a privileged intent
        intents.voice_states = True  # Enable voice state updates
        
        super().__init__(
            command_prefix='/',
            intents=intents
        )
        
        # One reply log shared by the AI handler and /forget, so deleting a person
        # deletes both their chat data and the encrypted copy of their replies.
        self.reply_log = ReplyLog()

        # Initialize the user data manager
        self.user_data_manager = UserDataManager(reply_log=self.reply_log)
        
        # Create game manager
        self.game_manager = GameManager()
        
        # Create message handler
        self.message_handler = MessageHandler(self)
        
        # Create AI response handler
        self.ai_response_handler = AIResponseHandler(
            endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_KEY,
            deployment=AZURE_OPENAI_DEPLOYMENT,
            reply_log=self.reply_log,
        )
        
        # PDFs are described page by page with the same model and budget as chat
        pdf_notes.set_describer(self.ai_response_handler.describe_images)
        emoji_notes.set_describer(self.ai_response_handler.describe_images)

        # Store a reference as ai_handler for compatibility
        self.ai_handler = self.ai_response_handler
        
        # One rate limiter for typed messages and slash commands, so the limits are shared
        self.rate_limiter = RateLimiter()

        # One music player for the whole bot, so the voice events and the commands see the same queues
        self.music_manager = MusicManager()

        # Typed commands: /tldr, /usage, /model, /setup, /stats and /help
        self.command_handler = CommandHandler(bot=self, rate_limiter=self.rate_limiter)
        
        # Create message processor with correct parameter.
        # Only /tldr, /usage and a bare /model, /setup, /stats, /courses or /help are typed commands. Any other "/word" message
        # is chat, so "/stop being weird" does not get mistaken for the music command.
        self.message_processor = MessageProcessor(
            bot=self,
            message_handler=self.message_handler,
            ai_response_handler=self.ai_response_handler,  # CHANGE THIS LINE - use ai_response_handler instead of ai_handler
            user_data_manager=self.user_data_manager,
            game_manager=self.game_manager,
            command_handler=self.command_handler,
            rate_limiter=self.rate_limiter,
            text_commands={"tldr", "usage", "model", "setup", "stats", "courses", "help"}
        )
        
        # Register event handlers
        self.setup_event_handlers()
        
        logger.info(f"ChronoChunk v{__version__} initialized with per-user rate limits and an AI budget guard")
    
    def setup_event_handlers(self):
        """Setup all event handlers for the bot"""
        @self.event
        async def on_guild_emojis_update(guild, before, after):
            # A new emoji gets its note as soon as the server adds it
            self._emoji_notes_task = asyncio.create_task(emoji_notes.describe_missing([guild]))

        @self.event
        async def on_voice_state_update(member, before, after):
            try:
                # Forward voice state updates to the music manager
                await self.music_manager.on_voice_state_update(member, before, after)
            except Exception as e:
                logger.error(f"Error handling voice state update: {e}")
                logger.error(traceback.format_exc())
    
    async def setup_hook(self) -> None:
        """Setup hook for bot initialization"""
        try:
            # Initialize web server
            self.web_server = WebServer()
            await self.web_server.start()
            logger.info("Web server started successfully")
        except Exception as e:
            logger.warning(f"Web server couldn't start (may be already running): {e}")
        
        # /setup names the machine the bot is on
        await setup_info.find_host()

        # A copy of the database now and once a day. The task is kept so it is not garbage collected.
        self.backup_task = asyncio.create_task(backups.run_daily())

        # Initialize slash commands WITHOUT rate limiter
        self.slash_commands = SlashCommandManager(
            bot=self,
            game_manager=self.game_manager,
            user_data_manager=self.user_data_manager,
            ai_handler=self.ai_handler,
            message_handler=self.message_handler,
            music_manager=self.music_manager
        )
        
        # Register slash commands
        await self.slash_commands.register_commands()
    
    async def on_message(self, message):
        """Handle incoming Discord messages"""
        # Skip our own messages
        if message.author == self.user:
            return
        
        try:
            # Rate limits are applied inside the processor, only to messages the bot answers
            await self.message_processor.process_message(message)
                
        except Exception as e:
            # Enhanced error reporting
            import traceback
            error_type = type(e).__name__
            tb = traceback.extract_tb(sys.exc_info()[2])
            error_file = tb[-1].filename
            error_line = tb[-1].lineno
            error_func = tb[-1].name
            
            error_msg = f"Error processing message: {error_type}: {e} in {error_file}, line {error_line}, function {error_func}"
            logger.error(error_msg)
            logger.error("Full traceback:")
            logger.error(traceback.format_exc())

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle errors from slash commands"""
        logger.error(f"Slash command error: {error}")
        try:
            await interaction.response.send_message("something broke on my end, try again?", ephemeral=True)
        except Exception:
            try:
                await interaction.followup.send("something broke on my end, try again?", ephemeral=True)
            except Exception as e:
                logger.error(f"Could not send slash command error response: {e}")
    
    async def on_ready(self):
        """Called when the bot is ready to receive events"""
        try:
            # on_ready only fires once the bot has logged in
            assert self.user is not None
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
                
            # Look at every server emoji that has no note yet. It runs behind the bot, which is ready meanwhile.
            emoji_count = sum(len(guild.emojis) for guild in self.guilds)
            logger.info(f"{emoji_count} custom emoji across {len(self.guilds)} servers")
            self._emoji_notes_task = asyncio.create_task(emoji_notes.describe_missing(list(self.guilds)))
            await self.music_manager.start_inactivity_checker()

        except Exception as e:
            logger.error(f"Error in on_ready: {e}")
    
    async def handle_command_error(self, ctx, error):
        """Handle errors from command processing"""
        try:
            if isinstance(error, CommandNotFound):
                # Simply ignore command not found errors
                return
                
            # Log the error
            logger.error(f"Command error: {error}")
            
            await ctx.channel.send("yo something broke, try again")
            
        except Exception as e:
            logger.error(f"Error handling command error: {e}")
    
    async def on_error(self, event, *args, **kwargs):
        """Global error handler for all events"""
        error_type, error, tb = sys.exc_info()
        
        # Generate error ID for tracking
        error_id = f"ERR-{int(time.time())}"
        
        logger.error(f"Error ID {error_id} | Unhandled exception in {event}: {getattr(error_type, '__name__', 'unknown')}: {error}")
        logger.error("".join(traceback.format_tb(tb)))
        
        if event == 'on_message' and len(args) > 0:
            message = args[0]
            try:
                await message.channel.send("something just broke on my end fr, my dev will look at it")
            except Exception as exc:
                logger.warning(f"Could not notify channel of error: {exc}")

    async def close(self):
        """Ensure proper cleanup when the bot is shutting down"""
        try:
            logger.info("Bot is shutting down, performing cleanup...")
            
            # Save any in-memory data
            if hasattr(self, 'user_data_manager'):
                logger.info("Saving user data...")
                # Any final save operations
            
            # Close web server if it exists
            if hasattr(self, 'web_server'):
                logger.info("Stopping web server...")
                await self.web_server.stop()
                
            # Close any other resources
            logger.info("Cleanup complete, shutting down bot")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        
        # Call parent close method
        await super().close()

# The bot that is running, so a stop signal can close it
_running_bot = None
# The close that a stop signal started, kept so it is not lost before it ends
_closing = None


def shutdown_bot():
    """
    Close the bot after a stop signal. Closing ends bot.run() normally. Stopping the event
    loop instead, as this used to, made every restart log "Bot crashed: Event loop stopped
    before Future completed" and skipped the cleanup in close().
    """
    logger.info("Initiating bot shutdown sequence")
    bot = _running_bot
    if bot is None:
        logger.warning("No running bot to shut down")
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("The event loop is not running, nothing to shut down")
        return

    def close() -> None:
        global _closing
        _closing = loop.create_task(bot.close())

    # The signal can land while the loop waits for network events. This wakes it.
    loop.call_soon_threadsafe(close)


def main() -> None: 
    """Start up the bot"""
    global _running_bot
    try:
        if not DISCORD_TOKEN:
            raise ValueError("DISCORD_TOKEN is not set")
        bot = _running_bot = ChronoChunk()
        bot.run(DISCORD_TOKEN, log_handler=None)  # logging is already set up in run.py
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        raise

if __name__ == "__main__":
    main()