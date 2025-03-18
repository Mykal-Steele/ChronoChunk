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
    COMMAND_PREFIX = "/"
    
    # AI model settings
    AI_MODEL = "gemini-2.0-flash-lite"  # Consistent AI model for all interactions
    FACT_MODEL = "gemini-2.0-flash-lite"  # Model used for fact extraction
    
    # Rate limiting settings
    RATE_LIMIT_WINDOW = 30 * 60  # 30 minutes in seconds
    RATE_LIMIT_MAX_MESSAGES = 50  # 50 messages per window
    RATE_LIMIT_CLEANUP_INTERVAL = 60 * 60  # Clean up old entries every hour
    
    # Memory and history settings
    MEMORY_SIZE = 30  # Remember last 30 messages in conversation context
    MAX_CONVERSATION_HISTORY = 20  # Keep 20 most recent conversations in user data
    CHANNEL_HISTORY_SIZE = 40  # Number of recent messages to keep in channel history
    DISPLAY_CONTEXT_SIZE = 20  # Number of messages to include in conversation context
    
    # Game settings
    MAX_GAME_ATTEMPTS = 10  # Maximum number of attempts in the guessing game
    
    # Paths
    BASE_DIR = Path(__file__).parent.parent
    USER_DATA_DIR = os.path.join(BASE_DIR, "user_data")
    LOG_DIR = os.path.join(BASE_DIR, "logs")
    LOG_FILE = os.path.join(LOG_DIR, "bot.log")
    
    # Web server settings for health checks
    WEB_HOST = "0.0.0.0"
    WEB_PORT = int(os.getenv("PORT", 10000))
    
    # Rate limit settings by action type
    RATE_LIMITS = {
        "chat": (50, 1800),    # 50 messages per 30 minutes
        "game": (30, 1800),    # 30 game commands per 30 minutes
        "info": (5, 60),       # 5 info requests per minute
        "forget": (20, 3600),  # 20 forget requests per hour
        "default": (30, 1800), # 30 requests per 30 minutes (fallback)
        "mydata": (10, 1800),  # 10 data requests per 30 minutes
    }
    
    # Important topics requiring special handling
    IMPORTANT_TOPICS = [
        # these are sensitive topics where we need to be extra careful
        # the bot needs to recognize when a conversation touches on these areas
        # so it can give appropriate warnings and handle with sensitivity
        
        # Mental health topics
        "depression", "suicide", "self-harm", "harm", "death", 
        "violence", "abuse", "mental health", "anxiety", "trauma",
        "sadness", "loneliness", "stress", "therapy", "medication",
        "drugs", "addiction", "eating disorder", "bipolar", "schizophrenia",
        
        # Sensitive social topics
        "race", "racism", "discrimination", "politics", "religion",
        "sexuality", "gender", "transgender", "homophobia", "feminism",
        "abortion", "guns", "terrorism", "war", "protests", "riots",
        
        # Family and relationship issues
        "divorce", "breakup", "cheating", "dating", "marriage", "family",
        "parents", "abuse", "domestic violence", "assault", "rape",
        
        # Health concerns
        "cancer", "disease", "illness", "health", "disability", "pain",
        "hospital", "surgery", "emergency", "accident", "injury",
        
        # Financial/life struggles
        "money", "debt", "poverty", "homeless", "unemployment", "job loss",
        "eviction", "bankruptcy", "financial"
    ]
    
    # Dynamic settings like directories are initialized in a method rather than duplicated
    @classmethod
    def ensure_directories(cls):
        """Create required directories if they don't exist"""
        # lazy initialization ftw - only create the dirs when actually needed
        # this avoids file system operations during import time which can be slow
        # and might cause issues in some environments (like read-only file systems)
        for directory in (cls.USER_DATA_DIR, cls.LOG_DIR):
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