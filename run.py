#!/usr/bin/env python3
"""
ChronoChunk Discord Bot
Main entry point to run the bot
"""
import os
import sys
import logging
import traceback  # Add this import
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
        # Get detailed traceback info
        error_type = type(e).__name__
        tb = traceback.extract_tb(sys.exc_info()[2])
        file_name = tb[-1].filename
        line_num = tb[-1].lineno
        code_name = tb[-1].name
        
        # Log with file path and line number
        error_msg = f"Fatal error in {file_name}:{line_num} (function: {code_name}): {error_type}: {e}"
        logging.error(error_msg)
        
        # Also print to console for immediate visibility
        print(f"\nERROR: {error_msg}\n")
        
        # Print full traceback for debugging
        print("Full traceback:")
        traceback.print_exc()
        
        sys.exit(1)