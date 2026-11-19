"""
What /memory shows and deletes, layer 5 of docs/memory-plan.md.

Everyone can see what the bot knows about them and delete one thing at a time: what they
taught it, what someone told it about them, the lessons about them, and the day summaries
they are in. A server manager can see what the bot learned in their server and delete any
of it. /forget still deletes everything about someone at once.

Each item has a code: m12 is memory #12, l3 is lesson #3 and d5 is the summary of a day.
"""
import json
import re
from datetime import datetime
from typing import List, Optional

from src.channel_log import ChannelLog
from src.clock import days_ago
from src.lessons import LessonStore
from src.memory_store import MemoryStore

# Discord's limit for one message is 2000 characters
MAX_CHARS = 1900
LINE_CHARS = 160


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= LINE_CHARS else text[:LINE_CHARS - 3] + "..."


def _fit(sections: List[tuple]) -> str:
    """Sections of (title, lines) as one message under the Discord limit, cutting lists with 'and N more'."""
    out: List[str] = []
    for title, lines in sections:
        if not lines:
            continue
        out.append(f"**{title}**")
        for n, line in enumerate(lines):
            left = len(lines) - n
            if len("\n".join(out)) + len(line) + 40 > MAX_CHARS:
                out.append(f"...and {left} more")
                break
            out.append(line)
    return "\n".join(out)


def _memory_line(row) -> str:
    where = f"{row['guild_name']} #{row['channel_name']}" if row["guild_name"] else "a DM"
    return f"`m{row['id']}` {row['source_name']} told me, {days_ago(datetime.fromisoformat(row['created_at']))}, in {where}: {_short(row['content'])}"


def about_me(user_id: str, username: str) -> str:
    memories, lessons, log = MemoryStore(), LessonStore(), ChannelLog()
    taught = memories.db.query("SELECT * FROM memories WHERE replaced_by IS NULL AND source_id = ? ORDER BY id DESC",
                               (str(user_id),))
    about = memories.db.query(
        "SELECT * FROM memories WHERE replaced_by IS NULL AND source_id != ? AND about = ? COLLATE NOCASE ORDER BY id DESC",
        (str(user_id), username))
    taught_lessons = lessons.db.query(
        "SELECT * FROM lessons WHERE (scope = 'person' AND target_id = ?) OR source_id = ? ORDER BY id DESC",
        (str(user_id), str(user_id)))
    messages = log.db.query("SELECT count(*) FROM channel_messages WHERE author_id = ?", (str(user_id),))[0][0]
    days = log.db.query("SELECT count(*) FROM episodes WHERE people LIKE ?", (f'%"{user_id}"%',))[0][0]
    if not (taught or about or taught_lessons or messages or days):
        return "i know nothing about u yet"
    counts = []
    if messages:
        counts.append(f"{messages} message{'s' if messages != 1 else ''} of urs from server channels, kept 30 days")
    if days:
        counts.append(f"{days} day summar{'ies' if days != 1 else 'y'} u are in")
    sections = [
        ("what u taught me", [_memory_line(r) for r in taught]),
        ("what others told me about u", [_memory_line(r) for r in about]),
        ("how u asked me to act", [f"`l{r['id']}` {'with u' if r['scope'] == 'person' else 'in ' + (r['guild_name'] or 'a server')}: {_short(r['lesson'])}"
                                   for r in taught_lessons]),
        ("also kept", counts),
    ]
    return _fit(sections) + "\n-# `/memory delete:m12` deletes one thing, `/forget` deletes everything"


def about_server(guild_id: str) -> str:
    memories, lessons, log = MemoryStore(), LessonStore(), ChannelLog()
    taught = memories.db.query("SELECT * FROM memories WHERE replaced_by IS NULL AND guild_id = ? ORDER BY id DESC",
                               (str(guild_id),))
    server_lessons = lessons.db.query("SELECT * FROM lessons WHERE scope = 'server' AND target_id = ? ORDER BY id DESC",
                                      (str(guild_id),))
    days = log.db.query("SELECT * FROM episodes WHERE guild_id = ? ORDER BY day DESC", (str(guild_id),))
    if not (taught or server_lessons or days):
        return "i havent learned anything in this server yet"
    sections = [
        ("what people taught me here", [_memory_line(r) for r in taught]),
        ("how this server asked me to act", [f"`l{r['id']}` {_short(r['lesson'])} ({r['source_name']} asked)" for r in server_lessons]),
        ("days i wrote down", [f"`d{r['id']}` {r['day']} #{r['channel_name']}: {_short(r['summary'])}" for r in days]),
    ]
    return _fit(sections) + "\n-# `/memory delete:m12` deletes one thing"


def _parse(code: str) -> Optional[tuple]:
    match = re.fullmatch(r"\s*([mld])(\d+)\s*", (code or "").lower())
    return (match.group(1), int(match.group(2))) if match else None


def delete_item(code: str, user_id: str, guild_id: str, is_admin: bool, username: str = "") -> str:
    """Delete one memory, lesson or day summary when this person may. Returns what to tell them."""
    parsed = _parse(code)
    if parsed is None:
        return "thats not a number i know. use the code from `/memory`, like m12, l3 or d5"
    kind, number = parsed
    user_id, guild_id = str(user_id), str(guild_id or "")
    if kind == "m":
        store = MemoryStore()
        rows = store.db.query("SELECT * FROM memories WHERE id = ?", (number,))
        if not rows:
            return f"theres no memory m{number}"
        row = rows[0]
        mine = row["source_id"] == user_id or (username and row["about"].lower() == username.lower())
        if not (mine or (is_admin and guild_id and row["guild_id"] == guild_id)):
            return "u cant delete that one. only who taught it, who its about, or a manager of the server it was taught in can"
        store.delete(number)
        return f"deleted m{number}"
    if kind == "l":
        lessons = LessonStore()
        rows = lessons.db.query("SELECT * FROM lessons WHERE id = ?", (number,))
        if not rows:
            return f"theres no lesson l{number}"
        row = rows[0]
        mine = row["source_id"] == user_id or (row["scope"] == "person" and row["target_id"] == user_id)
        managed = is_admin and guild_id and (row["guild_id"] == guild_id or (row["scope"] == "server" and row["target_id"] == guild_id))
        if not (mine or managed):
            return "u cant delete that one. only who its about, who asked for it, or a manager of that server can"
        lessons.delete(number)
        return f"deleted l{number}"
    log = ChannelLog()
    rows = log.db.query("SELECT * FROM episodes WHERE id = ?", (number,))
    if not rows:
        return f"theres no day summary d{number}"
    row = rows[0]
    if not (user_id in json.loads(row["people"]) or (is_admin and guild_id and row["guild_id"] == guild_id)):
        return "u cant delete that one. only someone who talked that day or a manager of that server can"
    log.delete_episode(number)
    return f"deleted d{number}"
