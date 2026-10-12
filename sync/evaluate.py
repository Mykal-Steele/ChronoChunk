"""
Scores search on a course file with a set of questions, the way the bot will use it:
the chat model is given the search tool and the question as a chat message, writes the
query, and the query goes through the store. A question passes when one of the results
(with the chunk before and after it) contains an expected phrase. Questions marked
"absent" pass when nothing comes back or the model does not search.

    uv run --project sync python -m sync.evaluate courses.db sync/probes/v10_questions.json

Rerun it after any change to chunking, the reader, the search or the tool's wording.
"""
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

from src.course_store import CourseStore
from sync.cache import Cache
from sync.models import Models
from sync.settings import CACHE_FILE, CHAT_MODEL

# The wording the bot's tool will carry. The V10 probe showed the query description decides
# whether a Thai question is translated, so it is kept here to be tested as it ships.
SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_course",
        "description": "Search the user's university course material: slides, lecture transcripts, assignments and posts.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": ("The topic to look for, written in English even when the user wrote in another language. "
                                    "A short phrase of the topic's own words, ten words at most. Leave out words such as "
                                    "lecture, slide, teacher or explain, and do not add possible answers."),
                },
            },
            "required": ["query"],
        },
    },
}
SYSTEM = "You are a casual discord bot for a friend group. You can search their course material with the tool."


def write_query(models: Models, question: str) -> Optional[str]:
    """The query the chat model writes for this message, None when it does not search."""
    models._check()
    reply = models.client.chat.completions.create(
        model=CHAT_MODEL, reasoning_effort="none", tools=[SEARCH_TOOL], tool_choice="auto",
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}])
    models._charge(CHAT_MODEL, reply.usage)
    calls = reply.choices[0].message.tool_calls
    return json.loads(calls[0].function.arguments).get("query") if calls else None


def main(store_file: Path, questions_file: Path) -> None:
    questions = json.loads(questions_file.read_text(encoding="utf-8"))
    store = CourseStore.open(str(store_file))
    cache = Cache(CACHE_FILE)
    models = Models(cache)
    tally: dict = defaultdict(lambda: [0, 0])
    misses = []
    search_ms = []
    for question in questions:
        query = write_query(models, question["question"])
        hits = []
        if query:
            vector = models.embed([query])[0]
            started = time.monotonic()
            hits = store.search(query, vector, course=question.get("course"))
            search_ms.append((time.monotonic() - started) * 1000)
        if question.get("absent"):
            passed = not hits
        else:
            found = " ".join(f"{hit.before} {hit.text} {hit.after}" for hit in hits).lower()
            passed = any(phrase.lower() in found for phrase in question["expect"])
        group = "absent" if question.get("absent") else question["language"]
        tally[group][0] += passed
        tally[group][1] += 1
        if not passed:
            misses.append(f"{group}: {question['question'][:50]} => {query!r}, {len(hits)} results, "
                          f"top {[(hit.title[:24], hit.locator) for hit in hits[:2]]}")
    print(", ".join(f"{group} {good}/{total}" for group, (good, total) in tally.items()))
    for miss in misses:
        print(f"  missed {miss}")
    if search_ms:
        print(f"median search {sorted(search_ms)[len(search_ms) // 2]:.1f} ms, model calls {models.calls}, about ${models.spent:.4f}")
    cache.close()
    store.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
