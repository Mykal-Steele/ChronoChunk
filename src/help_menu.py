"""The /help menu. Built in one place so the slash command and a typed /help show the same thing."""
from typing import List

import discord

from config.config import Config
from src.tools.documents import MAX_FILES_PER_REPLY
from src.version import __version__
from src.setup_info import span as _span
from src.message_context import (
    MAX_FILE_BYTES, MAX_FILE_CHARS, MAX_FILES, MAX_IMAGE_BYTES, MAX_IMAGES, MAX_PDF_PAGES, RECENT_FILE_CHARS,
    RECENT_MAX_FILES, RECENT_MAX_IMAGES, RECENT_POST_MINUTES, RECENT_POSTS, TLDR_FILE_CHARS, TLDR_MAX_FILES,
    TLDR_MAX_IMAGES,
)

MB = 1024 * 1024


PRIVACY_URL = "https://github.com/Mykal-Steele/ChronoChunk/blob/main/PRIVACY.md"

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
    pdf_count, pdf_window = Config.RATE_LIMITS["pdf"]
    web_count, web_window = Config.RATE_LIMITS["web"]
    course_count, course_window = Config.RATE_LIMITS["course"]
    return [
        f"images: png, jpg or webp, up to {MAX_IMAGE_BYTES // MB} MB each. files: pdf, md, txt and code files, up to {MAX_FILE_BYTES // MB} MB each",
        f"i read up to {MAX_IMAGES} images and {MAX_FILES} files per message",
        f"pdfs: the first {MAX_PDF_PAGES} pages. i look at every slide, so tables, charts and diagrams count. the first look at a big deck takes up to a minute",
        f"long text files get cut at {MAX_FILE_CHARS:,} characters",
        f"stuff posted right before ur message: the last {RECENT_POSTS} messages, {RECENT_POST_MINUTES} min old at most, "
        f"{RECENT_MAX_IMAGES} images and {RECENT_MAX_FILES} file (cut at {RECENT_FILE_CHARS:,} characters)",
        f"`/tldr`: 5 to 200 messages, plus the newest {TLDR_MAX_IMAGES} images and {TLDR_MAX_FILES} files in them "
        f"(each cut at {TLDR_FILE_CHARS:,} characters). {tldr_count} recaps {_window(tldr_window)}",
        f"chat: {chat_count} messages {_window(chat_window)}. files i make: {file_count} {_window(file_window)}, "
        f"{MAX_FILES_PER_REPLY} per reply",
        f"new pdfs i read in full: {pdf_count} {_window(pdf_window)}. web lookups: {web_count} {_window(web_window)}. "
        f"course searches: {course_count} {_window(course_window)}. `/usage` shows what u have left",
        "cant read yet: gifs, videos, voice messages, word, excel and powerpoint files",
    ]


def build_music_help_embed() -> discord.Embed:
    """/help music: how to play, every music command, and the limits from the settings that enforce them."""
    per_user, window = Config.RATE_LIMITS["music"]
    per_guild, guild_window = Config.RATE_LIMITS["music_guild"]
    controls, control_window = Config.RATE_LIMITS["music_control"]
    embed = discord.Embed(
        title="ChronoChunk Music",
        description="how music works, my g:",
        color=0x9B59B6,
    )
    playing = [
        "join a voice channel first, then `/music <song or link>`",
        "a YouTube link, a Spotify track link, or just words: `/music never gonna give you up`, `/music sad lofi for studying`",
        "a playlist link only plays its first song",
        "`/relate` - add a song like the one playing, picked from YouTube's mix",
        "i cant start songs from chat. ask me in chat and i'll point u to `/music`",
    ]
    embed.add_field(name="▶️ playing", value="\n".join(playing), inline=False)
    control = [
        "`/queue` - what's playing and what's next",
        "`/skip`, `/pause`, `/resume` - the song playing now",
        "`/volume <0-100>` - how loud i am",
        "`/stop` - stop, clear the queue and leave voice",
        "skip, pause, resume, volume and stop need Manage Server, so one person cant skip everyone's songs",
    ]
    embed.add_field(name="🎛️ controls", value="\n".join(control), inline=False)
    limits = [
        f"{per_user} songs per person {_window(window)}, {per_guild} per server {_window(guild_window)}"
        if guild_window != window else f"{per_user} songs per person and {per_guild} per server {_window(window)}",
        f"{controls} controls per person {_window(control_window)}",
        f"{Config.MUSIC_MAX_QUEUE_PER_GUILD} songs in the queue, {Config.MUSIC_MAX_SONGS_PER_USER} from one person",
        f"i leave voice after {_span(Config.MUSIC_ALONE_SECONDS)} with nobody else in the channel, "
        f"or after {_span(Config.MUSIC_IDLE_SECONDS)} with nothing playing. a long song never gets cut off",
    ]
    embed.add_field(name="📏 limits", value="\n".join(f"- {line}" for line in limits), inline=False)
    embed.set_footer(text=f"ChronoChunk v{__version__} | /help shows everything else")
    return embed


