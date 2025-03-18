import discord
import logging
import os
import sys
from typing import List, Optional, Any, Dict

# Try different import approaches to handle both direct and relative imports
try:
    from src.exceptions import RateLimitError
    from src.game_manager import GameManager
    from src.user_data_manager import UserDataManager
    from src.rate_limiter import RateLimiter
    from src.intent_detector import IntentDetector
    from src.logger import logger
except ImportError:
    # Add parent directory to path and try again
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from src.exceptions import RateLimitError
    from src.game_manager import GameManager
    from src.user_data_manager import UserDataManager
    from src.rate_limiter import RateLimiter
    from src.intent_detector import IntentDetector
    from src.logger import logger

class CommandHandler:
    """handles all the bot commands"""
    
    def __init__(self, bot=None, game_manager: GameManager = None, user_data_manager: UserDataManager = None, rate_limiter: RateLimiter = None, intent_detector: IntentDetector = None):
        """set up the command handler"""
        self.bot = bot  # Can be None for standalone usage
        
        # Initialize required components, creating them if not provided
        if game_manager is None:
            logger.warning("No GameManager provided, creating a new one")
            self.game_manager = GameManager()
        else:
            self.game_manager = game_manager
            
        self.user_data_manager = user_data_manager if user_data_manager else UserDataManager()
        self.rate_limiter = rate_limiter if rate_limiter else RateLimiter()
        self.intent_detector = intent_detector if intent_detector else IntentDetector()
        
        # we're supporting multiple command prefixes to make the bot flexible
        # users can trigger commands with / (slash commands) or ! (traditional prefix)
        # this makes it easier for users coming from different bot ecosystems
        self.command_prefixes = ["/", "!"]
        
        # mapping command text to handler methods - this way we can easily add new commands
        # without giant if-else blocks that get messy real quick
        self.command_handlers = {
            "chat": self._handle_chat,
            "game": self._handle_game,
            "info": self._handle_info,
            "forget": self._handle_forget,
            "mydata": self._handle_my_data,
            "end": self._handle_end,
            "guess": self._handle_guess,
            "code": self._handle_code,
            "good-boy": self._handle_good_boy,
        }
        
    async def handle_command(self, command: str, args: List[str], message: discord.Message, user_id: str) -> Optional[str]:
        """process a command and return a response"""
        # First check if we have an exact command match
        if command in self.command_handlers:
            # Check rate limit first
            try:
                self.rate_limiter.check_rate_limit(user_id, command)
            except RateLimitError as e:
                return str(e)
                
            # Call the command handler
            return await self.command_handlers[command](args, message, user_id)
        
        # If no exact match, check for intent
        # Handle user info intent
        if await self.intent_detector.detect_user_info_intent(message.content):
            try:
                self.rate_limiter.check_rate_limit(user_id, "mydata")
            except RateLimitError as e:
                return str(e)
            return await self._handle_my_data(args, message, user_id)
            
        # Handle forget intent
        is_forget, target = await self.intent_detector.detect_forget_intent(message.content)
        if is_forget:
            try:
                self.rate_limiter.check_rate_limit(user_id, "forget")
            except RateLimitError as e:
                return str(e)
            # Pass what needs to be forgotten as arg
            return await self._handle_forget([target] if target else args, message, user_id)
            
        # Handle game start intent
        if await self.intent_detector.detect_game_intent(message.content):
            try:
                self.rate_limiter.check_rate_limit(user_id, "game")
            except RateLimitError as e:
                return str(e)
            return await self._handle_game(args, message, user_id)
            
        # Handle game end intent
        if await self.intent_detector.detect_end_game_intent(message.content):
            try:
                self.rate_limiter.check_rate_limit(user_id, "end")
            except RateLimitError as e:
                return str(e)
            return await self._handle_end(args, message, user_id)
            
        # Handle guess
        guess_value = await self.intent_detector.extract_guess_value(message.content)
        if guess_value is not None:
            try:
                self.rate_limiter.check_rate_limit(user_id, "guess")
            except RateLimitError as e:
                return str(e)
            return await self._handle_guess([str(guess_value)], message, user_id)
        
        # No matching intent found
        return None
    
    async def _handle_my_data(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """show user what data we have about them"""
        try:
            # Get username from message
            username = message.author.display_name if message and hasattr(message, 'author') else None
            summary = await self.user_data_manager.get_user_summary(user_id, username)
            
            # If the summary is empty or minimal, provide a friendly message
            if "I don't have any information" in summary or len(summary.strip().split('\n')) <= 3:
                return "damn, i don't know much about you yet. hit me up with some convos so i can learn more about you!"
                
            return summary
        except Exception as e:
            logger.error(f"Error handling mydata: {e}")
            return "shit, couldn't get your data right now"
            
    async def _handle_forget(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """delete user data based on input"""
        # Get username from message
        username = message.author.display_name if message and hasattr(message, 'author') else None
        
        if not args:
            # Check if this is actually a correction using AI
            if await self.intent_detector.detect_correction_intent(message.content):
                # Handle as correction
                result = await self.user_data_manager.handle_correction(user_id, message.content, username)
                if result:
                    return "aight, fixed that shit for you"
                else:
                    return "couldn't figure out what to fix, be more specific?"
            
            # Clear all user data
            try:
                new_data = self.user_data_manager.load_user_data(user_id, username)
                new_data["facts"] = []
                new_data["topics_of_interest"] = []
                self.user_data_manager.save_user_data(user_id, new_data)
                return "bet, wiped all your data. fresh start 💀"
            except Exception as e:
                logger.error(f"Error clearing user data: {e}")
                return "damn, couldn't clear your data rn"
                
        else:
            # Extract the thing to forget from args
            fact_to_remove = " ".join(args)
            
            # Try to remove it
            result = self.user_data_manager.remove_fact(user_id, fact_to_remove, username)
            
            if result:
                return "bet, forgot that shit 👍"
            else:
                return "couldn't find anything about that to forget, try different words?"
                
    async def _handle_game(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """start a guessing game"""
        max_range = 100  # default
        
        if args:
            try:
                max_range = int(args[0])
            except ValueError:
                return "bro that's not a number 💀 try again"
        
        # Convert user_id to int as expected by the GameManager
        user_id_int = int(user_id) if user_id.isdigit() else 0
        success, response = self.game_manager.start_game(user_id_int, max_range)
        return response
        
    async def _handle_end(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """end the current game"""
        # Convert user_id to int as expected by the GameManager
        user_id_int = int(user_id) if user_id.isdigit() else 0
        success, response = self.game_manager.end_game(user_id_int)
        return response
        
    async def _handle_guess(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """process a guess for the game"""
        try:
            # Try to extract the guess value with AI first
            guess = await self.intent_detector.extract_guess_value(message.content)
            
            # If AI couldn't extract it, try direct number parsing
            if guess is None and args:
                try:
                    guess = int(args[0])
                except ValueError:
                    pass
                    
            if guess is None:
                return "yo, i need a number to guess! try something like '/guess 40' or just '/40'"
            
            # Convert user_id to int as expected by the GameManager
            user_id_int = int(user_id) if user_id.isdigit() else 0
            success, response = self.game_manager.make_guess(user_id_int, guess)
            return response
            
        except ValueError:
            return "that ain't a number bro, try again"
            
    async def _handle_code(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """show github link to bot code"""
        return "check out my code here: https://github.com/Mykal-Steele/ChronoChunk"

    async def _handle_chat(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """Process a general chat command by forwarding to the bot's AI"""
        if not self.bot:
            return "Can't chat right now - bot connection unavailable"
            
        # Join the arguments to form the complete query
        query = " ".join(args) if args else ""
        
        try:
            # Get username for personalization
            username = message.author.display_name if message and hasattr(message, 'author') else None
            
            # Get user data for context
            user_data = self.user_data_manager.load_user_data(user_id, username)
            
            # If the bot has the necessary method to process chat
            if hasattr(self.bot, 'build_conversation_context') and hasattr(self.bot, 'process_ai_message'):
                # Build context from past conversations
                conversation_history = await self.bot.build_conversation_context(str(message.channel.id), user_data)
                
                # Process the AI message
                await self.bot.process_ai_message(message, query, conversation_history)
                
                # Return None to indicate that the bot is handling the response directly
                return None
            else:
                return "The bot doesn't support AI chat features yet."
        except Exception as e:
            logger.error(f"Error handling chat: {e}")
            return "Couldn't process that chat request right now."

    async def _handle_info(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """Alias for _handle_my_data - shows user what data we have about them"""
        return await self._handle_my_data(args, message, user_id)
        
    async def _handle_good_boy(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """Simple command that responds with a smiley face"""
        return ":)" 