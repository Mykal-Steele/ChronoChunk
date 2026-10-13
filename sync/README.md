# Course sync

Collects this semester's course material from Microsoft Teams and writes one file, `courses.db`, that the bot can search. It runs on a PC, not on the bot's server, because it needs a browser that is signed in to the university account.

What it collects per course:

- Documents from the class site's libraries (PDF, PowerPoint, Word, Excel, CSV, text).
- The transcript of every lecture recording. The recordings themselves are never downloaded.

People's names are taken out of the transcripts. Teams writes a name it knows from the meeting in capitals, so a word in capitals that the course never uses as a word becomes `[name]`. This misses a name written like any other word and a name shorter than five letters. Names in the documents, such as a presenter's name on a slide, are kept.

It leaves out the `Student Work` library, files last changed before the semester, photos, archives, and the Classwork and Class Notebook tabs.

## Before the first run

You need `uv`, Chromium at `/usr/bin/chromium`, and LibreOffice (`soffice`) for Office files. The bot's `.env` in the repo root must hold `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_KEY`, and the Azure resource needs a `text-embedding-3-small` deployment.

Sign in once. A window opens, and the sign-in is kept in a browser profile of its own:

```bash
uv run --project sync python -m sync.session
```

## Running it

From the repo root:

```bash
uv run --project sync python -m sync                  # every course of the semester
uv run --project sync python -m sync CSC340 CSC345    # only these, into courses-trial.db
uv run --project sync python -m sync --upload         # and put the result on the bot's server
```

A run for named courses is a trial. It writes `courses-trial.db`, cannot write to `courses.db`, and cannot be uploaded, because its file holds only those courses.

The run prints what it found per course, what it left out and why, what failed, and what the model calls cost.

| Option | What it does |
|---|---|
| `--out PATH` | Write the course file somewhere else |
| `--upload` | Copy the finished file to the server (needs `CHRONOCHUNK_SERVER`) |
| `--cap USD` | Stop before spending more than this on models (default 2) |
| `--force` | Use the new file even when a course lost more than 30% of its chunks |
| `--window` | Show the browser while it works |

## What a run does

1. Opens the saved sign-in without a window and finds every class site whose title starts with the semester prefix.
2. Lists each site's files and downloads the ones that changed, six at a time.
3. Fetches the transcripts that changed.
4. Reads each document page by page. A page gives its text, and the model adds what the text misses (tables, charts, text inside pictures) from a picture of the page.
5. Cuts transcripts into chunks of about 75 seconds.
6. Translates anything in Thai into English and keeps the Thai beside it. Search runs on the English.
7. Embeds every chunk and writes `courses.db.new`.
8. Checks the new file, keeps the old one as `courses.db.previous`, and swaps the new one in.

## Reruns are cheap

Everything a run downloads or pays for goes into a cache. A second run with nothing new takes a few seconds and calls no model. A run that was stopped, or that hit the spending limit, continues where it left off.

A failed run never replaces the course file. A file or transcript that changed but could not be fetched keeps its last copy, and the report says so.

## Where things are kept

All under `~/.local/share/chronochunk-sync/` (change it with `CHRONOCHUNK_SYNC_HOME`):

| Path | What it is |
|---|---|
| `profile/` | The browser profile with the sign-in |
| `files/` | Downloaded documents |
| `cache.db` | Transcripts, page descriptions, translations and embeddings |
| `courses.db` | The finished course file |
| `courses.db.previous` | The course file from the run before |
| `courses-trial.db` | What a run for named courses writes |

Deleting `files/` and `cache.db` is safe. The next run downloads and reads everything again, which costs about what the first run did.

## Settings

Set these in the environment. The defaults are in `settings.py`.

| Name | Default | Meaning |
|---|---|---|
| `COURSE_PREFIX` | `1-2569` | Class teams of the semester start with this |
| `SEMESTER_START` | `2026-07-01` | Files changed before this day are left out |
| `SYNC_SPEND_CAP` | `2` | USD one run may spend |
| `SYNC_CHAT_DEPLOYMENT` | `gpt-6-luna` | Describes pages and translates |
| `EMBEDDING_DEPLOYMENT` | `text-embedding-3-small` | Embeds chunks |
| `CHRONOCHUNK_SERVER` | empty | `user@host` of the bot's server, for `--upload` |

At the start of a new semester, change `COURSE_PREFIX` and `SEMESTER_START`.

## When the sign-in expires

The run stops with "Not signed in". Run `python -m sync.session` again, sign in, and start the sync again.

## Checking search quality

`evaluate.py` asks a set of questions the way the bot will and reports how many found the right place:

```bash
uv run --project sync python -m sync.evaluate ~/.local/share/chronochunk-sync/courses.db sync/probes/v10_questions.json
```

Run it after changing how documents are read, how chunks are cut, or how search works.

## Tests

The sync's tests run with the bot's (`tests/unit/test_sync_*.py`). They use a fake browser and a fake model, so they need no sign-in and cost nothing. `probes/` holds the scripts that were used to learn how Teams stores things, with their results in `probes/RESULTS.md`.
