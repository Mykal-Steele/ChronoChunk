import logging
import discord
from discord import app_commands
from typing import Dict, Any, List, Optional, Callable, Awaitable

# Setup logging
logger = logging.getLogger(__name__)

class SlashCommandManager:
    """Handles registration and execution of slash commands"""
    
    def __init__(self, bot, game_manager=None, user_data_manager=None, 
                 ai_handler=None, message_handler=None, music_manager=None):
        """Initialize WITHOUT rate limiter"""
        self.bot = bot
        self.game_manager = game_manager
        self.user_data_manager = user_data_manager
        self.ai_handler = ai_handler
        self.message_handler = message_handler
        
        # Initialize music manager
        if music_manager is None:
            from src.music_manager import MusicManager
            self.music_manager = MusicManager()
        else:
            self.music_manager = music_manager
            
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
            await self._register_help_command()  # Make sure this line is here
            
            # Register music commands
            await self._register_music_command()
            await self._register_skip_command()
            await self._register_pause_command()
            await self._register_resume_command()
            await self._register_stop_command()
            await self._register_queue_command()
            await self._register_volume_command()
            await self._register_relate_command()
            
            # Register test audio command
            await self._register_test_audio_command()
            
            # Sync the commands with Discord - force sync to update
            await self.bot.tree.sync()
            
            logger.info("Slash commands registered!")
        
        except Exception as e:
            logger.error(f"Error registering slash commands: {e}")
            import traceback
            traceback.print_exc()  # Add this to see detailed error information
    
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
    
    async def _register_music_command(self):
        """Register the music command"""
        @self.bot.tree.command(name="music", description="Play music from YouTube or Spotify")
        @app_commands.describe(query="YouTube/Spotify URL or search term")
        async def music(interaction: discord.Interaction, query: str):
            if not interaction.user.voice:
                await interaction.response.send_message("yo, u gotta be in a voice channel to play music")
                return
            
            # Join the user's voice channel
            voice_channel = interaction.user.voice.channel
            
            try:
                # Defer response for longer processing
                await interaction.response.defer(thinking=True)
                
                # Join the voice channel
                await self.music_manager.join_voice_channel(voice_channel)
                
                # Play the music
                success, response = await self.music_manager.play(
                    interaction.guild_id, 
                    query, 
                    interaction.user.display_name
                )
                
                await interaction.followup.send(response)
                
            except Exception as e:
                logger.error(f"Error in music command: {e}")
                await interaction.followup.send("damn, something went wrong tryna play that")

    async def _register_skip_command(self):
        """Register the skip command"""
        @self.bot.tree.command(name="skip", description="Skip to the next song in queue")
        async def skip(interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message("this only works in servers not dms")
                return
                
            success, response = await self.music_manager.skip(interaction.guild_id)
            await interaction.response.send_message(response)

    async def _register_pause_command(self):
        """Register the pause command"""
        @self.bot.tree.command(name="pause", description="Pause the current song")
        async def pause(interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message("this only works in servers not dms")
                return
                
            success, response = await self.music_manager.pause(interaction.guild_id)
            await interaction.response.send_message(response)

    async def _register_resume_command(self):
        """Register the resume command"""
        @self.bot.tree.command(name="resume", description="Resume playback")
        async def resume(interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message("this only works in servers not dms")
                return
                
            success, response = await self.music_manager.resume(interaction.guild_id)
            await interaction.response.send_message(response)

    async def _register_stop_command(self):
        """Register the stop command"""
        @self.bot.tree.command(name="stop", description="Stop playback and clear the queue")
        async def stop(interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message("this only works in servers not dms")
                return
            
            # Clear the queue
            self.music_manager.clear_queue(interaction.guild_id)
            
            # Leave the voice channel
            success = await self.music_manager.leave_voice_channel(interaction.guild_id)
            
            await interaction.response.send_message(
                "aight, stopped the music n left the channel" if success else "im not even playing anything rn"
            )

    async def _register_queue_command(self):
        """Register the queue command"""
        @self.bot.tree.command(name="queue", description="Show the current queue")
        async def queue(interaction: discord.Interaction):
            if not interaction.guild:
                await interaction.response.send_message("this only works in servers not dms")
                return
                
            # Get the current song
            current_song = self.music_manager.get_current_song(interaction.guild_id)
            
            # Get the queue
            queue = self.music_manager.get_queue(interaction.guild_id)
            
            if not current_song and not queue:
                await interaction.response.send_message("queue empty af, add something with /music")
                return
            
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
            
            await interaction.response.send_message("\n".join(response))

    async def _register_volume_command(self):
        """Register the volume command"""
        @self.bot.tree.command(name="volume", description="Set the volume (0-100)")
        @app_commands.describe(level="Volume level (0-100)")
        async def volume(interaction: discord.Interaction, level: int):
            if not interaction.guild:
                await interaction.response.send_message("this only works in servers not dms")
                return
                
            try:
                volume = max(0, min(100, level)) / 100.0  # Clamp to 0-100 and convert to 0.0-1.0
                success, response = await self.music_manager.set_volume(interaction.guild_id, volume)
                await interaction.response.send_message(response)
            except Exception as e:
                logger.error(f"Error in volume command: {e}")
                await interaction.response.send_message("couldn't change the volume, something broke")
    
    async def _register_test_audio_command(self):
        """Register a test command to check audio functionality"""
        @self.bot.tree.command(name="testaudio", description="Test if audio playback is working")
        async def testaudio(interaction: discord.Interaction):
            if not interaction.user.voice:
                await interaction.response.send_message("You need to be in a voice channel to test audio")
                return
                
            voice_channel = interaction.user.voice.channel
            await interaction.response.defer(thinking=True)
                
            try:
                # Join voice channel
                voice_client = await voice_channel.connect() if not interaction.guild.voice_client else interaction.guild.voice_client
                    
                # Play a simple audio test
                audio_source = discord.FFmpegPCMAudio(
                    "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",  # Test audio URL
                    executable=self.music_manager.ffmpeg_path
                )
                    
                if voice_client.is_playing():
                    voice_client.stop()
                    
                voice_client.play(audio_source)
                await interaction.followup.send("Playing test audio... If you can hear it, your audio setup is working!")
                    
            except Exception as e:
                await interaction.followup.send(f"Audio test failed: {str(e)}")
                import traceback
                traceback.print_exc()

    async def _register_help_command(self):
        """Register the help command"""
        @self.bot.tree.command(name="help", description="Show all available commands and how to use them")
        async def help(interaction: discord.Interaction):
            # Create an embed for a nicer-looking help menu
            embed = discord.Embed(
                title="ChronoChunk Bot Commands",
                description="here's all the shit i can do, my g:",
                color=0x9B59B6  # Purple color
            )
            
            # General commands section
            general_cmds = [
                "`/chat <message>` - talk with me directly",
                "`/info` - see what i know about you",
                "`/mydata` - same as /info, shows what i remember",
                "`/forget <info>` - make me forget specific info (empty to wipe all)",
                "`/code` - get link to my source code"
            ]
            embed.add_field(name="💬 general commands", value="\n".join(general_cmds), inline=False)
            
            # Game commands section
            game_cmds = [
                "`/game <max>` - start a number guessing game (1 to max, default 100)",
                "`/guess <number>` - make a guess in the game",
                "`/end` - end the current game"
            ]
            embed.add_field(name="🎮 game commands", value="\n".join(game_cmds), inline=False)
            
            # Music commands section
            music_cmds = [
                "`/music <query>` - play music from YouTube/Spotify. If you type a natural language query instead of a link, the AI will read your response and determine what music you're looking for.",
                "`/skip` - skip to next song",
                "`/pause` - pause current playback",
                "`/resume` - resume playback",
                "`/stop` - stop music and leave voice channel",
                "`/queue` - show current song queue",
                "`/volume <0-100>` - adjust volume",
                "`/relate` - play a song related to the current one based on YouTube recommendations"
            ]
            embed.add_field(name="🎵 music commands", value="\n".join(music_cmds), inline=False)
            
            # Fun commands section
            fun_cmds = [
                "`/good-boy` - get a smiley face :)"
            ]
            embed.add_field(name="😂 other shit", value="\n".join(fun_cmds), inline=False)
            
            # Add a footer with extra info
            embed.set_footer(text="u can also just chat with me normally in any channel by replying to the bot message, no commands needed")
            
            await interaction.response.send_message(embed=embed)

    async def _register_relate_command(self):
        """Register the relate command"""
        @self.bot.tree.command(name="relate", description="Play a song related to the current one based on YouTube recommendations")
        async def relate(interaction: discord.Interaction):
            if not interaction.user.voice:
                await interaction.response.send_message("yo, u gotta be in a voice channel to play related music")
                return
            
            try:
                # Defer response for longer processing
                await interaction.response.defer(thinking=True)
                
                # Get a related song
                success, response = await self.music_manager.get_related_song(
                    interaction.guild_id,
                    interaction.user.display_name
                )
                
                await interaction.followup.send(response)
                
            except Exception as e:
                logger.error(f"Error in relate command: {e}")
                await interaction.followup.send("damn, something went wrong trying to get a related song")