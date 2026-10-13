import io
import logging
import discord
from discord import app_commands
from typing import Optional
from src import usage_context
from src.ai_response_handler import AIResponseHandler
from src.exceptions import RateLimitError
from src.game_manager import GameManager
from src.help_menu import build_help_embed
from src.channel_settings import get_channel_settings
from src.conversation import Conversation
from src.message_context import gather_context, resolve_message_link
from src.message_handler import MessageHandler
from src.message_processor import MessageProcessor, NO_PINGS, give_first_use_role
from src.server_emoji import NO_EMOJI, QUIET_REPLIES, asked_about_emoji, render, usable_emoji, used_custom_emoji
from src.stats import stats_summary
from src.tools.courses import courses_summary
from src.timing import Stopwatch
from src.user_data_manager import UserDataManager

# Setup logging
logger = logging.getLogger(__name__)

class SlashCommandManager:
    """Handles registration and execution of slash commands"""
    
    def __init__(self, bot, *, game_manager: GameManager, user_data_manager: UserDataManager,
                 ai_handler: AIResponseHandler, message_handler: MessageHandler, music_manager=None):
        """The rate limiter is the bot's shared one, read from bot.rate_limiter when a command runs."""
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
            await self._register_tldr_command()
            await self._register_usage_command()
            await self._register_model_command()
            await self._register_setup_command()
            await self._register_stats_command()
            await self._register_courses_command()
            await self._register_recent_posts_command()
            await self._register_join_in_command()
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
            
            # Sync the commands with Discord - force sync to update
            await self.bot.tree.sync()
            
            logger.info("Slash commands registered!")
        
        except Exception as e:
            logger.error(f"Error registering slash commands: {e}")
            logger.exception("Slash command registration failed")
    
    async def _register_good_boy_command(self):
        """Register the good-boy command"""
        @self.bot.tree.command(name="good-boy", description="Get a smiley face")
        async def good_boy(interaction: discord.Interaction):
            await interaction.response.send_message(":)")
    
    async def _send_user_summary(self, interaction: discord.Interaction) -> None:
        """Answer /info and /mydata. Only the person who asked sees it, because it lists personal facts."""
        try:
            summary = self.user_data_manager.get_user_summary(str(interaction.user.id), interaction.user.display_name)
            if "I don't have any information" in summary:
                summary = "i dont know much about u yet. talk to me a bit and ill pick things up"
            await interaction.response.send_message(summary, ephemeral=True)
        except Exception as e:
            logger.error(f"Error showing user data: {e}")
            await interaction.response.send_message("couldnt get ur data right now, try again in a bit", ephemeral=True)

    async def _register_info_command(self):
        """Register the info command"""
        @self.bot.tree.command(name="info", description="See what information the bot has about you")
        async def info(interaction: discord.Interaction):
            await self._send_user_summary(interaction)

    async def _register_mydata_command(self):
        """Register the mydata command (alias for info)"""
        @self.bot.tree.command(name="mydata", description="See what information the bot has about you (alias for /info)")
        async def mydata(interaction: discord.Interaction):
            await self._send_user_summary(interaction)

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
        async def forget(interaction: discord.Interaction, fact: Optional[str] = None):
            user_id = str(interaction.user.id)
            username = interaction.user.display_name
            
            try:
                if not fact:
                    # Clear all user data
                    self.user_data_manager.forget_user(user_id)
                    await interaction.response.send_message("bet, wiped all your data. fresh start 💀", ephemeral=True)
                else:
                    # Try to remove specific fact
                    result = self.user_data_manager.remove_fact(user_id, fact, username)
                    if result:
                        await interaction.response.send_message("bet, forgot that 👍", ephemeral=True)
                    else:
                        await interaction.response.send_message("couldnt find anything about that to forget, try different words?", ephemeral=True)
            except Exception as e:
                logger.error(f"Error handling forget command: {e}")
                await interaction.response.send_message("couldnt clear ur data right now, try again in a bit", ephemeral=True)
    
    async def _register_code_command(self):
        """Register the code command"""
        @self.bot.tree.command(name="code", description="Get a link to the bot's source code")
        async def code(interaction: discord.Interaction):
            await interaction.response.send_message("check out my code here: https://github.com/Mykal-Steele/ChronoChunk")
    
    def _usable_emoji(self, interaction: discord.Interaction):
        """The server emoji an answer to this slash command may use."""
        if interaction.channel is None:
            return NO_EMOJI
        return usable_emoji(interaction.channel, self.bot.guilds, via_interaction=True)

    async def _send_followup(self, interaction: discord.Interaction, text: str, files=None,
                             custom_emoji: bool = True, listing: bool = False) -> str:
        """
        Send an answer to a slash command, splitting long text. files are BuiltFile
        objects. Emoji names in the text become server emoji the bot can use here, and
        any other is taken out. listing is for an answer to someone who asked about the
        emoji, which may show several. Returns the text as the chat history should keep it.
        """
        shown = render(text, self._usable_emoji(interaction), custom_allowed=custom_emoji, listing=listing)
        chunks = MessageProcessor._split_text(shown.text) or [""]
        uploads = [discord.File(io.BytesIO(f.data), filename=f.filename) for f in (files or [])]
        if uploads:
            await interaction.followup.send(chunks[0], allowed_mentions=NO_PINGS, files=uploads)
        else:
            await interaction.followup.send(chunks[0], allowed_mentions=NO_PINGS)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, allowed_mentions=NO_PINGS)
        return shown.plain

    async def _register_chat_command(self):
        """Register the chat command"""
        @self.bot.tree.command(name="chat", description="Chat with ChronoChunk")
        @app_commands.describe(
            message="What you want to say to ChronoChunk",
            file="An image (5 MB max), PDF or text file (15 MB max) for ChronoChunk to read"
        )
        async def chat(interaction: discord.Interaction, message: str, file: Optional[discord.Attachment] = None):
            user_id = str(interaction.user.id)
            usage_context.set_user(user_id)
            username = interaction.user.display_name
            channel_id = str(interaction.channel_id)
            rate_limiter = getattr(self.bot, "rate_limiter", None)
            watch = Stopwatch(getattr(interaction, "created_at", None))
            # Held while this answer is written, so answers in one channel come out in order
            held_lock = None
            
            try:
                if rate_limiter:
                    try:
                        rate_limiter.check_rate_limit(user_id, "chat")
                    except RateLimitError as e:
                        minutes = max(1, round(e.retry_after / 60))
                        await interaction.response.send_message(
                            f"slow down, u hit the message limit. try again in about {minutes} min", ephemeral=True
                        )
                        return

                # Defer the response to give us time to process
                await interaction.response.defer(thinking=True)
                # /chat is a call like any other, so the bot may join in afterwards
                conversation = getattr(getattr(self.bot, "message_processor", None), "conversation", None)
                session_start = None
                if isinstance(conversation, Conversation):
                    conversation.called(channel_id, user_id, getattr(interaction, "created_at", None))
                    session_start = conversation.session_start(channel_id)
                
                # Get user data for context
                user_data = self.user_data_manager.load_user_data(user_id, username)
                
                # Build context from what is really in the channel right now
                processor = getattr(self.bot, "message_processor", None)
                recent = []
                if isinstance(processor, MessageProcessor):
                    lock = processor.channel_lock(channel_id)
                    await lock.acquire()
                    held_lock = lock
                with watch.step("history"):
                    if processor and interaction.channel:
                        recent = await processor.sync_channel_history(interaction.channel)
                    conversation_history = self.message_handler.build_conversation_context(
                        channel_id, user_data, query=message, session_start=session_start)

                # Resolve any Discord message links — keep original for clean history storage
                original_message = message
                with watch.step("context"):
                    enriched_message, linked = await resolve_message_link(self.bot, message)

                # Read the attached image or file and a linked message. With neither,
                # what was posted in the channel just before is read, unless that
                # was switched off for this channel.
                if not get_channel_settings().reads_recent_posts(channel_id):
                    recent = []
                # Files made, PDFs read in full, web lookups and course searches each count against a daily limit per user
                file_gate = pdf_gate = web_gate = course_gate = None
                if rate_limiter:
                    file_gate = lambda: rate_limiter.check_rate_limit(user_id, "file")
                    pdf_gate = lambda: rate_limiter.check_rate_limit(user_id, "pdf")
                    web_gate = lambda: rate_limiter.check_rate_limit(user_id, "web")
                    course_gate = lambda: rate_limiter.check_rate_limit(user_id, "course")
                with watch.step("context"):
                    attached = await gather_context([file] if file else [], bot_user_id=self.bot.user.id,
                                                    linked=linked, recent=recent, pdf_gate=pdf_gate)

                # The model always knows the server's emoji. This reply may show one only when
                # none of the bot's last few replies here had one.
                custom_emoji = not any(used_custom_emoji(text) for text in
                                       self.message_handler.recent_bot_replies(channel_id, QUIET_REPLIES))
                emoji = self._usable_emoji(interaction)
                with watch.step("model and tools"):
                    reply = await self.ai_handler.generate_reply(
                        enriched_message, conversation_history, username, user_id,
                        attached_context=attached.text, images=attached.images,
                        allow_files=True, file_gate=file_gate, web_gate=web_gate, course_gate=course_gate,
                        emoji_prompt=emoji.prompt()
                    )
                # The emoji are worked out here too, so the history keeps what is really sent
                listing = asked_about_emoji(message)
                ai_response = render(reply.text, self._usable_emoji(interaction), custom_allowed=custom_emoji,
                                     listing=listing).plain

                # Update channel history with original (clean) user message
                self.message_handler.update_channel_history(
                    channel_id=channel_id,
                    user_id=user_id,
                    username=username,
                    content=original_message,
                    is_bot=False
                )
                
                # Update channel history with bot response
                if not reply.ok:
                    self.message_handler.not_answers.append(ai_response)
                self.message_handler.update_channel_history(
                    channel_id=channel_id,
                    user_id=str(self.bot.user.id),
                    username="ChronoChunk",
                    content=ai_response,
                    is_bot=True,
                    reply_to=username,
                    reply_to_id=user_id
                )

                # The answer goes out first. Saving the conversation also extracts facts,
                # which is another model call they should not have to wait for.
                with watch.step("send"):
                    await self._send_followup(interaction, ai_response, reply.files, custom_emoji=custom_emoji,
                                              listing=listing)
                logger.info(f"Answered {username} in {watch.summary()}")
                # The answer is out, so the next one in this channel may start
                if held_lock is not None:
                    held_lock.release()
                    held_lock = None

                await give_first_use_role(interaction.guild, interaction.user.id)
                try:
                    await self.user_data_manager.add_conversation(user_id, original_message, ai_response, username)
                except Exception as e:
                    logger.error(f"Could not save the conversation for {user_id}: {e}")

            except Exception as e:
                logger.error(f"Error handling chat command: {e}")
                await interaction.followup.send("something went wrong on my end, try again?")
            finally:
                if held_lock is not None:
                    held_lock.release()

    async def _register_tldr_command(self):
        """Register the tldr command"""
        @self.bot.tree.command(name="tldr", description="Sum up the recent messages in this channel, or ask about them")
        @app_commands.describe(
            count="How many messages to read, 5 to 200 (default 50)",
            prompt="What to do with them instead of a recap, like 'what did we decide' or 'make a pdf of the plan'"
        )
        async def tldr(interaction: discord.Interaction, count: Optional[int] = 50, prompt: Optional[str] = None):
            usage_context.set_user(interaction.user.id)
            try:
                await interaction.response.defer(thinking=True)
                reply = await self.bot.command_handler.summarize_channel(
                    interaction.channel, interaction.user.display_name, str(interaction.user.id), count or 50,
                    prompt=(prompt or "").strip()
                )
                await self._send_followup(interaction, reply.text, reply.files, custom_emoji=False)
            except Exception as e:
                logger.error(f"Error handling tldr command: {e}")
                await interaction.followup.send("couldnt sum that up, try again?")

    async def _register_recent_posts_command(self):
        """Register the switch for reading just-posted images and files in a channel"""
        @self.bot.tree.command(name="recent-posts",
                               description="Turn my reading of just-posted images and files on or off in this channel")
        @app_commands.describe(enabled="on: i look at what was posted right before a message. off: only replies, attachments and /tldr")
        @app_commands.default_permissions(manage_channels=True)
        async def recent_posts(interaction: discord.Interaction, enabled: bool):
            # Discord hides the command from people without the permission. This covers the rest.
            permissions = getattr(interaction, "permissions", None)
            if interaction.guild and not getattr(permissions, "manage_channels", False):
                await interaction.response.send_message(
                    "only someone who can manage this channel can change that", ephemeral=True)
                return
            get_channel_settings().set_reads_recent_posts(interaction.channel_id, enabled)
            if enabled:
                answer = "aight, in this channel i look at pics and files posted right before a message again"
            else:
                answer = ("aight, in this channel i wont look at pics and files unless someone replies to them, "
                          "attaches them or runs `/tldr`")
            await interaction.response.send_message(answer)

    async def _register_join_in_command(self):
        """Register the switch for answering messages nobody addressed to the bot in a channel"""
        @self.bot.tree.command(name="join-in",
                               description="Turn my joining in on messages nobody sent to me on or off in this channel")
        @app_commands.describe(enabled="on: right after someone talks to me i may answer other messages too. off: only when called")
        @app_commands.default_permissions(manage_channels=True)
        async def join_in(interaction: discord.Interaction, enabled: bool):
            # Discord hides the command from people without the permission. This covers the rest.
            permissions = getattr(interaction, "permissions", None)
            if interaction.guild and not getattr(permissions, "manage_channels", False):
                await interaction.response.send_message(
                    "only someone who can manage this channel can change that", ephemeral=True)
                return
            get_channel_settings().set_joins_in(interaction.channel_id, enabled)
            if enabled:
                answer = "aight, in this channel i might jump in for a bit after someone talks to me"
            else:
                answer = "aight, in this channel i only talk when someone calls me"
            await interaction.response.send_message(answer)

    async def _register_usage_command(self):
        """Register the usage command"""
        @self.bot.tree.command(name="usage", description="See how much of the AI budget is used")
        async def usage(interaction: discord.Interaction):
            # Their own numbers, so only they see them
            await interaction.response.send_message(
                self.bot.command_handler.usage_summary(str(interaction.user.id)), ephemeral=True)
    
    async def _register_model_command(self):
        """Register the model command"""
        @self.bot.tree.command(name="model", description="See which AI model the bot is running on")
        async def model(interaction: discord.Interaction):
            await interaction.response.send_message(self.bot.command_handler.model_summary())

    async def _register_setup_command(self):
        """Register the setup command"""
        @self.bot.tree.command(name="setup", description="See where the bot runs and the settings it runs with")
        async def setup(interaction: discord.Interaction):
            await interaction.response.send_message(self.bot.command_handler.setup_summary())

    async def _register_stats_command(self):
        """Register the stats command"""
        @self.bot.tree.command(name="stats", description="See everything the bot has used so far")
        async def stats(interaction: discord.Interaction):
            await interaction.response.send_message(stats_summary())

    async def _register_courses_command(self):
        """Register the courses command"""
        @self.bot.tree.command(name="courses", description="See the course material the bot can search")
        async def courses(interaction: discord.Interaction):
            await interaction.response.send_message(courses_summary())

    async def _register_music_command(self):
        """Register the music command"""
        @self.bot.tree.command(name="music", description="Play music from YouTube or Spotify")
        @app_commands.describe(query="YouTube/Spotify URL or search term")
        async def music(interaction: discord.Interaction, query: str):
            usage_context.set_user(interaction.user.id)
            # Only a server member has a voice state. In a DM there is none.
            voice = getattr(interaction.user, "voice", None)
            if not voice or not interaction.guild_id:
                await interaction.response.send_message("yo, u gotta be in a voice channel to play music")
                return
            
            # Join the user's voice channel
            voice_channel = voice.channel
            
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
                await interaction.followup.send("couldnt play that, something went wrong on my end")

    async def _register_skip_command(self):
        """Register the skip command"""
        @self.bot.tree.command(name="skip", description="Skip to the next song in queue")
        async def skip(interaction: discord.Interaction):
            if not interaction.guild_id:
                await interaction.response.send_message("this only works in servers not dms")
                return
                
            success, response = await self.music_manager.skip(interaction.guild_id)
            await interaction.response.send_message(response)

    async def _register_pause_command(self):
        """Register the pause command"""
        @self.bot.tree.command(name="pause", description="Pause the current song")
        async def pause(interaction: discord.Interaction):
            if not interaction.guild_id:
                await interaction.response.send_message("this only works in servers not dms")
                return
                
            success, response = await self.music_manager.pause(interaction.guild_id)
            await interaction.response.send_message(response)

    async def _register_resume_command(self):
        """Register the resume command"""
        @self.bot.tree.command(name="resume", description="Resume playback")
        async def resume(interaction: discord.Interaction):
            if not interaction.guild_id:
                await interaction.response.send_message("this only works in servers not dms")
                return
                
            success, response = await self.music_manager.resume(interaction.guild_id)
            await interaction.response.send_message(response)

    async def _register_stop_command(self):
        """Register the stop command"""
        @self.bot.tree.command(name="stop", description="Stop playback and clear the queue")
        async def stop(interaction: discord.Interaction):
            if not interaction.guild_id:
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
            if not interaction.guild_id:
                await interaction.response.send_message("this only works in servers not dms")
                return
                
            # Get the current song
            current_song = self.music_manager.get_current_song(interaction.guild_id)
            
            # Get the queue
            queue = self.music_manager.get_queue(interaction.guild_id)
            
            if not current_song and not queue:
                await interaction.response.send_message("the queue is empty, add something with /music")
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
            if not interaction.guild_id:
                await interaction.response.send_message("this only works in servers not dms")
                return
                
            try:
                volume = max(0, min(100, level)) / 100.0  # Clamp to 0-100 and convert to 0.0-1.0
                success, response = await self.music_manager.set_volume(interaction.guild_id, volume)
                await interaction.response.send_message(response)
            except Exception as e:
                logger.error(f"Error in volume command: {e}")
                await interaction.response.send_message("couldnt change the volume, something went wrong on my end")
    
    async def _register_help_command(self):
        """Register the help command"""
        @self.bot.tree.command(name="help", description="Show all available commands and how to use them")
        async def help(interaction: discord.Interaction):
            await interaction.response.send_message(embed=build_help_embed())

    async def _register_relate_command(self):
        """Register the relate command"""
        @self.bot.tree.command(name="relate", description="Play a song related to the current one based on YouTube recommendations")
        async def relate(interaction: discord.Interaction):
            usage_context.set_user(interaction.user.id)
            if not getattr(interaction.user, "voice", None) or not interaction.guild_id:
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
                await interaction.followup.send("couldnt find a related song, something went wrong on my end")