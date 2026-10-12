"""
What the sync is set to: where it keeps its files, which semester it reads, which
models it uses and how much one run may spend. Everything can be changed from the
environment. Nothing here needs the browser, so every part of the sync can import it.
"""
import os
from pathlib import Path

# expanduser leaves the path as it is where there is no home folder, which Path.home() does not survive
HOME = Path(os.getenv("CHRONOCHUNK_SYNC_HOME") or os.path.expanduser("~/.local/share/chronochunk-sync"))
# Downloaded documents, named by their id in SharePoint
FILES_DIR = HOME / "files"
# What earlier runs already read, translated and embedded
CACHE_FILE = HOME / "cache.db"
# The finished course file. The run before it is kept next to it as courses.db.previous.
OUTPUT_FILE = HOME / "courses.db"

# Where the bot runs, as user@host, and the file it reads there. With no server set, --upload refuses to run.
SERVER = os.getenv("CHRONOCHUNK_SERVER", "")
SERVER_FILE = os.getenv("CHRONOCHUNK_SERVER_FILE", "/opt/chronochunk/data/state/courses.db")

SHAREPOINT = os.getenv("SHAREPOINT_URL", "https://kmuttsit.sharepoint.com")
# Class teams of this semester start with this
COURSE_PREFIX = os.getenv("COURSE_PREFIX", "1-2569")
# Files and posts last changed before this day belong to an older semester
SEMESTER_START = os.getenv("SEMESTER_START", "2026-07-01")

# Deployments on the bot's Azure resource
CHAT_MODEL = os.getenv("SYNC_CHAT_DEPLOYMENT", "gpt-6-luna")
EMBEDDING_MODEL = os.getenv("EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
# USD per million tokens: (input, output). The embedding price is Azure's global list price.
PRICES = {
    "gpt-6-luna": (0.10, 0.50),
    "text-embedding-3-small": (0.02, 0.0),
}
# One run stops before it spends more than this many USD. Its calls are made from
# the PC, so the bot's own monthly limit does not see them.
SPEND_CAP = float(os.getenv("SYNC_SPEND_CAP", "2"))

# How many files download at once, and how many requests run at once inside a SharePoint page
DOWNLOADS_AT_ONCE = 6
REQUESTS_AT_ONCE = 8
