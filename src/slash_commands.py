import logging
import discord
from discord import app_commands
from typing import Dict, Any, List, Optional, Callable, Awaitable

# Setup logging
logger = logging.getLogger(__name__)

class SlashCommandManager:
    """Handles registration and execution of slash commands"""
    
    def __init__(self, bot, game_manager=None, user_data_manager=None, 
                 ai_handler=None, message_handler=None):
        """Initialize WITHOUT rate limiter"""
        self.bot = bot
        self.game_manager = game_manager
        self.user_data_manager = user_data_manager
        self.ai_handler = ai_handler
        self.message_handler = message_handler
        logger.info("Slash command manager initialized without rate limiting")
    
    async def register_commands(self):
        """Register all slash commands with Discord"""
        try:
            # Clear existing commands
            self.bot.tree.clear_commands(guild=None)
            
            # Register all commands
            await self._register_good_boy_command()
            await self._register_info_command()
            await self._register_mydata_command()
            await self._register_game_command()
            await self._register_guess_command()
            await self._register_end_command()
            await self._register_forget_command()
            await self._register_code_command()
            await self._register_chat_command()
            
            # Sync the commands with Discord
            await self.bot.tree.sync()
            logger.info("Slash commands registered!")
        
        except Exception as e:
            logger.error(f"Error registering slash commands: {e}")
    
    async def _register_good_boy_command(self):
        """Register the good-boy command"""
        @self.bot.tree.command(name="good-boy", description="Get a smiley face")
        async def good_boy(interaction: discord.Interaction):
            await interaction.response.send_message(":)")
    
    async def _register_info_command(self):
        """Register the info command"""
        @self.bot.tree.command(name="info", description="See what information the bot has about you")
        async def info(interaction: discord.Interaction):
            # Get user data
            user_id = str(interaction.user.id)
            username = interaction.user.display_name
            
            try:
                # Get user summary directly
                summary = await self.user_data_manager.get_user_summary(user_id, username)
                
                # If the summary is empty or minimal, provide a friendly message
                if "I don't have any information" in summary or len(summary.strip().split('\n')) <= 3:
                    await interaction.response.send_message("damn, i don't know much about you yet. hit me up with some convos so i can learn more about you!")
                else:
                    await interaction.response.send_message(summary)
            except Exception as e:
                logger.error(f"Error handling info command: {e}")
                await interaction.response.send_message("shit, couldn't get your data right now")
    
    async def _register_mydata_command(self):
        """Register the mydata command (alias for info)"""
        @self.bot.tree.command(name="mydata", description="See what information the bot has about you (alias for /info)")
        async def mydata(interaction: discord.Interaction):
            # Reuse the info command functionality
            user_id = str(interaction.user.id)
            username = interaction.user.display_name
            
            try:
                # Get user summary directly
                summary = await self.user_data_manager.get_user_summary(user_id, username)
                
                # If the summary is empty or minimal, provide a friendly message
                if "I don't have any information" in summary or len(summary.strip().split('\n')) <= 3:
                    await interaction.response.send_message("damn, i don't know much about you yet. hit me up with some convos so i can learn more about you!")
                else:
                    await interaction.response.send_message(summary)
            except Exception as e:
                logger.error(f"Error handling mydata command: {e}")
                await interaction.response.send_message("shit, couldn't get your data right now")
    
    async def _register_game_command(self):
        """Register the game command"""
        @self.bot.tree.command(name="game", description="Start a number guessing game")
        @app_commands.describe(max_range="Maximum number for the guessing game (default: 100)")
        async def game(interaction: discord.Interaction, max_range: int = 100):
            user_id = str(interaction.user.id)
            
            try:
                # Convert user_id to int as expected by the GameManager
                user_id_int = int(user_id) if user_id.isdigit() else 0
                success, response = self.game_manager.start_game(user_id_int, max_range)
                await interaction.response.send_message(response)
            except Exception as e:
                logger.error(f"Error handling game command: {e}")
                await interaction.response.send_message("Couldn't start the game right now")
    
    async def _register_guess_command(self):
        """Register the guess command"""
        @self.bot.tree.command(name="guess", description="Make a guess in the current game")
        @app_commands.describe(number="Your guess")
        async def guess(interaction: discord.Interaction, number: int):
            user_id = str(interaction.user.id)
            
            try:
                # Convert user_id to int as expected by the GameManager
                user_id_int = int(user_id) if user_id.isdigit() else 0
                success, response = self.game_manager.make_guess(user_id_int, number)
                await interaction.response.send_message(response)
            except Exception as e:
                logger.error(f"Error handling guess command: {e}")
                await interaction.response.send_message("Couldn't process your guess right now")
    
    async def _register_end_command(self):
        """Register the end command"""
        @self.bot.tree.command(name="end", description="End the current game")
        async def end(interaction: discord.Interaction):
            user_id = str(interaction.user.id)
            
            try:
                # Convert user_id to int as expected by the GameManager
                user_id_int = int(user_id) if user_id.isdigit() else 0
                success, response = self.game_manager.end_game(user_id_int)
                await interaction.response.send_message(response)
            except Exception as e:
                logger.error(f"Error handling end command: {e}")
                await interaction.response.send_message("Couldn't end the game right now")
    
    async def _register_forget_command(self):
        """Register the forget command"""
        @self.bot.tree.command(name="forget", description="Forget specific information about you")
        @app_commands.describe(fact="The fact to forget (leave empty to forget everything)")
        async def forget(interaction: discord.Interaction, fact: str = None):
            user_id = str(interaction.user.id)
            username = interaction.user.display_name
            
            try:
                if not fact:
                    # Clear all user data
                    new_data = self.user_data_manager.load_user_data(user_id, username)
                    new_data["facts"] = []
                    new_data["topics_of_interest"] = []
                    self.user_data_manager.save_user_data(user_id, new_data)
                    await interaction.response.send_message("bet, wiped all your data. fresh start 💀")
                else:
                    # Try to remove specific fact
                    result = self.user_data_manager.remove_fact(user_id, fact, username)
                    if result:
                        await interaction.response.send_message("bet, forgot that shit 👍")
                    else:
                        await interaction.response.send_message("couldn't find anything about that to forget, try different words?")
            except Exception as e:
                logger.error(f"Error handling forget command: {e}")
                await interaction.response.send_message("damn, couldn't clear your data rn")
    
    async def _register_code_command(self):
        """Register the code command"""
        @self.bot.tree.command(name="code", description="Get a link to the bot's source code")
        async def code(interaction: discord.Interaction):
            await interaction.response.send_message("check out my code here: https://github.com/Mykal-Steele/ChronoChunk")
    
    async def _register_chat_command(self):
        """Register the chat command"""
        @self.bot.tree.command(name="chat", description="Chat with ChronoChunk")
        @app_commands.describe(message="What you want to say to ChronoChunk")
        async def chat(interaction: discord.Interaction, message: str):
            user_id = str(interaction.user.id)
            username = interaction.user.display_name
            channel_id = str(interaction.channel_id)
            
            try:
                # Defer the response to give us time to process
                await interaction.response.defer(thinking=True)
                
                # Get user data for context
                user_data = self.user_data_manager.load_user_data(user_id, username)
                
                # Build context
                conversation_history = await self.message_handler.build_conversation_context(channel_id, user_data, False)
                
                # Process through AI
                ai_response = await self.ai_handler.generate_response(message, conversation_history, username)
                
                # Update channel history with user message
                self.message_handler.update_channel_history(
                    channel_id=channel_id,
                    user_id=user_id,
                    username=username,
                    content=message,
                    is_bot=False
                )
                
                # Update channel history with bot response
                self.message_handler.update_channel_history(
                    channel_id=channel_id,
                    user_id=str(self.bot.user.id),
                    username="ChronoChunk",
                    content=ai_response,
                    is_bot=True
                )
                
                # Update conversation memory
                self.message_handler.update_conversation_memory(
                    channel_id=channel_id,
                    username=username,
                    user_message=message,
                    bot_response=ai_response
                )
                
                # Save to user data
                await self.user_data_manager.add_conversation(user_id, message, ai_response, username)
                
                # Extract facts
                await self.user_data_manager.extract_and_save_facts(user_id, message, username)
                
                # Send the response
                await interaction.followup.send(ai_response)
                
            except Exception as e:
                logger.error(f"Error handling chat command: {e}")
                await interaction.followup.send("damn, something went wrong with the AI. try again?")