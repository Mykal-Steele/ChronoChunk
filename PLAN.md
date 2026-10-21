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
