# yo this is where we keep all the settings n stuff
# change these in prod but don't commit the changes lmao

from typing import Dict, Any
import os
from dotenv import load_dotenv
from pathlib import Path

# load the secret stuff from .env
load_dotenv()

class Config:
    """main configuration settings for the bot"""
    
    # Discord bot settings
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

    # Rate limiting settings
    RATE_LIMIT_CLEANUP_INTERVAL = 60 * 60  # Clean up old entries every hour
    
    # Memory and history settings
    MEMORY_SIZE = 30  # Remember last 30 messages in conversation context
    MAX_CONVERSATION_HISTORY = 20  # Keep 20 most recent conversations in user data
    CHANNEL_HISTORY_SIZE = 50  # Number of recent messages to keep in channel history
    
    # Game settings
    MAX_GAME_ATTEMPTS = 10  # Maximum number of attempts in the guessing game
    
    # Paths
    BASE_DIR = Path(__file__).parent.parent
    USER_DATA_DIR = os.path.join(BASE_DIR, "user_data")
    LOG_DIR = os.path.join(BASE_DIR, "logs")
    LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", 30))  # daily log files older than this are deleted
    STATE_DIR = os.path.join(BASE_DIR, "state")  # small files that must survive a restart

    # AI spending caps. Once a cap is hit the bot stops calling the model until it resets.
    # $20 is the whole Azure budget for a month. The daily cap stops one bad day from
    # eating it, and the per-person caps below stop one student from spending it all.
    AI_MONTHLY_BUDGET_USD = float(os.getenv("AI_MONTHLY_BUDGET_USD", 20))
    AI_DAILY_BUDGET_USD = float(os.getenv("AI_DAILY_BUDGET_USD", 2.50))
    AI_BUDGET_RESET_DAY = int(os.getenv("AI_BUDGET_RESET_DAY", 14))  # the Azure credit renews on the 14th

    # What one person may spend, so nobody can use up the bot for everyone. One 30-question
    # study reply is a few cents, and a heavy study day is under a dollar. Three students
    # using it hard for a few days stay well under $10; the $20 ceiling is the backstop.
    # The 7 days roll. A weekly cap of $1.50 each means three people cannot spend the month out.
    AI_USER_DAILY_BUDGET_USD = float(os.getenv("AI_USER_DAILY_BUDGET_USD", 0.50))
    AI_USER_WEEKLY_BUDGET_USD = float(os.getenv("AI_USER_WEEKLY_BUDGET_USD", 1.50))
    # Discord user ids, separated by commas, that the two limits above do not apply to
    AI_UNLIMITED_USER_IDS = {part.strip() for part in os.getenv("AI_UNLIMITED_USER_IDS", "").split(",") if part.strip()}

    # Reasoning effort for chat replies. "low" answers about twice as fast and costs less than the default.
    AI_CHAT_REASONING_EFFORT = os.getenv("AI_CHAT_REASONING_EFFORT", "low")
    # The routing call can use a different deployment, so it gets its own effort. It falls
    # back to the chat setting, which production keeps at "none" for tool calls.
    AI_COURSE_CHECK_REASONING_EFFORT = os.getenv(
        "AI_COURSE_CHECK_REASONING_EFFORT", AI_CHAT_REASONING_EFFORT)
    # Study replies are the one place the model gets more room and a harder think. The
    # raised ceiling is a maximum, not a target: Azure bills the tokens actually written.
    AI_STUDY_REASONING_EFFORT = os.getenv("AI_STUDY_REASONING_EFFORT", "medium")
    AI_CHAT_MAX_TOKENS = int(os.getenv("AI_CHAT_MAX_TOKENS", 4000))
    AI_CHAT_TOOL_MAX_TOKENS = int(os.getenv("AI_CHAT_TOOL_MAX_TOKENS", 8000))
    AI_STUDY_MAX_TOKENS = int(os.getenv("AI_STUDY_MAX_TOKENS", 32000))
    # The call before a reply decides whether the course material is needed. It gets a short
    # answer, and its own deployment only when one is set; empty keeps it on the chat model.
    AI_COURSE_CHECK_MAX_TOKENS = int(os.getenv("AI_COURSE_CHECK_MAX_TOKENS", 1500))
    AI_COURSE_CHECK_DEPLOYMENT = os.getenv("AI_COURSE_CHECK_DEPLOYMENT", "").strip()
    AI_DETAIL_MAX_TOKENS = int(os.getenv("AI_DETAIL_MAX_TOKENS", 12000))
    # Tool rounds per reply. Study requests may need to search several topics; normal chat does not.
    AI_CHAT_MAX_TOOL_ROUNDS = int(os.getenv("AI_CHAT_MAX_TOOL_ROUNDS", 3))
    AI_COURSE_MAX_TOOL_ROUNDS = int(os.getenv("AI_COURSE_MAX_TOOL_ROUNDS", 6))
    AI_STUDY_MAX_TOOL_ROUNDS = int(os.getenv("AI_STUDY_MAX_TOOL_ROUNDS", 8))
    # The document writer gets a harder think, but only once per file.
    AI_WRITER_REASONING_EFFORT = os.getenv("AI_WRITER_REASONING_EFFORT", "medium")

    # gpt-5-mini prices in USD per 1M tokens, used to estimate what each call costs
    AI_PRICE_INPUT_PER_M = float(os.getenv("AI_PRICE_INPUT_PER_M", 0.25))
    AI_PRICE_CACHED_INPUT_PER_M = float(os.getenv("AI_PRICE_CACHED_INPUT_PER_M", 0.025))
    AI_PRICE_OUTPUT_PER_M = float(os.getenv("AI_PRICE_OUTPUT_PER_M", 2.00))

    # Prices for other models the bot calls, in USD per 1M tokens: (input, cached input, output),
    # plus a fourth number for models that charge extra to write the cache. From Azure's price
    # list for Global Standard, October 2026. Matched against the model name in each response.
    # Anything not listed is costed at the prices above.
    AI_MODEL_PRICES = {
        "gpt-5-nano": (0.05, 0.005, 0.40),
        "gpt-6-luna": (0.10, 0.01, 0.50, 0.125),
        "gpt-5.6-luna": (0.20, 0.02, 1.20, 0.25),
        "text-embedding-3-small": (0.02, 0.02, 0.0),
    }

    # How PDFs that someone attaches, replies to or links are read. "auto" describes every page
    # that has a picture, drawing or almost no text, from an image of the page. "on" describes
    # every page. "off" reads the text and the first few embedded pictures only.
    PDF_PAGE_NOTES = os.getenv("PDF_PAGE_NOTES", "auto").strip().lower()
    # How many days a PDF's page descriptions are kept in the database after they were last
    # used, so a restart does not mean reading every deck again. 0 keeps them in memory only.
    PDF_NOTES_KEEP_DAYS = int(os.getenv("PDF_NOTES_KEEP_DAYS", 7))

    # A cheaper deployment for jobs that run behind the reply, such as picking facts out of a
    # message. Empty means those jobs use the main deployment.
    AI_BACKGROUND_DEPLOYMENT = os.getenv("AZURE_OPENAI_BACKGROUND_DEPLOYMENT", "").strip()

    # For testing the live bot. The bot ignores every other bot, except this one bot's messages in
    # this one channel. Both are Discord ids and both must be set. Empty means no bot is answered.
    TEST_BOT_ID = os.getenv("TEST_BOT_ID", "").strip()
    TEST_CHANNEL_ID = os.getenv("TEST_CHANNEL_ID", "").strip()

    # For this many seconds after someone calls the bot in a channel, it may answer a message there
    # that nobody addressed to it, when the model thinks a person in the chat would. 0 turns that off.
    JOIN_IN_SECONDS = int(os.getenv("JOIN_IN_SECONDS", 30))
    # How likely the bot is to take that chance at all, from 0 to 1. Decided once per call, before the model is asked.
    JOIN_IN_CHANCE = float(os.getenv("JOIN_IN_CHANCE", 0.5))

    # Tavily key for web search. Without it the bot can still open links, it just cannot search.
    TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()

    # The course material the bot can search. The sync on a PC builds this file and uploads it.
    # With no file there, the bot has no course search and says nothing about courses.
    COURSES_FILE = os.getenv("COURSES_FILE", os.path.join(STATE_DIR, "courses.db"))
    # How long a course flag keeps course mode on for the person who used it, in seconds
    COURSE_SESSION_SECONDS = int(os.getenv("COURSE_SESSION_SECONDS", 3600))
    # The deployment that embeds a course question. It has to carry the name of the model the sync
    # embedded the material with, and the sync reads the same setting.
    EMBEDDING_DEPLOYMENT = os.getenv("EMBEDDING_DEPLOYMENT", "text-embedding-3-small").strip()
    
    # Rate limit settings by action type
    RATE_LIMITS = {
        "chat": (50, 1800),    # 50 messages per 30 minutes
        "game": (30, 1800),    # 30 game commands per 30 minutes
        "info": (5, 60),       # 5 info requests per minute
        "forget": (20, 3600),  # 20 forget requests per hour
        "default": (30, 1800), # 30 requests per 30 minutes (fallback)
        "mydata": (10, 1800),  # 10 data requests per 30 minutes
        "file": (30, 86400),   # 30 generated documents or diagrams per day
        "tldr": (6, 3600),     # 6 channel summaries per hour
        "pdf": (30, 86400),    # 30 PDFs described page by page per day. One already described is free.
        "web": (20, 86400),    # 20 web searches or opened links per day
        "course": (120, 86400),# 120 searches of the course material per day
    }
    
    # Dynamic settings like directories are initialized in a method rather than duplicated
    @classmethod
    def ensure_directories(cls):
        """Create required directories if they don't exist"""
        # lazy initialization ftw - only create the dirs when actually needed
        # this avoids file system operations during import time which can be slow
        # and might cause issues in some environments (like read-only file systems)
        for directory in (cls.USER_DATA_DIR, cls.LOG_DIR, cls.STATE_DIR):
            os.makedirs(directory, exist_ok=True)
        
    @classmethod
    def validate(cls) -> Dict[str, Any]:
        """Validate configuration and ensure directories exist"""
        # Check for required environment variables
        missing = []
        if not cls.DISCORD_TOKEN:
            missing.append("DISCORD_TOKEN")
            
        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")
        
        # Create necessary directories once during validation
        cls.ensure_directories()
        
        return {
            "discord_token": bool(cls.DISCORD_TOKEN),
            "data_dir": os.path.exists(cls.USER_DATA_DIR),
            "log_dir": os.path.exists(cls.LOG_DIR)
        } 