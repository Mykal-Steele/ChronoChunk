"""
Check data in and out of its database. Run from the repo root, where .env is:

    uv run --project sync python -m sync.checks questions-push release1 questions.json
    uv run --project sync python -m sync.checks questions-pull release1 questions.json
    uv run --project sync python -m sync.checks run-push result.json
    uv run --project sync python -m sync.checks runs

The run itself happens in the bot's image, see run.py.
"""
import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from sync.checks import store


def main() -> None:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(prog="python -m sync.checks")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("questions-push", "questions-pull"):
        command = commands.add_parser(name)
        command.add_argument("name")
        command.add_argument("file", type=Path)
    commands.add_parser("run-push").add_argument("file", type=Path)
    commands.add_parser("runs")
    options = parser.parse_args()

    if options.command == "questions-push":
        count = store.push_questions(options.name, json.loads(options.file.read_text(encoding="utf-8")))
        print(f"stored {count} messages as {options.name}")
    elif options.command == "questions-pull":
        items = store.pull_questions(options.name)
        options.file.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {len(items)} messages to {options.file}")
    elif options.command == "run-push":
        print("stored run", store.push_run(json.loads(options.file.read_text(encoding="utf-8"))))
    else:
        for run in store.last_runs():
            report = run.get("gate", {})
            print(f"{run['saved']:%Y-%m-%d %H:%M} {run.get('label', '')}: {'PASSED' if report.get('passed') else 'held'}, "
                  f"${run.get('cost', 0):.3f}, {report.get('lines', {})}")


if __name__ == "__main__":
    main()
