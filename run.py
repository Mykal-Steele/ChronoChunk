#!/usr/bin/env python3
"""
ChronoChunk Discord Bot
Main entry point to run the bot with production-ready features for 24/7 operation
"""
import os
import sys
import signal
import logging
import traceback
import time
import platform
from datetime import datetime
from dotenv import load_dotenv

# Import our logging utils before setting up any loggers
from src.logging_utils import patch_all_loggers

# Configure logging to file AND console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"logs/chronochunk_{datetime.now().strftime('%Y%m%d')}.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# Patch all loggers to handle Unicode characters
patch_all_loggers()

logger = logging.getLogger("ChronoChunk")

# Create logs directory if it doesn't exist
os.makedirs("logs", exist_ok=True)

# Add project directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Store start time for uptime tracking
START_TIME = time.time()

# Load environment variables first
load_dotenv()

# Track if shutdown is requested
shutdown_requested = False

def signal_handler(sig, frame):
    """Handle termination signals gracefully"""
    global shutdown_requested
    
    if not shutdown_requested:
        logger.info(f"Received signal {sig}, initiating graceful shutdown...")
        shutdown_requested = True
        
        # We'll implement this in bot.py
        try:
            from src.bot import shutdown_bot
            shutdown_bot()
        except ImportError:
            logger.error("Could not import shutdown_bot function")
    else:
        logger.warning(f"Received second signal, forcing exit")
        sys.exit(0)

# Register signal handlers - platform specific  
signal.signal(signal.SIGINT, signal_handler)   # Handle Ctrl+C (works on all platforms)
signal.signal(signal.SIGTERM, signal_handler)  # Handle systemd stop

# Register UNIX-specific signals only on UNIX platforms
if platform.system() != "Windows":  # Check for non-Windows platforms
    try:
        signal.signal(signal.SIGHUP, signal_handler)   # Handle terminal window closing
    except AttributeError:
        # This should never happen, but just in case
        logger.warning("SIGHUP signal not available on this platform")

if __name__ == "__main__":
    try:
        # Log startup info
        logger.info(f"=== ChronoChunk Discord Bot Starting ===")
        logger.info(f"Platform: {platform.system()} {platform.release()}")
        logger.info(f"Python version: {sys.version}")
        logger.info(f"Current directory: {os.getcwd()}")
        
        # Then import the rest
        from src.bot import main
        
        # Run the bot
        main()
    except Exception as e:
        # Get detailed traceback info
        error_type = type(e).__name__
        tb = traceback.extract_tb(sys.exc_info()[2])
        error_file = tb[-1].filename
        error_line = tb[-1].lineno
        error_func = tb[-1].name
        
        # Log with file path and line number
        error_msg = f"Fatal error in {error_file}:{error_line} (function: {error_func}): {error_type}: {e}"
        logger.critical(error_msg)
        logger.critical("Full traceback:")
        logger.critical(traceback.format_exc())
        
        sys.exit(1)