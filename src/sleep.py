"""
What the bot does at night, layer 3 of docs/memory-plan.md. Once a day after 4am Bangkok:

1. Each channel's finished day with enough talk is written down as a short summary on
   the background model, and the summary is embedded so recall can find it by meaning.
2. Memories that say nearly the same thing are judged in one call: a repeat from someone
   else becomes a confirmation, and a newer correction replaces the old memory when the
   trust rules let its author do that by hand.
3. Raw channel messages older than CHANNEL_LOG_KEEP_DAYS are deleted.

Everything is costed through the shared budget guard. A spent budget ends the night early.
"""
import asyncio
import json
import logging
from datetime import date, datetime, timezone
from typing import Optional

from config.config import Config
from src.channel_log import ChannelLog
from src.clock import BANGKOK
from src.database import Database, get_database
from src.memory_store import MemoryStore, Place
from src.tools.memory import embed_missing
from src.usage_guard import BudgetExceededError
from src import usage_context

logger = logging.getLogger(__name__)

NIGHT_HOUR = 4
_LAST_DAY = "sleep_last_day"
# How much of one day goes to the model, newest kept when a day is longer
MAX_DAY_MESSAGES = 600
MAX_DAY_CHARS = 40_000
SUMMARY_MAX_TOKENS = 600
CHECK_EVERY_SECONDS = 3600

SUMMARY_PROMPT = """Below is everything said in the Discord channel #{channel} of the server {guild} on {day}, Bangkok time.
Write down what happened that day as short plain notes, at most 8 lines, each starting with "- ".
Keep what someone might ask about later: who said what about themselves, plans, dates, deadlines, decisions, questions that got answered, links that were shared.
Use people's names, never "someone". Leave out greetings and small talk unless that was all there was.
Lines from ChronoChunk are the bot's own replies. Do not treat them as facts.
Write only what the messages say. Do not guess or add anything.

{messages}"""

JUDGE_TOOL = "judge_memory_pairs"
JUDGE_PROMPT = """Each numbered pair below is two things people told a Discord bot, older first.
For each pair decide:
- same: both say the same thing
- newer_corrects: the newer one changes or corrects the older one (a new date, a new room, a changed fact)
- different: they are about different things, or both can be true
When unsure, say different.

{pairs}"""
JUDGE_DEFINITION = {
    "type": "function",
    "function": {
        "name": JUDGE_TOOL,
        "description": "Give the verdict for every numbered pair.",
        "parameters": {
            "type": "object",
            "properties": {
                "pairs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pair": {"type": "integer"},
                            "verdict": {"type": "string", "enum": ["same", "newer_corrects", "different"]},
                        },
                        "required": ["pair", "verdict"],
                    },
                },
            },
            "required": ["pairs"],
        },
    },
}


def _local_time(created_at: str) -> str:
    return datetime.fromisoformat(created_at).astimezone(BANGKOK).strftime("%H:%M")


def _day_name(day: date) -> str:
    return f"{day:%A} {day.day} {day:%b} {day.year}"


async def summarize_day(log: ChannelLog, client, model: str, embedder, channel_id: str, day: date) -> Optional[int]:
    """Write one channel's day down and keep it. Returns the summary's id, or None when nothing came back."""
    every = log.messages_on(channel_id, day)
    rows = every[-MAX_DAY_MESSAGES:]
    if not rows:
        return None
    lines = [f"{_local_time(r['created_at'])} {r['author_name']}{' (the bot)' if r['is_bot'] else ''}: {r['content']}"
             for r in rows]
    text = "\n".join(lines)[-MAX_DAY_CHARS:]
    first = rows[0]
    prompt = SUMMARY_PROMPT.format(channel=first["channel_name"], guild=first["guild_name"], day=_day_name(day),
                                   messages=text)
    with usage_context.feature("sleep"):
        response = await client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=SUMMARY_MAX_TOKENS, reasoning_effort="low")
    summary = (response.choices[0].message.content or "").strip()
    if not summary:
        logger.warning(f"Empty summary for channel {channel_id} on {day}")
        return None
    place = Place(first["guild_id"], first["guild_name"], first["channel_id"], first["channel_name"])
    # Everyone who spoke that day, so /forget finds the summary even when their messages were cut from the prompt
    people = [r["author_id"] for r in every if not r["is_bot"]]
    # Saved only if no message it was written from was deleted during the call, by /forget or in Discord
    episode_id = log.save_episode(place, day, summary, people, written_from=[r["message_id"] for r in rows])
    if episode_id is None:
        logger.info(f"The day of {channel_id} on {day} changed while it was written down, trying again next night")
        return None
    if embedder is not None:
        try:
            vector, _ = await embedder.embed(summary, "document")
            log.set_episode_embedding(episode_id, vector, embedder.model)
        except BudgetExceededError:
            raise
        except Exception as e:
            # Embedded on a later night
            logger.warning(f"Could not embed the summary of {channel_id} on {day}: {e}")
    return episode_id