def _bullets(lines: List[str]) -> str:
    return "\n".join(line if line.startswith("- ") else f"- {line}" for line in lines)


def _page(title: str, description: str, fields: List[tuple]) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=0x9B59B6)
    for name, lines in fields:
        embed.add_field(name=name, value=_bullets(lines), inline=False)
    # A footer shows no formatting, so no backticks in it
    embed.set_footer(text=f"ChronoChunk v{__version__} | /help goes back to the start")
    return embed


def build_help_embed() -> discord.Embed:
    """The start page: how to talk to the bot, the commands people use most, and where the rest is."""
    embed = discord.Embed(
        title="ChronoChunk",
        description=("yo my g. u dont need commands for most stuff: reply to one of my messages, ping me, "
                     "or start a message with `/` and say what u want, like `/explain this` or `/write an email to my prof`"),
        color=0x9B59B6,
    )
    embed.add_field(name="the main ones", value=_bullets([
        "`/chat <message>` talk to me from the command list",
        "reply to any message with `/` and what u want (`/summarize`, `/read this`) and i read it and whatever is attached",
        "`/tldr <count>` catch up on what was said here",
        "`/music <song>` play something in ur voice channel",
        "`/usage` what u have left today. only u see it",
    ]), inline=False)
    embed.add_field(name="i remember stuff", value=_bullets([
        "tell me to remember something or note a link and i keep it, then just ask for it later in normal words",
        "`/memory` shows what i know about u",
    ]), inline=False)
    embed.add_field(name="more help", value=_bullets([
        "`/help memory` notes, private notes, how i learn, seeing and deleting what i know",
        "`/help files` images, pdfs, links, making docs and diagrams, searching the web",
        "`/help courses` class slides, recordings and due dates",
        "`/help music` all the music commands and who can control them",
        "`/help limits` how much u can do and what i cant read yet",
    ]), inline=False)
    embed.add_field(name="other commands", value=_bullets([
        "`/info` or `/mydata` the facts i picked up about u. `/forget` deletes everything about u",
        "`/game`, `/guess`, `/end` a number guessing game. `/good-boy` a smiley face",
        "`/model` which ai im on. `/setup` how im set up. `/stats` what i used so far. `/code` my source code",
    ]), inline=False)
    embed.add_field(name="ur data", value=_bullets([
        "what u send me goes to the ai model to answer. what i keep, and how to delete it, is in `/help memory`",
        f"the full list is at <{PRIVACY_URL}>",
    ]), inline=False)
    embed.set_footer(text=f"ChronoChunk v{__version__}")
    return embed


