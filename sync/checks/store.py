"""
Where check data lives: question sets written from the real course material, and the
result of every run. It is kept in a MongoDB database of its own (MONGODB_URI in .env,
with the database name in it), never in the repo, because it holds real course content.
"""
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

QUESTIONS = "questions"
RUNS = "runs"


def database() -> Any:
    # Imported here, so grading and the runner load without the database package
    from pymongo import MongoClient

    address = os.environ.get("MONGODB_URI")
    if not address:
        raise SystemExit("MONGODB_URI is not set. Put it in .env, with the database name in the address.")
    return MongoClient(address, serverSelectionTimeoutMS=20000).get_default_database()


def push_questions(name: str, items: List[Dict]) -> int:
    """Store a question set under a name, replacing the one that had it."""
    database()[QUESTIONS].replace_one(
        {"_id": name}, {"_id": name, "items": items, "saved": datetime.now(timezone.utc)}, upsert=True)
    return len(items)


def pull_questions(name: str) -> List[Dict]:
    found = database()[QUESTIONS].find_one({"_id": name})
    if not found:
        raise SystemExit(f"no question set called {name!r}")
    return found["items"]


def push_run(run: Dict) -> str:
    """Store one run: its gate report, its cost and every graded message."""
    return str(database()[RUNS].insert_one(dict(run, saved=datetime.now(timezone.utc))).inserted_id)


def last_runs(count: int = 5) -> List[Dict]:
    return list(database()[RUNS].find({}, {"results": 0}).sort("saved", -1).limit(count))
