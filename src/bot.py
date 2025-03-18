import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import google.generativeai as genai
import logging
import os
import sys
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
        """Initialize the bot with all required components"""
        # Init Discord stuff with needed perms
        intents = discord.Intents.default()
        intents.message_content = True  # Need to read messages
        intents.members = True  # Need to see server members
        super().__init__(command_prefix='/', intents=intents)
        
        # Override default error handling to suppress CommandNotFound errors
        self._old_on_command_error = self.on_command_error
        self.on_command_error = self._custom_on_command_error
        
        # Initialize core components
        self.rate_limiter = RateLimiter()
        self.game_manager = GameManager()
        self.intent_detector = IntentDetector()
        self.user_data_manager = UserDataManager()
        
        # Initialize refactored components
        self.ai_handler = AIResponseHandler(
            api_key=GEMINI_API_KEY,
            important_topics=Config.IMPORTANT_TOPICS,
            intent_detector=self.intent_detector
        )
        
        self.message_handler = MessageHandler()
        
        self.command_handler = CommandHandler(
            bot=self,
            game_manager=self.game_manager,
            user_data_manager=self.user_data_manager,
            rate_limiter=self.rate_limiter,
            intent_detector=self.intent_detector
        )
        
        self.web_server = WebServer()
        
        self.message_processor = MessageProcessor(
            bot=self,
            command_handler=self.command_handler,
            rate_limiter=self.rate_limiter,
            user_data_manager=self.user_data_manager,
            intent_detector=self.intent_detector,
            message_handler=self.message_handler,
            ai_handler=self.ai_handler
        )
        
        logger.info("Bot initialized")
    
    async def _custom_on_command_error(self, context, exception):
        """Custom error handler that suppresses CommandNotFound errors"""
        if not isinstance(exception, CommandNotFound):
            # For any other exception, use the original handler
            await self._old_on_command_error(context, exception)
        
    async def setup_hook(self) -> None:
        """Set up things that need to run before bot starts"""
        try:
            # Start the web server
            try:
                await self.web_server.start()
            except Exception as e:
                # More graceful handling of web server errors
                logger.warning(f"Web server couldn't start (may be already running): {e}")
            
            # Initialize slash commands
            self.slash_commands = SlashCommandManager(
                bot=self,
                game_manager=self.game_manager,
                user_data_manager=self.user_data_manager,
                rate_limiter=self.rate_limiter,
                ai_handler=self.ai_handler,
                message_handler=self.message_handler
            )
            
            # Register slash commands
            await self.slash_commands.register_commands()
            
        except Exception as e:
            logger.error(f"Error in setup_hook: {e}")
    
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
    
    async def on_message(self, message: discord.Message) -> None:
        """Handle incoming messages"""
        await self.message_processor.process_message(message)

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