def _memory_page() -> discord.Embed:
    days = Config.CHANNEL_LOG_KEEP_DAYS
    return _page("how my memory works", "i remember what people tell me, across every server im in. heres how it works and how to clean it up", [
        ("teaching me", [
            "just say it: `remember the exam moved to tuesday` or `btw im vegetarian`. i keep who told me, when and where",
            "if u correct something i swap the old one out. only u can change what u said about urself",
            "ask about it later in any server, in ur own words, and ill tell u who said it",
        ]),
        ("notes", [
            "`note down this link, its for my korean presentation` and i keep it",
            "if u dont say who can see it, i ask: public (anyone can ask me for it) or private (only u)",
            "say it up front to skip the question: `save this privately: ...` or `remember this for everyone: ...`",
            "to get it back just ask: `whats the link for my korean presentation`",
            "private notes only come back in a DM with me, since everyone in a channel can read my reply",
        ]),
        ("did it save?", [
            "when i really save something, my reply ends with a small grey line like `saved as m12` or `saved as m12 (private)`",
            "no line means nothing got saved, even if i said noted. just ask again",
        ]),
        ("how i act with u", [
            "tell me what u want: `stop roasting me`, `keep it short`, `talk to me in thai`. i stick to it in every server",
            "a rule for the whole server (`keep answers short here`) only counts in that server, and it cant be about a person",
            "i also learn from 👍 and 👎 on my replies. i go through those once a night",
        ]),
        ("what i keep from chats", [
            f"i keep what's said in server channels for {days} days. DMs are not kept this way",
            "each night i write every busy channel's day down in a few lines, so i can tell u what happened on the 3rd",
            "i only bring those up in the same server, for people who can see that channel",
        ]),
        ("seeing and deleting", [
            "`/memory` shows what u taught me, what others told me about u, how u asked me to act and the days ur in. only u see it",
            "every item has a code like m12, l3 or d5. `/memory delete:m12` deletes that one",
            "`/memory view:server` shows what i learned in this server (needs manage server). managers can delete stuff from their own server",
            "`/forget` deletes everything about u at once. `/forget <text>` deletes one fact. `/info` shows the facts i picked up",
        ]),
    ])


def _files_page() -> discord.Embed:
    return _page("files, links and the web", "i can read stuff, write stuff and look things up", [
        ("reading", [
            "reply to a message with `/` and what u want, like `/read this` or `/summarize`. i read the message and whats attached",
            "images, pdfs (the pictures inside them too) and md, txt or code files. attach one to ur own `/` message and i read that too",
            "something was just posted? no need to reply, just type `/explain this`. `/recent-posts off` stops that in a channel (needs manage channel)",
            "paste a link to a discord message and i read that message and its files",
        ]),
        ("catching up", [
            "`/tldr <count>` sums up the last messages here (50 by default, up to 200), pictures and files included",
            "`/tldr <count> <question>` asks about them or makes something from them: `/tldr 60 what did we decide`",
        ]),
        ("writing", [
            "ask for an email, report, proposal or notes and i write it properly, no slang",
            "say pdf or docx (`/make this a pdf`) and i attach the file. ask for a diagram or flowchart and i send a picture",
        ]),
        ("the web", [
            "give me a link and what u want (`/sum up https://...`) and i open the page",
            "ask about something recent or say look it up and i search the web, with links to where i got it",
            "i cant log in to sites, so pages behind a login or paywall mostly come back empty",
        ]),
        ("talking in a channel", [
            "pinging me works the same as starting with `/`",
            "right after someone talks to me i might jump in on other messages for a bit. `/join-in off` stops that in a channel",
        ]),
    ])


def _courses_page() -> discord.Embed:
    return _page("course material", "i can look things up in ur class slides, lecture recordings and assignments", [
        ("asking", [
            "end a message with `-c`: `/what did the ai lecture say about search -c`. a course code works too: `-csc340`",
            "after that just keep chatting. course mode stays on for u for an hour, and i look things up when i need to",
            "`/whats due this week -c` lists assignments with due dates and time left",
        ]),
        ("what i have", [
            "`/courses` shows which courses i have, how much of each, and when they were last synced",
            "if `/courses` says i have none, course search is off for now and `-c` wont find anything",
        ]),
    ])


def _limits_page() -> discord.Embed:
    return _page("limits", "so nobody burns through everything. `/usage` shows what u have left", [
        ("limits", [f"- {line}" for line in limit_lines()]),
    ])


# Each /help page by its topic word, for the slash command and for typed '/help <topic>'
TOPICS = {
    "memory": _memory_page,
    "files": _files_page,
    "courses": _courses_page,
    "music": build_music_help_embed,
    "limits": _limits_page,
}


def build_topic_help(topic: str) -> discord.Embed:
    return TOPICS[topic]()
