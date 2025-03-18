#!/usr/bin/env python3
"""
ChronoChunk Discord Bot
Main entry point to run the bot
"""
import os
import sys
import logging
from dotenv import load_dotenv

# hacky but effective - make our imports work no matter where we run from
# avoids the annoying "ModuleNotFoundError" when running from different directories
# basically tells Python "hey, look in this directory for imports too"
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables first
load_dotenv()

# Then import the rest
from src.bot import main

if __name__ == "__main__":
    try:
        # Run the bot
        main()
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1) 