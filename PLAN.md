# Plan

Work that is bigger than one change, or that needs its own check before it ships. The code
that exists stays as it is until its item here is planned and checked.

## Study material

- **Live quality gate for 100 questions and a full guide.** Run one 100-question quiz and one
  chapter-by-chapter guide on `gpt-6-luna-checks` against the real course file. Measure the
  number of complete questions, duplicate or near-duplicate questions, correct answer-key
  entries, formula rendering, diagrams, and cost. Announce it in `run.md` before the run.
  Acceptance: 100 distinct questions with a key; no raw LaTeX; the guide covers every topic
  the outline lists; cost inside the personal daily cap. Why not now: it needs a paid model run
  and a hand read of the output, not a unit test.
- **Outline relevance and size.** The study lookup now starts from a course outline and can
  return 18 passages (6 per source). Check whether the outline crowds out the passages that
  answer the request, and whether 160,000 characters is the right ceiling for a 100-question
  reply. Acceptance: a measured prompt size and a retrieval check on real exam questions.
- **Writer reasoning on the live model.** The writer uses `AI_WRITER_REASONING_EFFORT=medium`
  with no tools. Check that `gpt-6-luna` accepts it and that the output improves enough to
  justify the tokens. Acceptance: one document run logged in `run.md`, no API error, and a
  measured cost.

## Sync

- **Posts and pictures in the sync.** The stashed sync work adds channel posts and the
  pictures inside them. It needs its own browser probe, privacy pass and full sync before it
  joins the course file. Acceptance: a real sync stores posts and pictures, the privacy guard
  passes, and the live bot reads them.

## P0 abuse controls and PR #36 follow-ups (2026-10-22)

Decisions from the owner: do the P0 work; no guild or DM gate; course material stays
public with no new access checks; encrypt the reply log before storing; skip the other
P1 items; Discord least privilege is already handled. Also fix every CodeRabbit finding
on PR #36 and the failing `test` check.

- [x] Fix the PR #36 `test` check: `sync/checks/run.py` calls the reply log without its
  handler after `_log_reply` became an instance method.
- [x] Add a request governor: one hard reply deadline, bounded global concurrency, a
  bounded queue that fails fast, and a per-reply model-call and cost ceiling.
- [x] Configure the OpenAI client with explicit short timeouts and retries instead of
  the SDK defaults.
- [x] Lock down music: per-user and per-guild rate limits, queue and per-user song caps,
  server-manager permission for skip/pause/resume/stop/volume, TLS verification on,
  fewer yt-dlp retries, and one extraction at a time per guild.
- [x] Encrypt reply-log payloads before they reach SQLite, keep a keyed user hash for
  deletion, secure the database and key file permissions, and document the key.
- [x] Delete a user's reply-log rows from `/forget` alongside their stored facts and
  conversations.
- [x] CodeRabbit: record reply-log rows and errors on every return path, including
  cache hits, unknown courses, budget fallbacks, timeouts, and failures.
- [x] CodeRabbit: move reply-log SQLite work off the event loop with `asyncio.to_thread`.
- [x] CodeRabbit: share one fence-aware splitter between citation extraction and
  cleanup, and support both backtick and tilde fences.
- [x] CodeRabbit: protect markers anywhere inside Markdown link labels from citation
  extraction and cleanup, including nested labels.
- [x] CodeRabbit: include eligible prose citations when building the source line,
  including citations after a source line.
- [x] CodeRabbit: remove known citation markers individually while preserving unknown
  neighbors such as `[9]` in `[2][9]`.
- [x] Add regression tests for the governor, music limits, encrypted/deleted logs,
  every reply-log return path, and all citation edge cases.
- [x] Run the full unit and integration suite, `ruff check .`, and `pyright`.