def _verdicts(response) -> dict:
    calls = getattr(response.choices[0].message, "tool_calls", None) or []
    for call in calls if isinstance(calls, list) else []:
        if call.function.name != JUDGE_TOOL:
            continue
        try:
            items = json.loads(call.function.arguments).get("pairs") or []
        except (ValueError, AttributeError):
            return {}
        return {int(i["pair"]): i.get("verdict") for i in items if isinstance(i, dict) and "pair" in i}
    return {}


async def tidy_memories(store: MemoryStore, client, model: str, embedding_model: str) -> dict:
    """Judge close pairs of memories in one call and apply the verdicts. Returns what was done."""
    pairs = store.pairs_to_tidy(embedding_model)
    result = {"checked": 0, "merged": 0, "settled": 0}
    if not pairs:
        return result
    listed = "\n".join(
        f"{n}. older ({older.source_name}, {older.created_at:%d %b}): {older.content}\n"
        f"   newer ({newer.source_name}, {newer.created_at:%d %b}): {newer.content}"
        for n, (older, newer) in enumerate(pairs, 1))
    with usage_context.feature("sleep"):
        response = await client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": JUDGE_PROMPT.format(pairs=listed)}],
            tools=[JUDGE_DEFINITION], tool_choice={"type": "function", "function": {"name": JUDGE_TOOL}},
            max_completion_tokens=200 + 30 * len(pairs), reasoning_effort="low")
    verdicts = _verdicts(response)
    if not verdicts:
        logger.warning("The memory judge gave no usable verdicts")
        return result
    for n, (older, newer) in enumerate(pairs, 1):
        verdict = verdicts.get(n)
        if verdict not in ("same", "newer_corrects", "different"):
            continue
        result["checked"] += 1
        store.pair_checked(older.id, newer.id)
        if verdict == "same" and store.merge(older, newer):
            result["merged"] += 1
        elif verdict == "newer_corrects" and store.settle(older, newer):
            result["settled"] += 1
    return result


def due(database: Database, now: Optional[datetime] = None) -> bool:
    """True once a day, from NIGHT_HOUR Bangkok time on."""
    local = (now or datetime.now(timezone.utc)).astimezone(BANGKOK)
    return local.hour >= NIGHT_HOUR and database.get_meta(_LAST_DAY) != local.date().isoformat()


async def sleep_once(database: Database, client, model: str, embedder, today: date) -> dict:
    """One night: summaries, then tidying, then pruning. Marks the day done even when the budget ran out."""
    log, store = ChannelLog(database), MemoryStore(database)
    result = {"summaries": 0, "merged": 0, "settled": 0, "pruned": 0}
    try:
        for channel_id, day in log.days_to_summarize(today):
            if await summarize_day(log, client, model, embedder, channel_id, day) is not None:
                result["summaries"] += 1
        if embedder is not None:
            await embed_missing(store, embedder)
            for row in log.missing_episode_embeddings(embedder.model):
                vector, _ = await embedder.embed(row["summary"], "document")
                log.set_episode_embedding(row["id"], vector, embedder.model)
            tidied = await tidy_memories(store, client, model, embedder.model)
            result["merged"], result["settled"] = tidied["merged"], tidied["settled"]
    except BudgetExceededError as e:
        logger.warning(f"The night stopped early, the budget is spent: {e}")
    except Exception as e:
        logger.error(f"The night stopped early: {e!r}")
    result["pruned"] = log.prune(today, Config.CHANNEL_LOG_KEEP_DAYS)
    database.set_meta(_LAST_DAY, today.isoformat())
    return result


async def run_nightly(handler) -> None:
    """Check every hour, and run the night once it is due. handler is the bot's AIResponseHandler."""
    while True:
        try:
            database = get_database()
            now = datetime.now(timezone.utc)
            if due(database, now):
                model = Config.AI_BACKGROUND_DEPLOYMENT or handler.deployment
                result = await sleep_once(database, handler.ai_client, model, handler.memory_embedder,
                                          now.astimezone(BANGKOK).date())
                logger.info(f"Night done: {result}")
        except Exception as e:
            logger.error(f"Nightly memory job failed: {e!r}")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
