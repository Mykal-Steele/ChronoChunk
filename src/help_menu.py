"""The /help menu. Built in one place so the slash command and a typed /help show the same thing."""
from typing import List

import discord

from config.config import Config
from src.ai_response_handler import MAX_FILES_PER_REPLY
from src.version import __version__
from src.message_context import (
    MAX_FILE_BYTES, MAX_FILE_CHARS, MAX_FILES, MAX_IMAGE_BYTES, MAX_IMAGES, MAX_PDF_IMAGES, MAX_PDF_PAGES,
    RECENT_FILE_CHARS, RECENT_MAX_FILES, RECENT_MAX_IMAGES, RECENT_POST_MINUTES, RECENT_POSTS, TLDR_FILE_CHARS,
    TLDR_MAX_FILES, TLDR_MAX_IMAGES,
)

MB = 1024 * 1024


def _window(seconds: int) -> str:
    """A rate limit window in words: 'a day', 'an hour' or 'every 30 min'."""
    if seconds == 86400:
        return "a day"
    if seconds == 3600:
        return "an hour"
    return f"every {seconds // 60} min"


def limit_lines() -> List[str]:
    """The bot's limits in its own voice, read from the settings that enforce them."""
    chat_count, chat_window = Config.RATE_LIMITS["chat"]
    file_count, file_window = Config.RATE_LIMITS["file"]
    tldr_count, tldr_window = Config.RATE_LIMITS["tldr"]
    return [
        f"images: png, jpg or webp, up to {MAX_IMAGE_BYTES // MB} MB each. files: pdf, md, txt and code files, up to {MAX_FILE_BYTES // MB} MB each",
        f"i read up to {MAX_IMAGES} images and {MAX_FILES} files per message, plus up to {MAX_PDF_IMAGES} pictures from inside pdfs",
        f"pdfs: the first {MAX_PDF_PAGES} pages. long files get cut at {MAX_FILE_CHARS:,} characters",
        f"stuff posted right before ur message: the last {RECENT_POSTS} messages, {RECENT_POST_MINUTES} min old at most, "
        f"{RECENT_MAX_IMAGES} images and {RECENT_MAX_FILES} file (cut at {RECENT_FILE_CHARS:,} characters)",
        f"`/tldr`: 5 to 200 messages, plus the newest {TLDR_MAX_IMAGES} images and {TLDR_MAX_FILES} files in them "
        f"(each cut at {TLDR_FILE_CHARS:,} characters). {tldr_count} recaps {_window(tldr_window)}",
        f"chat: {chat_count} messages {_window(chat_window)}. files i make: {file_count} {_window(file_window)}, "
        f"{MAX_FILES_PER_REPLY} per reply",
        "cant read yet: gifs, videos, voice messages, word, excel and powerpoint files",
    ]


def build_help_embed() -> discord.Embed:
    """Every command, what the bot can read and write, and its limits. Kept clean: no swearing in here."""
    embed = discord.Embed(
        title="ChronoChunk Bot Commands",
        description="here's everything i can do, my g:",
        color=0x9B59B6  # Purple color
    )

    general_cmds = [
        "`/chat <message>` - talk with me directly",
        "`/info` - see what i know about you",
        "`/mydata` - same as /info, shows what i remember",
        "`/forget <info>` - make me forget specific info (empty to wipe all)",
        "`/code` - get link to my source code"
    ]
    embed.add_field(name="💬 general commands", value="\n".join(general_cmds), inline=False)

    reading_cmds = [
        "reply to any message and type `/` plus what u want, like `/read this` or `/summarize` - i read that message and whatever is attached to it",
        "works on images, pdfs (the pictures inside them too), and md/txt/code files. attach one to ur own `/` message and i read that too",
        "no reply needed for something that was just posted: drop a pic or file, then type `/explain this`",
        "`/recent-posts off` - stop me doing that in this channel (needs manage channel). `on` brings it back",
        "paste a link to a discord message and i read that message and its files",
        "`/tldr <count>` - catch up on the last messages in the channel (default 50, max 200). i look at the images and files in them too",
        "`/tldr <count> <what u want>` - ask about those messages or have me make something from them, like `/tldr 60 what did we decide` or `/tldr 60 make a pdf of the design`",
        "pinging me works the same as starting with `/`"
    ]
    embed.add_field(name="📎 reading stuff", value="\n".join(reading_cmds), inline=False)

    writing_cmds = [
        "ask for an email, proposal, report, cover letter or notes and i write it properly, no slang",
        "ask for it as a pdf or docx (`/make this a pdf`, `/write a proposal for X as docx`) and i attach the file",
        "ask for a diagram or flowchart and i send it as an image",
        "`/usage` - how much of the ai budget is used"
    ]
    embed.add_field(name="📝 writing and files", value="\n".join(writing_cmds), inline=False)

    embed.add_field(name="📏 limits", value="\n".join(f"- {line}" for line in limit_lines()), inline=False)

    game_cmds = [
        "`/game <max>` - start a number guessing game (1 to max, default 100)",
        "`/guess <number>` - make a guess in the game",
        "`/end` - end the current game"
    ]
    embed.add_field(name="🎮 game commands", value="\n".join(game_cmds), inline=False)

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

    fun_cmds = [
        "`/good-boy` - get a smiley face :)"
    ]
    embed.add_field(name="😂 other stuff", value="\n".join(fun_cmds), inline=False)

    embed.set_footer(text=f"ChronoChunk v{__version__} | u can also just chat with me normally in any channel "
                          "by replying to the bot message, no commands needed")
    return embed
