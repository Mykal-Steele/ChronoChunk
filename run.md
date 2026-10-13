# Run log

Every run that spends Azure money or signs in to the university account is listed here. The row is written before the run starts, with an estimate. The actual cost and the result are filled in after.

Rules:

- A run estimated above $0.50 is announced before it starts.
- Checks run against the checks deployment, never the one the live bot uses, and ask one question at a time.
- Costs are what the code counted from token usage at list price. The Azure bill is the final word.

## Planned

| Run | Why | Estimate |
|---|---|---|
| Picture probe, then 4 picture descriptions | Assignment instructions that are only a picture | about $0.01 |
| Answer check with a judge, about 60 questions, per run | The gate for every change to retrieval or the prompt | about $0.06 a run, about 6 runs |
| Full sync for the server, after the remaining sync work | The course file the live bot will read | about $0.01 |

## Done

| Date | Run | Why | Calls | Estimate | Actual | Result |
|---|---|---|---|---|---|---|
| 2026-10-13 | Browser probes of the Teams Assignments app, 4 runs | Find a reliable way to open each assignment | no model calls | none made | $0 | The app has its own address for every class and assignment |
| 2026-10-13 | Assignment collection over 9 classes, 2 runs | Check the new collection on real data | no model calls | none made | $0 | 16 assignments, no failures on the second run |
| 2026-10-13 | Trial sync, CSC340 and CSC345 | Check assignments end to end in a course file | 3 | none made | $0.0008 | 15 assignments stored, 1 opened without its instructions |
| 2026-10-13 | Full sync, 9 courses, into a scratch file | A course file for the checks | 2 | none made | under $0.0001 | 16 assignments, no problems |
| 2026-10-13 | V12, 51 questions, 4 at a time, on the live deployment | Gate for the assignment tool | about 60 | none made | about $0.02 | Not usable. 23 questions hit the token rate limit. This is the run that could have starved the live bot |
| 2026-10-13 | Assignment questions, 12, one at a time, on the live deployment | Check which tool the model calls and what it answers | about 24 | none made | $0.009 | All 12 behaved right |
| 2026-10-13 | V12, 51 questions, one at a time, on the live deployment | Gate for the assignment tool | about 100 | none made | about $0.03 | 37 of 41 found the right passage, 10 of 10 with no answer said so |
| 2026-10-13 | E1: the 51 V12 questions searched with the raw message, embeddings only, one at a time | Decides whether the lookup can run before the first model call | 51 embedding calls | under $0.001 | $0.00002 | English 31 of 33 found (the model's own wording got 30). But only 2 of 10 questions with no answer came back empty, because the course name and words like "lecture" match everything a little |
| 2026-10-13 | E1b: the same with the course name and filler words taken out first | Fix for the above | 51 embedding calls | under $0.001 | $0.00002 | English 31 of 33, no-answer 10 of 10. Decision: the lookup runs before the first model call, with the cleaned message as the search |
| 2026-10-13 | Full sync, 9 courses, into a scratch file | A course file with the nicknames in it, and a rerun of the assignment collection after its type fix | 0 model calls, all cached | under $0.01 | $0 | 16 assignments, no problems, 125 seconds to collect |
| 2026-10-13 | First check of the flag on the real model, 78 messages, one at a time on `gpt-6-luna-checks` | Nothing built for the flag had met the real model | 78 chat replies, 71 lookups | about $0.05 | $0.033 | Every flagged message got a lookup. Right passage in 36 of 41, no-answer 10 of 10 said so, 6 of 6 chat replies had no course talk, 3 of 3 unflagged class questions pointed to the flag. 55 of 63 flagged replies took one model call. Median 3.2 seconds (4.8 before). No rate limit hits. Found: the nickname "java" sent a question to the wrong course, and two Thai questions found nothing and were not searched again in English |
| 2026-10-13 | Replay of the lookup for the two bad due-date answers, then 16 date and homework questions after the fix, on `gpt-6-luna-checks` | "whats due this week" was padded with unrelated schedule lines, and "any homework" gave lecture homework with no word on Teams | 16 chat replies, about 14 embedding calls | about $0.01 | $0.0084 | Cause: a pure date question was also searched as a topic, and the block told the model its passages were only loosely related even when the list answered it. After the fix "whats due this week" answers from the list alone, in one call. A course with nothing in Teams now says so. Still weak: homework taken from a slide is not always given with that slide's date |

Total so far on 2026-10-13: about $0.11.
