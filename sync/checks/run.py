"""
One check run: every message of a question set goes through the bot's real reply path,
one at a time, and is graded. It runs in the bot's image, because it uses the bot's code:

    docker run --rm --env-file .env -e CHECK_DEPLOYMENT=gpt-6-luna-checks \\
        -v "$PWD":/app:ro -v <scratch>:/scratch -v <scratch>/logs:/app/logs -w /app chronochunk:dev \\
        python -m sync.checks.run /scratch/questions.json /scratch/courses.db /scratch/result.json "<label>"

It never runs on the deployment the live bot uses, and it refuses to start unless
run.md has the run written down first.
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List

from sync.checks.grading import FLAGGED, gate, grade, parse_verdict

# The most characters of looked-up material the judge reads for one message
JUDGE_MATERIAL_CHARS = 30000
# Seconds to wait before asking again after the model's rate limit was hit
RATE_LIMIT_PAUSE = 30
ASKER = "Kruskal"

JUDGE_PROMPT = """You grade one reply of a study assistant. The assistant was shown MATERIAL from a university course \
(numbered passages from slides, lecture recordings and assignments, and sometimes an assignment list marked [A]) and \
answered a student's MESSAGE. Judge the REPLY only by the MATERIAL and the REFERENCE, never by what you know yourself.

Answer with one JSON object and nothing else:
{{"verdict": "...", "why": "one short sentence", "passages": {{"1": "answers", "2": "related", "3": "unrelated"}}}}

verdict is exactly one of:
- "correct": what the reply says about the course is what the material says, and it answers the message.
- "incomplete": right as far as it goes, but it leaves out part of what the message asked and the material has.
- "wrong_right_place": the material has the answer, the reply used that passage and still states it wrongly.
- "wrong_place": the reply answers from material that is not about what was asked, while the answer is elsewhere or nowhere.
- "not_found": the reply says it could not find it. Offering the closest thing, clearly called that, still counts as this.
- "made_up": the reply states, as a fact of this course, something no passage or list it was shown supports: what a \
slide, a teacher or an assignment said, a date, a number of points, a requirement, a name.

REFERENCE lists what a right answer contains, when that is known. If it is empty, go by the material alone. If the \
reply contradicts the reference, it is not correct.

Judge what the reply says about the course in answer to the message. These are not claims to check, and never make \
a reply "made_up": a joke or a side remark, what a course is called (see COURSES), and a general explanation of the \
subject or an everyday example that agrees with the material. Where a slide and a lecture recording differ, the slide \
is what the course says, since recordings are transcribed by machine. Before you say "made_up", read the material \
again for the exact thing you think is missing: a line of an assignment, a table, a schedule or a list in a passage \
counts. When you do say it, "why" has to quote the unsupported claim.

COURSES:
{courses}

passages has one entry for every numbered passage in the material: "answers" when it answers the message, "related" \
when it is on the topic without answering it, "unrelated" otherwise.

MESSAGE:
{message}

REFERENCE:
{reference}

MATERIAL:
{material}

