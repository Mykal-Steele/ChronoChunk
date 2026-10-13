# Run log

Every run that spends Azure money or signs in to the university account is listed here. The row is written before the run starts, with an estimate. The actual cost and the result are filled in after.

Rules:

- A run estimated above $0.50 is announced before it starts.
- Checks run against the checks deployment, never the one the live bot uses, and ask one question at a time.
- Costs are what the code counted from token usage at list price. The Azure bill is the final word.

## Planned

| Run | Why | Estimate |
|---|---|---|
| E1: search with the raw message against search with words the model wrote, about 60 questions | Decides whether the lookup can run before the first model call | under $0.01 |
| Picture probe, then 4 picture descriptions | Assignment instructions that are only a picture | about $0.01 |
| Answer check with a judge, about 60 questions, per run | The gate for every change to retrieval or the prompt | about $0.06 a run, about 6 runs |
| Full sync, twice | The course file with assignments, for the check and for the server | about $0.01 |

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

Total so far on 2026-10-13: about $0.06.
