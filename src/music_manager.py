import asyncio
import discord
import logging
import re
import os
import time
from typing import Dict, List, Optional, Tuple
import yt_dlp
import sys

# Setup logging with proper encoding handling
logger = logging.getLogger(__name__)

# Custom handler to handle Unicode characters properly
class SafeStreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            # Safely encode/decode to handle Unicode characters
            msg = msg.encode(sys.stdout.encoding or 'utf-8', 'replace').decode(sys.stdout.encoding or 'utf-8')
            stream.write(msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)

# Replace the default handler with our safe handler
for handler in logger.handlers[:]:
    logger.removeHandler(handler)
handler = SafeStreamHandler(sys.stdout)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# YouTube and Spotify URL regex patterns
YOUTUBE_REGEX = r'(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]+)'
SPOTIFY_REGEX = r'(?:https?://)?(?:open\.spotify\.com/track/|spotify:track:)([a-zA-Z0-9]+)'

# Inactivity timeout in seconds (10 minutes)
INACTIVITY_TIMEOUT = 600

class Song:
    """Represents a song with metadata"""
    def __init__(self, url: str, title: str, duration: int, requested_by: str):
        self.url = url
        self.title = title
        self.duration = duration  # in seconds
        self.requested_by = requested_by
        
    def __str__(self):
        return f"{self.title} [{self.format_duration()}] (requested by {self.requested_by})"
    
    def format_duration(self) -> str:
        """Format duration in MM:SS format"""
        # Ensure duration is treated as integer
        duration_seconds = int(self.duration)
        minutes = duration_seconds // 60
        seconds = duration_seconds % 60
        return f"{minutes}:{seconds:02d}"

class GuildMusicState:
    """Manages music playback state for a specific guild"""
    def __init__(self):
        self.voice_client: Optional[discord.VoiceClient] = None
        self.queue: List[Song] = []
        self.current_song: Optional[Song] = None
        self.volume = 0.5  # Default volume (0.0 to 1.0)
        self.is_playing = False
        self.is_paused = False
        self.loop = False
        self.last_activity = time.time()  # Track when the last activity occurred

