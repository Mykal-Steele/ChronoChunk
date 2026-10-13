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
| 2026-10-13 | Full sync into a scratch file, 6 runs | Fill the new assignment cache and run the privacy guard on real data | 0 model calls | under $0.01 | $0 | The first 4 runs were stopped by the guard, on a false alarm: Microsoft's own id for the Assignments app sits in every submission link. After the guard learned those two ids, run 5 opened 15 assignments and run 6 opened none. The course file is version 2 with due times |
| 2026-10-14 | Baseline check with the judge, question set `release1`, 104 messages, on `gpt-6-luna-checks` | The first judged measure of flags, sessions, sources by number and assignments | 116 replies, 73 judged | about $0.12 | $0.083 | 98 of 104 passed. Lookup on 73 of 73 flagged messages, right passage 58 of 59, answers judged right 57 of 59, session small talk clean 6 of 6, flag pointed to 5 of 5. Gate held on 6 misses: 1 real bug (passage numbers stripped out of code), 2 wrong "made up" verdicts by the judge (both claims are in the material), 2 false alarms of the course-talk rule, 1 expected phrase that was too strict. Also seen: 15 of 42 replies searched a second time for the same thing. Median reply 5.3 seconds |
| 2026-10-14 | Second check with the judge, same 104 messages | After the fixes from the baseline | 116 replies, 73 judged | about $0.09 | $0.074 | Right passage 59 of 59, plain chat clean 20 of 20, session small talk clean 6 of 6, median reply 3.8 seconds (5.3 before), second searches down from 36 to 24. Gate held: 2 small made-up statements (a general Java claim added from the model's own knowledge, and a sloppy retelling of "the closest thing"), one source line left as numbers because it was written mid-sentence, 3 questions that were graded as answerable though the material cannot answer them, and 1 judge verdict I do not agree with |
| 2026-10-14 | Third check with the judge, same 104 messages | After the mid-sentence source line and two prompt rules | 116 replies, 73 judged | about $0.08 | $0.078 | Bot behaviour steady: lookup 73 of 73, plain chat clean 20 of 20, session small talk clean 6 of 6, median 3.9 seconds. Gate held on 4 "made up" verdicts. Checked by hand: 3 are in the material (Lab 7 is 5 points, the slide says f(n) = g(n) + h(n), LNG320 is the English class) and 1 is the bot adding vacuum-cleaner examples of its own. The judge was the weak part of this run |
| 2026-10-14 | Fourth check with the judge, same 104 messages | A more careful judge | 116 replies, 73 judged | about $0.12 | $0.086 | Every line of the gate passed except "nothing made up" (3). Looking each claim up in what the bot had been shown found a real bug: a session kept 16,000 characters, a lookup is 10,000 to 12,000, so a second lookup pushed the first one out. The bot was then told those passages were "already shown" while it could no longer read them on the next turn, and the judge was grading against the cut-down material |
| 2026-10-14 | Fifth check with the judge, same 104 messages | Sessions that hold three lookups, and a judge that reads all the model was shown | 116 replies, 73 judged | about $0.10 | $0.089 | Gate passed. 102 of 104. Lookup 73 of 73, right place 56 of 56, answers judged right 54 of 56, made up 0, says so when it is not there 17 of 17, plain chat clean 20 of 20, session small talk clean 6 of 6, flag pointed to 5 of 5. Median reply 4.0 seconds. The 2 misses: one reference fact in the question set that misled the judge, and one reply that added general Java knowledge the course's slide disagrees with |

Total so far: about $0.54.
