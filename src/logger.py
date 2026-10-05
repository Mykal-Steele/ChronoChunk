import logging
import os
from datetime import datetime
from config.config import Config
from src.logging_utils import SafeStreamHandler  # canonical definition lives there

# set up logging directory
os.makedirs(Config.LOG_DIR, exist_ok=True)

# create logger
logger = logging.getLogger("chronochunk")
logger.setLevel(logging.INFO)

# create file handler - UTF-8 encoding for log files
log_file = os.path.join(Config.LOG_DIR, f"bot_{datetime.now().strftime('%Y%m%d')}.log")
file_handler = logging.FileHandler(log_file, encoding='utf-8')
file_handler.setLevel(logging.INFO)

# create console handler (using our safe handler)
console_handler = SafeStreamHandler()
console_handler.setLevel(logging.INFO)

# create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# add handlers to logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)