class MusicManager:
    """Manages music playback across all guilds"""
    def __init__(self):
        # Initialize FFmpeg path
        try:
            self.ffmpeg_path = self._find_ffmpeg()
            logger.info(f"Found FFmpeg at: {self.ffmpeg_path}")
        except Exception as e:
            logger.critical(f"FFmpeg not found or not working: {e}")
            logger.critical("Please install FFmpeg (https://ffmpeg.org/download.html) and make sure it's in your PATH")
            self.ffmpeg_path = "ffmpeg"  # Fallback, will likely fail
        
        # Rest of the initialization
        self.guild_music_states: Dict[int, GuildMusicState] = {}
        self.yt_dlp_options = {
            'format': 'bestaudio/best',
            'restrictfilenames': True,
            'noplaylist': True,
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'quiet': True,
            'no_warnings': True,
            'default_search': 'auto',
            'source_address': '0.0.0.0',
        }
        self.inactivity_check_task = None  # Initialize as None, we'll create it later
    
    def _find_ffmpeg(self):
        """Verify FFmpeg is available"""
        try:
            from shutil import which
            ffmpeg_path = which("ffmpeg")
            if not ffmpeg_path:
                # Fallback to common Windows install locations if not in PATH
                common_locations = [
                    r"C:\ffmpeg\bin\ffmpeg.exe",
                    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
                    r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe"
                ]
                for location in common_locations:
                    if os.path.isfile(location):
                        return location
                raise RuntimeError("FFmpeg not found in PATH or common locations")
            return ffmpeg_path
        except Exception as e:
            logger.critical("FFmpeg requirement check failed: %s", str(e))
            raise
    
    async def start_inactivity_checker(self):
        """Start the task that checks for inactive voice clients"""
        if self.inactivity_check_task is None:
            self.inactivity_check_task = asyncio.create_task(self._check_inactive_voice_clients())
            logger.info("Inactivity checker task started")
    
    def update_activity(self, guild_id: int) -> None:
        """Update the last activity timestamp for a guild"""
        state = self.get_guild_music_state(guild_id)
        state.last_activity = time.time()
    
    async def _check_inactive_voice_clients(self) -> None:
        """Periodically check for inactive voice clients and disconnect them"""
        while True:
            try:
                current_time = time.time()
                for guild_id, state in list(self.guild_music_states.items()):
                    if state.voice_client and state.voice_client.is_connected():
                        # Check if bot is alone in the voice channel too long
                        if hasattr(state, 'alone_since') and current_time - state.alone_since > 600:  # 10 minutes
                            logger.info(f"Bot has been alone in guild {guild_id} for 10 minutes, disconnecting")
                            await self.leave_voice_channel(guild_id)
                        
                        # Also check general inactivity timeout
                        elif current_time - state.last_activity > INACTIVITY_TIMEOUT:
                            logger.info(f"Guild {guild_id}: Disconnecting due to inactivity timeout")
                            await self.leave_voice_channel(guild_id)
                
                # Check every 30 seconds
                await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"Error in inactivity checker: {e}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                await asyncio.sleep(60)
    
    def get_guild_music_state(self, guild_id: int) -> GuildMusicState:
        """Get or create a GuildMusicState for the specified guild"""
        if guild_id not in self.guild_music_states:
            self.guild_music_states[guild_id] = GuildMusicState()
        return self.guild_music_states[guild_id]
    
    async def join_voice_channel(self, voice_channel: discord.VoiceChannel) -> discord.VoiceClient:
        """Join a voice channel and return the voice client"""
        guild_id = voice_channel.guild.id
        state = self.get_guild_music_state(guild_id)
        
        # Check if already connected to a voice channel in this guild
        if state.voice_client and state.voice_client.is_connected():
            await state.voice_client.move_to(voice_channel)
        else:
            state.voice_client = await voice_channel.connect()
        
        # Update activity timestamp
        self.update_activity(guild_id)
        return state.voice_client
    
    async def extract_info(self, url: str, requested_by: str = None) -> Tuple[str, str, int]:
        """Extract audio URL, title and duration from a YouTube or Spotify URL or search query"""
        logger.info(f"Extracting info for: {url}")
        
        # Check if it's a Spotify URL
        spotify_match = re.match(SPOTIFY_REGEX, url)
        if spotify_match:
            try:
                # For Spotify links, create a better search query
                import requests
                from bs4 import BeautifulSoup
                
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                
                track_id = spotify_match.group(1)
                full_url = f"https://open.spotify.com/track/{track_id}"
                logger.info(f"Fetching Spotify track info from: {full_url}")
                
                response = requests.get(full_url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    search_query = f"spotify track {track_id}"
                    try:
                        soup = BeautifulSoup(response.text, 'html.parser')
                        
                        # Try to get title and artist from meta tags
                        title_tag = soup.find("meta", property="og:title")
                        artist_tag = soup.find("meta", property="og:description")
                        
                        search_terms = []
                        if title_tag and title_tag.get("content"):
                            search_terms.append(title_tag.get("content"))
                        if artist_tag and artist_tag.get("content"):
                            artist = artist_tag.get("content").split("·")[0].strip()
                            search_terms.append(artist)
                        
                        if search_terms:
                            search_query = " ".join(search_terms)
                            logger.info(f"Extracted song info from Spotify: {search_query}")
                    except Exception as e:
                        logger.error(f"Error parsing Spotify page: {e}")
                        
                    search_url = f"ytsearch1:{search_query}"
                else:
                    logger.warning(f"Failed to get Spotify page, status: {response.status_code}")
                    search_url = f"ytsearch1:spotify track {track_id}"
                    
            except Exception as e:
                logger.error(f"Error extracting Spotify metadata: {e}")
                # Fallback to just the track ID
                track_id = spotify_match.group(1)
                search_url = f"ytsearch1:spotify track {track_id}"
            
            logger.info(f"Using search query for Spotify track: {search_url}")
            
        elif re.match(YOUTUBE_REGEX, url):
            # Direct YouTube URL
            search_url = url
        else:
            # Not a URL - ALWAYS enhance with AI
            try:
                # Use the AI to enhance the search query for any non-URL input
                enhanced_query = await self._enhance_search_query(url, requested_by)
                logger.info(f"AI enhanced query: '{url}' -> '{enhanced_query}'")
                # Use the enhanced query
                search_url = f"ytsearch1:{enhanced_query}"
            except Exception as e:
                # Fallback to original query if AI enhancement fails
                logger.error(f"AI query enhancement failed: {e}")
                search_url = f"ytsearch1:{url}"
            
        try:
            # Configure yt-dlp for reliable streaming
            options = {
                'format': 'bestaudio/best',
                'noplaylist': True,
                'nocheckcertificate': True,
                'ignoreerrors': False,
                'logtostderr': False,
                'quiet': True,
                'no_warnings': True,
                'default_search': 'auto',
                'source_address': '0.0.0.0',  # IPv6 addresses cause issues sometimes
                'extract_flat': False,  # Full extraction
                'skip_download': True,  # We only need the info
                'retries': 10,          # Retry more times
                'socket_timeout': 30,   # Increase timeout further
                # Add these new options to filter out members-only content
                'match_filter': lambda info_dict: None if info_dict.get('availability') == 'subscriber_only' else None if 'members only' in info_dict.get('title', '').lower() else None if info_dict.get('premium') is True else True,
            }
            
            with yt_dlp.YoutubeDL(options) as ydl:
                logger.info(f"Extracting info with yt-dlp for: {search_url}")
                info = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.extract_info(search_url, download=False)
                )
                
                # Handle search results
                if 'entries' in info:
                    if not info['entries']:
                        raise ValueError("No results found")
                    info = info['entries'][0]  # Get the first result
                
                if not info:
                    raise ValueError("Could not extract info from URL")
                
                # Get title and duration
                title = info.get('title', 'Unknown Title')
                duration = info.get('duration', 0)
                
                # Get direct audio URL - simplified to use the direct url
                audio_url = info.get('url')
                if not audio_url:
                    # Fallback to first format with a URL
                    for format_item in info.get('formats', []):
                        if 'url' in format_item:
                            audio_url = format_item['url']
                            break
                
                if not audio_url:
                    raise ValueError("Could not extract a valid audio URL")
                
                logger.info(f"Successfully extracted info for: {title} (duration: {duration}s)")
                return audio_url, title, duration
                
        except Exception as e:
            logger.error(f"Error extracting info with yt-dlp: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
    
    async def play(self, guild_id: int, url: str, requested_by: str) -> Tuple[bool, str]:
        """Add a song to the queue and start playing if not already"""
        state = self.get_guild_music_state(guild_id)
        
        try:
            # Update activity timestamp whenever there's user interaction
            self.update_activity(guild_id)
            
            # If state had an alone_since attribute, remove it since there's user activity
            if hasattr(state, 'alone_since'):
                delattr(state, 'alone_since')
            
            # Rest of the method remains the same
            logger.info(f"Attempting to play: {url} (requested by {requested_by})")
            audio_url, title, duration = await self.extract_info(url, requested_by)
            
            # Log the audio URL (truncated for security)
            logger.info(f"Audio URL obtained (first 50 chars): {audio_url[:50]}...")
            
            # Create song object
            song = Song(audio_url, title, duration, requested_by)
            
            # Add to queue
            state.queue.append(song)
            
            # Start playing if not already
            if not state.is_playing and not state.is_paused:
                return await self._play_next(guild_id)
            
            return True, f"Added to queue: {song}"
            
        except Exception as e:
            error_message = str(e)
            logger.error(f"Error playing music: {error_message}")
            
            # Provide user-friendly error messages
            if "No results found" in error_message:
                return False, "couldn't find any songs matching that query"
            elif "Could not extract info" in error_message:
                return False, "had trouble getting that song info, maybe try a different link?"
            elif "Could not extract a valid audio URL" in error_message:
                return False, "can't get the audio for that song sorry, try another one"
            else:
                return False, f"error playing that track: {error_message}"
    
    async def _play_next(self, guild_id: int) -> Tuple[bool, str]:
        """Play the next song in the queue"""
        state = self.get_guild_music_state(guild_id)
        
        # Update activity timestamp
        self.update_activity(guild_id)
        
        if not state.voice_client or not state.voice_client.is_connected():
            return False, "Not connected to a voice channel"
        
        if not state.queue and not state.loop:
            state.is_playing = False
            state.current_song = None
            return False, "Queue is empty"
        
        # Get the next song
        if state.loop and state.current_song:
            next_song = state.current_song
        else:
            if not state.queue:
                return False, "Queue is empty"
            next_song = state.queue.pop(0)
        
        state.current_song = next_song
            
        try:
            # Simplified FFmpeg options to improve reliability
            ffmpeg_options = {
                'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                'options': '-vn'  # Simplified options: just disable video
            }

            logger.info(f"Playing URL: {next_song.url[:100]}... with FFmpeg at {self.ffmpeg_path}")
            
            # Add executable path to FFmpegPCMAudio
            audio_source = discord.FFmpegPCMAudio(
                next_song.url,
                executable=self.ffmpeg_path,
                **ffmpeg_options
            )
            
            # Apply volume transformation
            audio_source = discord.PCMVolumeTransformer(audio_source, volume=state.volume)
            
            def after_playing(error):
                if error:
                    logger.error(f"Error playing audio: {error}")
                else:
                    logger.info("Song finished playing normally")
                
                # Schedule next song in the event loop
                asyncio.run_coroutine_threadsafe(
                    self._play_next(guild_id), 
                    state.voice_client.loop
                )
            
            # Start playing
            state.voice_client.play(audio_source, after=after_playing)
            state.is_playing = True
            state.is_paused = False
            
            logger.info(f"Now playing: {next_song}")
            return True, f"Now playing: {next_song}"
            
        except Exception as e:
            logger.error(f"Error starting playback: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False, f"Error playing {next_song.title}: {str(e)}"
    
    async def skip(self, guild_id: int) -> Tuple[bool, str]:
        """Skip the current song"""
        state = self.get_guild_music_state(guild_id)
        
        self.update_activity(guild_id)
        
        if not state.voice_client or not state.is_playing:
            return False, "Nothing is playing"
        
        state.voice_client.stop()
        return True, "Skipped the current song"
    
    async def pause(self, guild_id: int) -> Tuple[bool, str]:
        """Pause the current song"""
        state = self.get_guild_music_state(guild_id)
        
        self.update_activity(guild_id)
        
        if not state.voice_client or not state.is_playing:
            return False, "Nothing is playing"
            
        if state.is_paused:
            return False, "Music is already paused"
            
        state.voice_client.pause()
        state.is_paused = True
        return True, "Paused the music"
    
    async def resume(self, guild_id: int) -> Tuple[bool, str]:
        """Resume playback"""
        state = self.get_guild_music_state(guild_id)
        
        self.update_activity(guild_id)
        
        if not state.voice_client or not state.is_playing:
            return False, "Nothing is playing"
            
        if not state.is_paused:
            return False, "Music is not paused"
            
        state.voice_client.resume()
        state.is_paused = False
        return True, "Resumed the music"
    
    async def set_volume(self, guild_id: int, volume: float) -> Tuple[bool, str]:
        """Set the playback volume (0.0 to 1.0)"""
        state = self.get_guild_music_state(guild_id)
        
        self.update_activity(guild_id)
        
        if not state.voice_client:
            return False, "Not connected to a voice channel"
        
        # Clamp volume between 0 and 1
        volume = max(0.0, min(1.0, volume))
        
        state.volume = volume
        
        if state.voice_client.source:
            state.voice_client.source.volume = volume
            
        return True, f"Volume set to {int(volume * 100)}%"
    
    async def leave_voice_channel(self, guild_id: int) -> bool:
        """Leave the voice channel in the specified guild"""
        state = self.get_guild_music_state(guild_id)
        
        if state.voice_client and state.voice_client.is_connected():
            # Stop current playback
            if state.voice_client.is_playing():
                state.voice_client.stop()
            
            # Disconnect
            await state.voice_client.disconnect()
            
            # Reset state
            state.is_playing = False
            state.current_song = None
            state.is_paused = False
            if hasattr(state, 'alone_since'):
                delattr(state, 'alone_since')
            
            logger.info(f"Left voice channel in guild {guild_id}")
            return True
        
        return False
    
    def get_queue(self, guild_id: int) -> List[Song]:
        """Get the current queue for the guild"""
        state = self.get_guild_music_state(guild_id)
        return state.queue
    
    def get_current_song(self, guild_id: int) -> Optional[Song]:
        """Get the currently playing song"""
        state = self.get_guild_music_state(guild_id)
        return state.current_song
    
    async def get_related_song(self, guild_id: int, requested_by: str) -> Tuple[bool, str]:
        """Get a related song based on YouTube's recommendations for the currently playing song"""
        state = self.get_guild_music_state(guild_id)
        
        # Update activity timestamp
        self.update_activity(guild_id)
        
        # Check if there's a current song to base recommendations on
        current_song = self.get_current_song(guild_id)
        if not current_song:
            return False, "nothing is currently playing to get recommendations for"
        
        try:
            # Get video ID from the current song's URL if it's a YouTube URL
            video_id = None
            youtube_match = re.match(YOUTUBE_REGEX, current_song.url)
            
            if youtube_match:
                # Direct extraction if it's a YouTube URL
                video_id = youtube_match.group(1)
            else:
                # Otherwise, search for the song on YouTube to get a video ID
                options = {
                    'default_search': 'ytsearch',
                    'noplaylist': True,
                    'extract_flat': True,
                    'skip_download': True,
                    'quiet': True,
                    'no_warnings': True
                }
                
                with yt_dlp.YoutubeDL(options) as ydl:
                    logger.info(f"Searching YouTube for: {current_song.title}")
                    search_results = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: ydl.extract_info(f"ytsearch1:{current_song.title}", download=False)
                    )
                    
                    if not search_results or 'entries' not in search_results or not search_results['entries']:
                        return False, "couldn't find the source video to get recommendations"
                    
                    video_id = search_results['entries'][0]['id']
            
            if not video_id:
                return False, "couldn't determine video ID for recommendations"
                
            logger.info(f"Found video ID: {video_id}")
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            
            # Now get related videos
            options = {
                'skip_download': True,
                'extract_flat': True,
                'quiet': True,
                'no_warnings': True,
                # Add filter for members-only content
                'match_filter': lambda info_dict: None if info_dict.get('availability') == 'subscriber_only' else None if 'members only' in info_dict.get('title', '').lower() else None if info_dict.get('premium') is True else True,
            }
            
            with yt_dlp.YoutubeDL(options) as ydl:
                logger.info(f"Getting recommendations for video ID: {video_id}")
                
                # Get video info without downloading
                video_info = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.extract_info(video_url, download=False)
                )
                
                if not video_info:
                    return False, "failed to get video information"
                    
                # Check for related videos
                if not video_info.get('related_videos'):
                    return False, "no recommendations found for this song"
                    
                # Get 5 related videos maximum (that aren't members-only)
                related_videos = []
                for video in video_info.get('related_videos', []):
                    # Skip if it's a members-only video
                    if video.get('availability') == 'subscriber_only' or 'members only' in video.get('title', '').lower() or video.get('premium') is True:
                        continue
                    related_videos.append(video)
                    if len(related_videos) >= 5:
                        break
                    
                if not related_videos:
                    return False, "no valid recommendations found (filtered out members-only content)"
                    
                # Select a random related video
                import random
                related_video = random.choice(related_videos)
                related_url = f"https://www.youtube.com/watch?v={related_video['id']}"
                
                logger.info(f"Selected related video: {related_video.get('title', 'Unknown')} ({related_url})")
                
                # Play the related song
                return await self.play(guild_id, related_url, requested_by)
                
        except Exception as e:
            logger.error(f"Error getting related song: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False, f"error finding related song: {str(e)}"
    
    def clear_queue(self, guild_id: int) -> bool:
        """Clear the queue for the guild"""
        state = self.get_guild_music_state(guild_id)
        state.queue = []
        return True

    async def _enhance_search_query(self, query: str, requested_by: str = None) -> str:
        """Use AI to enhance music search queries"""
        try:
            # Check if query already contains clear indicators it's a game soundtrack
            if any(game_term in query.lower() for game_term in ["warframe", "soundtrack", "ost", "game music", "theme"]):
                # For game soundtrack queries, keep the original query and add "soundtrack" if needed
                if "soundtrack" not in query.lower() and "ost" not in query.lower():
                    enhanced_query = f"{query} soundtrack"
                    user_context = f" requested by {requested_by}" if requested_by else ""
                    logger.info(f"Game soundtrack detected{user_context}: '{query}' -> '{enhanced_query}'")
                    return enhanced_query
                return query
                
            # Create a prompt that helps find the right song
            prompt = f"""You are a music search assistant for a Discord bot. Your ONLY job is to convert the user's request into the EXACT song they want.

INSTRUCTIONS:
1. Convert natural language requests into specific YouTube search queries with artist name and song title
2. Focus on finding songs ONLY, not videos or other content
3. Interpret vague descriptions, lyrics snippets, or "vibe" requests
4. Respond with ONLY the search query, nothing else - no explanations or comments
5. For requests like "songs similar to X" or "same vibe as X", pick ONE specific song that matches
6. For descriptions of songs where the user doesn't know the title, determine the most likely song they mean
7. IMPORTANT: For video game music or soundtrack requests, preserve the game name and song title
8. If request appears to reference game music (Warframe, Destiny, etc.), include the game name in your response
9. For any soundtracks or OSTs, prioritize the original soundtrack name over popular songs

Examples:
Request: "give me something that has the same vibe as see you again"
Response: "Tyler, The Creator See You Again"

Request: "old town road but the one with billy ray cyrus"
Response: Lil Nas X Old Town Road remix ft. Billy Ray Cyrus

Request: "the great despire warframe"
Response: Warframe The Great Despire soundtrack

Request: "we lift together warframe"
Response: Warframe We All Lift Together soundtrack

Request: "{query}"
"""

            # Rest of the method remains the same
            # Import and set up AI only when needed (to avoid circular imports)
            import google.generativeai as genai
            import os
            from dotenv import load_dotenv
            
            # Load API key
            load_dotenv()
            api_key = os.getenv("GEMINI_API_KEY")
            
            if not api_key:
                logger.warning("No Gemini API key found for AI search enhancement")
                return query
                
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-1.5-flash-latest')
            
            # Get AI response with a timeout
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, 
                    lambda: model.generate_content(prompt).text
                ),
                timeout=5.0  # Increased timeout to 5 seconds for better results
            )
            
            # Clean up the response
            enhanced_query = response.strip(' "\'\n')
            
            # If empty or too long, fall back to original query
            if not enhanced_query or len(enhanced_query) > 150:
                return query
                
            # Add user context if available
            user_context = f" requested by {requested_by}" if requested_by else ""
            logger.info(f"AI enhanced music query{user_context}: '{query}' -> '{enhanced_query}'")
            
            return enhanced_query
            
        except Exception as e:
            logger.error(f"Error enhancing search query with AI: {e}")
            return query  # Fall back to the original query

    async def on_voice_state_update(self, member, before, after):
        """Handle voice state changes to detect when users leave"""
        # Skip if the member is the bot itself
        if member.bot:
            return
            
        # Only care about users leaving a channel
        if before and before.channel and (not after or before.channel != after.channel):
            # Find any guild states where the bot is in the same channel that was left
            for guild_id, state in self.guild_music_states.items():
                if not state.voice_client or not state.voice_client.is_connected():
                    continue
                    
                # Check if this is the channel the user left
                if state.voice_client.channel.id == before.channel.id:
                    # Count non-bot members in the voice channel
                    real_members = [m for m in before.channel.members if not m.bot]
                    
                    # If no real members remain and the bot is there
                    if not real_members:
                        logger.info(f"All users left voice channel in guild {guild_id}, starting alone timer")
                        # Start tracking when the bot became alone
                        state.alone_since = time.time()
                        break