REPLY:
{reply}
"""


def _refuse(why: str) -> None:
    raise SystemExit(f"The check did not start: {why}")


def _guard() -> str:
    deployment = os.environ.get("CHECK_DEPLOYMENT", "")
    live = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-6-luna")
    if not deployment.endswith("-checks") or deployment == live:
        _refuse("CHECK_DEPLOYMENT has to name the checks deployment, never the one the live bot uses")
    log = Path("run.md").read_text(encoding="utf-8") if Path("run.md").exists() else ""
    running = log.split("## Running", 1)[1].split("## Done", 1)[0] if "## Running" in log else ""
    if "check" not in running.lower():
        _refuse("run.md has no row for this check under Running. Write it down first, with the estimated cost")
    return deployment


async def run(cases: List[Dict], courses_file: str, label: str, deployment: str) -> Dict:
    from config.config import Config
    Config.STATE_DIR = tempfile.mkdtemp(prefix="check-state-")
    Config.COURSES_FILE = courses_file
    Config.AI_CHAT_REASONING_EFFORT = "none"
    Config.AI_UNLIMITED_USER_IDS = {"999"}
    Config.AI_DAILY_BUDGET_USD = 5

    import src.ai_response_handler as handler_module
    from src import course_retrieval
    from src.ai_response_handler import AIResponseHandler
    from src.tools import courses
    from src.tools.courses import SearchCourse

    seen: Dict[str, List] = {"lookups": [], "searches": [], "scores": []}
    # Every block of material the model was shown for the case being run, which is what the judge reads
    shown: List[str] = []
    rate_limited = [False]

    lookup_for = handler_module.lookup_for

    async def counted_lookup(turn, scope):
        text = await lookup_for(turn, scope)
        seen["lookups"].append(scope.codes)
        shown.append(text)
        return text
    handler_module.lookup_for = counted_lookup

    search = SearchCourse.run

    async def counted_search(self, args, turn):
        result = await search(self, args, turn)
        seen["searches"].append(args)
        shown.append(result.text)
        return result
    SearchCourse.run = counted_search  # type: ignore[method-assign]

    retrieve = course_retrieval.retrieve

    async def scored_retrieve(*args, **kwargs):
        found = await retrieve(*args, **kwargs)
        first = kwargs.get("first_number", 1)
        seen["scores"].extend({"number": str(number), "similarity": hit.similarity, "kind": hit.kind}
                              for number, hit in enumerate(found.hits, first))
        return found
    courses.retrieve = scored_retrieve  # type: ignore[assignment]

    costs: Dict[str, float] = {"total": 0.0, "last": 0.0, "calls": 0}
    log_reply = AIResponseHandler._log_reply

    def counted_log(request, reply):
        costs["last"], costs["calls"] = request.cost, request.calls
        log_reply(request, reply)
    AIResponseHandler._log_reply = staticmethod(counted_log)  # type: ignore[method-assign]

    class Watch(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if "rate limit" in record.getMessage().lower():
                rate_limited[0] = True
    logging.getLogger().addHandler(Watch())

    handler = AIResponseHandler(os.environ["AZURE_OPENAI_ENDPOINT"], os.environ["AZURE_OPENAI_KEY"], deployment)
    held = courses.course_store()
    course_list = "\n".join(f"- {course['code']} {course['name']}, also called: {', '.join(course['aliases']) or 'nothing else'}"
                            for course in (held.courses() if held else []))

    async def ask(message: str, history: str, key: str) -> str:
        for _ in range(4):
            rate_limited[0] = False
            for kind in seen.values():
                kind.clear()
            reply = await handler.generate_reply(message, history, ASKER, "999", allow_files=True, session_key=key)
            if not rate_limited[0]:
                return reply.text
            await asyncio.sleep(RATE_LIMIT_PAUSE)
        return reply.text

    async def judge(case: Dict, material: str, reply: str, again: bool = False) -> Dict:
        reference = "\n".join(f"- {fact}" for fact in case.get("facts") or case.get("expect_source") or []) or "(none)"
        prompt = JUDGE_PROMPT.format(message=case["messages"][-1], reference=reference, courses=course_list,
                                     material=material[:JUDGE_MATERIAL_CHARS] or "(nothing was looked up)", reply=reply)
        for _ in range(4):
            try:
                answer = await handler.ai_client.chat.completions.create(
                    model=deployment, messages=[{"role": "user", "content": prompt}], max_completion_tokens=3000,
                    reasoning_effort="medium")
            except Exception as error:
                if "429" not in str(error):
                    return {"verdict": "unreadable", "why": str(error)[:200], "passages": {}}
                await asyncio.sleep(RATE_LIMIT_PAUSE)
                continue
            from src.usage_guard import UsageGuard
            costs["total"] += UsageGuard.cost_of(getattr(answer, "usage", None), deployment)
            verdict = parse_verdict(answer.choices[0].message.content or "")
            if verdict["verdict"] == "made_up" and not again:
                # The judge is a model too. Its heaviest verdict has to hold up a second time.
                second = await judge(case, material, reply, again=True)
                return second if second["verdict"] != "made_up" else verdict
            return verdict
        return {"verdict": "unreadable", "why": "rate limited four times", "passages": {}}

    results = []
    started = time.monotonic()
    for case in cases:
        key = f"check:{case['id']}"
        history = ""
        reply = ""
        shown.clear()
        asked_at = time.monotonic()
        for message in case["messages"]:
            reply = await ask(message, history, key)
            costs["total"] += costs["last"]
            history += f"{ASKER}: {message.lstrip('/!')}\nYOU (ChronoChunk): {reply}\n"
            await asyncio.sleep(1)
        seconds = time.monotonic() - asked_at
        material = "\n\n".join(shown)
        outcome: Dict = {"reply": reply, "lookups": len(seen["lookups"]), "searches": len(seen["searches"]),
                         "sources": [material]}
        verdict = {"verdict": None, "why": "", "passages": {}}
        if case["group"] in FLAGGED:
            verdict = await judge(case, material, reply)
            outcome["verdict"], outcome["passages"] = verdict["verdict"], verdict["passages"]
        graded = grade(case, outcome)
        graded["why"] = graded["why"] or verdict["why"]
        results.append({**graded, "id": case["id"], "group": case["group"], "messages": case["messages"], "reply": reply,
                        "lookups": outcome["lookups"], "searches": list(seen["searches"]), "scopes": list(seen["lookups"]),
                        "scores": list(seen["scores"]), "passages": verdict["passages"], "model_calls": costs["calls"],
                        "cost": costs["last"], "seconds": round(seconds, 1), "material": material})
        mark = "pass" if graded["ok"] else "MISS"
        print(f"{mark} [{case['group']}] {case['messages'][-1][:60]!r} {graded['verdict'] or ''} "
              f"{'' if graded['right_place'] else 'WRONG PLACE '}{graded['why'][:110]}", flush=True)
        if not graded["ok"]:
            print("      => " + reply[:300].replace("\n", " | "), flush=True)

    report = gate(results)
    flagged = sorted(r["seconds"] for r in results if r["group"] in FLAGGED)
    return {"label": label, "deployment": deployment, "messages": len(results), "gate": report, "cost": round(costs["total"], 4),
            "minutes": round((time.monotonic() - started) / 60, 1),
            "median_seconds": flagged[len(flagged) // 2] if flagged else 0, "results": results}


def main(questions_file: str, courses_file: str, out_file: str, label: str) -> None:
    deployment = _guard()
    cases = json.loads(Path(questions_file).read_text(encoding="utf-8"))
    done = asyncio.run(run(cases, courses_file, label, deployment))
    Path(out_file).write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    report = done["gate"]
    print("\n" + ("GATE PASSED" if report["passed"] else "GATE HELD: " + ", ".join(report["failed"])))
    for line, value in report["lines"].items():
        print(f"  {line}: {value}")
    print(f"  cost ${done['cost']}, {done['minutes']} minutes, median reply {done['median_seconds']}s")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
