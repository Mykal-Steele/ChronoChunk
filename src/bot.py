import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import google.generativeai as genai
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
from config.config import Config
from src.command_handler import CommandHandler, RateLimitError
from src.game_manager import GameManager
from src.intent_detector import IntentDetector
from src.rate_limiter import RateLimiter
from src.user_data_manager import UserDataManager

# Import refactored modules
from src.ai_response_handler import AIResponseHandler
from src.message_handler import MessageHandler
from src.slash_commands import SlashCommandManager
from src.web_server import WebServer
from src.message_processor import MessageProcessor

# Setup logging
logger = logging.getLogger(__name__)

# Suppress CommandNotFound error logging
logging.getLogger('discord.ext.commands.bot').setLevel(logging.WARNING)

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

class ChronoChunk(commands.Bot):
    """Main bot class that ties everything together"""
    
    def __init__(self):
        """Initialize the bot without rate limiting or intent detection"""
        # Initialize Discord bot with required parameters
        intents = discord.Intents.default()
        intents.message_content = True  # This is a privileged intent
        intents.members = True  # This is a privileged intent
        intents.voice_states = True  # Enable voice state updates
        
        super().__init__(
            command_prefix='/',
            intents=intents
        )
        
        # Initialize the user data manager
        self.user_data_manager = UserDataManager()
        
        # Create game manager
        self.game_manager = GameManager()
        
        # Create message handler
        self.message_handler = MessageHandler(self)
        
        # Create AI response handler
        self.ai_response_handler = AIResponseHandler(
            api_key=GEMINI_API_KEY,
            important_topics=Config.IMPORTANT_TOPICS,
            user_data_manager=self.user_data_manager
        )
        
        # Store a reference as ai_handler for compatibility
        self.ai_handler = self.ai_response_handler
        
        # Create command handler
        self.command_handler = CommandHandler(
            bot=self,
            user_data_manager=self.user_data_manager,
            game_manager=self.game_manager
        )
        
        # Create message processor with correct parameter
        self.message_processor = MessageProcessor(
            bot=self,
            message_handler=self.message_handler,
            ai_response_handler=self.ai_response_handler,  # CHANGE THIS LINE - use ai_response_handler instead of ai_handler
            user_data_manager=self.user_data_manager,
            game_manager=self.game_manager
        )
        
        # Register event handlers
        self.setup_event_handlers()
        
        logger.info("Bot initialized without rate limiting")
    
    def setup_event_handlers(self):
        """Setup all event handlers for the bot"""
        @self.event
        async def on_voice_state_update(member, before, after):
            try:
                # Forward voice state updates to the music manager
                if hasattr(self.command_handler, 'music_manager'):
                    await self.command_handler.music_manager.on_voice_state_update(member, before, after)
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
        
        # Initialize slash commands WITHOUT rate limiter
        self.slash_commands = SlashCommandManager(
            bot=self,
            game_manager=self.game_manager,
            user_data_manager=self.user_data_manager,
            ai_handler=self.ai_handler,
            message_handler=self.message_handler
        )
        
        # Register slash commands
        await self.slash_commands.register_commands()
    
    async def on_message(self, message):
        """Handle incoming Discord messages with NO rate limiting"""
        # Skip our own messages
        if message.author == self.user:
            return
        
        try:
            # Process ALL messages immediately without any rate limiting
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
        try:
            await interaction.response.send_message(f"Error executing command: {str(error)}", ephemeral=True)
            logger.error(f"Slash command error: {error}")
        except Exception as e:
            logger.error(f"Error handling command error: {e}")
    
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
                
            # Collect all server emojis the bot has access to
            server_emojis = {}
            for guild in self.guilds:
                for emoji in guild.emojis:
                    server_emojis[emoji.name] = str(emoji)
            
            # Log available emojis
            logger.info(f"Collected {len(server_emojis)} custom emojis from {len(self.guilds)} servers")
            await self.command_handler.music_manager.start_inactivity_checker()
            # Store emoji info in your AI handler
            self.ai_handler.server_emojis = list(server_emojis.keys())
            
            # Also pass to message processor if needed
            if hasattr(self, 'message_processor'):
                self.message_processor.server_emojis = server_emojis
                
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
            
            # Send a friendly error message
            await ctx.channel.send("damn, something went wrong with that command")
            
        except Exception as e:
            logger.error(f"Error handling command error: {e}")
    
    async def process_message(self, message):
        """Process a message through the message processor"""
        # This method is incomplete and causing errors
        # Simply delegate to the message processor which has the correct logic
        try:
            user_id = str(message.author.id)
            # Use the proper message processor instead of trying to handle directly
            await self.message_processor.process_message(message)
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            # Fallback to a simple response if everything fails
            await message.channel.send("yo my brain just froze for a sec, try again?")

    async def on_error(self, event, *args, **kwargs):
        """Global error handler for all events"""
        error_type, error, tb = sys.exc_info()
        
        # Generate error ID for tracking
        error_id = f"ERR-{int(time.time())}"
        
        logger.error(f"Error ID {error_id} | Unhandled exception in {event}: {error_type.__name__}: {error}")
        logger.error("".join(traceback.format_tb(tb)))
        
        # Try to notify channel if possible
        if event == 'on_message' and len(args) > 0:
            message = args[0]
            try:
                await message.channel.send(f"Encountered an unexpected error (ID: {error_id}). The issue has been logged.")
            except:
                # If we can't send to the channel, just log it
                pass

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

def shutdown_bot():
    """Gracefully shut down the bot and all its components"""
    logger.info("Initiating bot shutdown sequence")
    
    # Get active bot instances
    from asyncio import all_tasks, current_task, get_event_loop
    
    try:
        # Try to close any bot instances
        for task in all_tasks():
            if task != current_task() and task.get_name() == "ChronoChunk Bot Task":
                logger.info("Found bot task, scheduling shutdown")
                task.cancel()
                
        # Signal event loop to stop
        loop = get_event_loop()
        if loop.is_running():
            logger.info("Stopping event loop")
            loop.stop()
    except Exception as e:
        logger.error(f"Error during shutdown sequence: {e}")

def main() -> None: 
    """Start up the bot"""
    try:
        bot = ChronoChunk()
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
        raise

if __name__ == "__main__":
    main()