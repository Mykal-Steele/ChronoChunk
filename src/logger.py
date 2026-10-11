"""One logging setup for the whole bot: the console, and a log file that starts fresh each day."""
import logging
import os
from logging.handlers import TimedRotatingFileHandler

from config.config import Config
from src.logging_utils import SafeStreamHandler

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Modules that import this logger write through the root handlers set up below
logger = logging.getLogger("chronochunk")


def setup_logging() -> None:
    """Send every log line to the console and to logs/chronochunk.log. Call once, at startup."""
    os.makedirs(Config.LOG_DIR, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)

    # A new file at midnight UTC. Files older than LOG_RETENTION_DAYS are deleted.
    file_handler = TimedRotatingFileHandler(
        os.path.join(Config.LOG_DIR, "chronochunk.log"), when="midnight", utc=True,
        backupCount=Config.LOG_RETENTION_DAYS, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    console_handler = SafeStreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [file_handler, console_handler]
