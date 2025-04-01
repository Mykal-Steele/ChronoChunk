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
    
    def __init__(self, bot=None, game_manager: GameManager = None, user_data_manager: UserDataManager = None, 
                rate_limiter: RateLimiter = None, intent_detector: IntentDetector = None, 
                music_manager=None):
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
        
        # Import MusicManager here to avoid circular imports
        if music_manager is None:
            from src.music_manager import MusicManager
            self.music_manager = MusicManager()
        else:
            self.music_manager = music_manager
        
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
            # Add music commands
            "music": self._handle_music,
            "skip": self._handle_skip,
            "pause": self._handle_pause,
            "resume": self._handle_resume,
            "stop": self._handle_stop,
            "queue": self._handle_queue,
            "volume": self._handle_volume,
            "relate": self._handle_relate,
        }
        
    async def handle_command(self, command: str, args: str, message: discord.Message, user_id: str) -> str:
        """Handle commands using exact command matching only"""
        
        if command == "forget":
            return await self._handle_forget_command(args, message, user_id)
        elif command == "remember":
            return await self._handle_remember_command(args, message, user_id)
        elif command == "info":
            return await self._handle_info_command(args, message, user_id)
        elif command == "game":
            return await self._handle_game_command(args, message, user_id)
        # Add all your other command handlers
        
        # Default - unknown command
        return "Command not recognized. Try '/help' for a list of commands."

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

    async def _handle_music(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """Play music from a YouTube or Spotify URL"""
        if not message.author.voice:
            return "yo, u gotta be in a voice channel to play music"
            
        if not args:
            return "gimme a link or search term, like '/music https://youtube.com/...' or '/music lofi beats'"
        
        # Join the user's voice channel
        voice_channel = message.author.voice.channel
        
        try:
            # Join the voice channel
            await self.music_manager.join_voice_channel(voice_channel)
            
            # Get the URL or search term
            query = " ".join(args)
            
            # Play the music
            success, response = await self.music_manager.play(
                message.guild.id, 
                query, 
                message.author.display_name
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Error in music command: {e}")
            return "damn, something went wrong tryna play that"
    
    async def _handle_skip(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """Skip to the next song in queue"""
        if not message.guild:
            return "this only works in servers not dms"
            
        success, response = await self.music_manager.skip(message.guild.id)
        return response
    
    async def _handle_pause(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """Pause the current song"""
        if not message.guild:
            return "this only works in servers not dms"
            
        success, response = await self.music_manager.pause(message.guild.id)
        return response
    
    async def _handle_resume(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """Resume playback"""
        if not message.guild:
            return "this only works in servers not dms"
            
        success, response = await self.music_manager.resume(message.guild.id)
        return response
    
    async def _handle_stop(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """Stop playback and clear the queue"""
        if not message.guild:
            return "this only works in servers not dms"
        
        # Clear the queue
        self.music_manager.clear_queue(message.guild.id)
        
        # Leave the voice channel
        success = await self.music_manager.leave_voice_channel(message.guild.id)
        return "aight, stopped the music n left the channel" if success else "im not even playing anything rn"
    
    async def _handle_queue(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """Show the current queue"""
        if not message.guild:
            return "this only works in servers not dms"
            
        # Get the current song
        current_song = self.music_manager.get_current_song(message.guild.id)
        
        # Get the queue
        queue = self.music_manager.get_queue(message.guild.id)
        
        if not current_song and not queue:
            return "queue empty af, add something with /music"
        
        response = []
        if current_song:
            response.append(f"**Now Playing:** {current_song}")
            
        if queue:
            response.append("\n**Up Next:**")
            for i, song in enumerate(queue, 1):
                if i > 10:  # Limit to 10 songs
                    remaining = len(queue) - 10
                    response.append(f"*...and {remaining} more*")
                    break
                response.append(f"{i}. {song}")
        
        return "\n".join(response)
    
    async def _handle_volume(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """Set the volume (0-100)"""
        if not message.guild:
            return "this only works in servers not dms"
            
        if not args:
            return "gimme a number between 0 and 100"
            
        try:
            volume = float(args[0]) / 100.0  # Convert to 0.0-1.0 range
            success, response = await self.music_manager.set_volume(message.guild.id, volume)
            return response
        except ValueError:
            return "yo that aint a number, try again"

    async def _handle_relate(self, args: List[str], message: discord.Message, user_id: str) -> str:
        """Play a related song based on YouTube recommendations"""
        if not message.guild:
            return "this only works in servers not dms"
            
        if not message.author.voice:
            return "yo, u gotta be in a voice channel to play related music"
        
        success, response = await self.music_manager.get_related_song(
            message.guild.id, 
            message.author.display_name
        )
        
        return response