"""
Run the course sync:

    uv run --project sync python -m sync                  # every course of the semester
    uv run --project sync python -m sync CSC340 CSC345    # only these, into courses-trial.db
    uv run --project sync python -m sync --upload         # and put the result on the bot's server

Run it from the repo root, where the bot's .env is. Sign in once before the first run
with `python -m sync.session`.
"""
import argparse
import logging
import sys
from pathlib import Path

from sync.build import BadRun, Report, build, collect
from sync.cache import Cache
from sync.models import Models, SpendCapReached
from sync.settings import CACHE_FILE, OUTPUT_FILE, SERVER, SERVER_FILE, SPEND_CAP, TRIAL_FILE
from sync.upload import UploadFailed, upload


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m sync", description="Collect course material into courses.db")
    parser.add_argument("courses", nargs="*", help="course codes to sync, all of them when none is given")
    parser.add_argument("--out", type=Path, help=f"where to write the course file (default {OUTPUT_FILE}, "
                                                 f"or {TRIAL_FILE.name} when courses are named)")
    parser.add_argument("--force", action="store_true", help="swap the new file in even when it lost much of the old one")
    parser.add_argument("--window", action="store_true", help="show the browser while it works")
    parser.add_argument("--upload", action="store_true", help="put the finished file on the server the bot runs on")
    parser.add_argument("--cap", type=float, default=SPEND_CAP, help=f"USD this run may spend on models (default {SPEND_CAP})")
    options = parser.parse_args()
    only = [code.upper() for code in options.courses]
    # A run for some courses holds only those, so it must never take the place of the full file
    if only and options.upload:
        parser.error("--upload needs a run of every course, so leave out the course codes")
    out = options.out or (TRIAL_FILE if only else OUTPUT_FILE)
    if only and out.resolve() == OUTPUT_FILE.resolve():
        parser.error(f"a run for some courses cannot write to {OUTPUT_FILE}, pick another --out")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    # The HTTP client logs every request at INFO, which buries the report
    for noisy in ("httpx", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    from sync.session import SignInNeeded
    cache = Cache(CACHE_FILE)
    report = Report()
    try:
        models = Models(cache, cap=options.cap)
        materials = collect(only, cache, report, headless=not options.window)
        if not materials:
            print("No course matched.", file=sys.stderr)
            return 1
        build(materials, models, cache, out, report, force=options.force)
        if options.upload:
            upload(out)
            report.say(f"Uploaded to {SERVER}:{SERVER_FILE}")
    except SignInNeeded as error:
        print(f"Not signed in: {error}. Run `python -m sync.session` and sign in, then run the sync again.", file=sys.stderr)
        return 2
    except SpendCapReached as error:
        print(f"Stopped: {error}. The course file was not changed. What was finished is cached, so run it again "
              "(with a higher --cap if this was expected).", file=sys.stderr)
        return 3
    except UploadFailed as error:
        print(f"The course file was built but not uploaded: {error}. The server still has its old file.", file=sys.stderr)
        return 5
    except BadRun as error:
        print(f"The new course file was not used: {error}. The old one is still in place. "
              "Use --force if the drop is expected.", file=sys.stderr)
        return 4
    finally:
        cache.close()
    if report.problems:
        print(f"\nFinished with {len(report.problems)} problems:", file=sys.stderr)
        for problem in report.problems:
            print(problem, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
