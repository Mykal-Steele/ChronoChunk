# Probe results

What was checked against the real Teams account before building the course sync, and what each check found. Run on 12 October 2026 with the saved sign-in, no window.

Run a probe from the repo root:

```bash
uv run --project sync python -m sync.session                         # sign in once
uv run --project sync python -m sync.probes.v02_census census.json   # sites, files, transcripts, notebooks
uv run --project sync python -m sync.probes.v03_transcripts census.json 4,8,16,32   # transcript quality, throttling
uv run --project sync python -m sync.probes.v04_posts                # channel posts
uv run --project sync python -m sync.probes.v05_assignments          # assignments
uv run --project sync python -m sync.probes.v06_classwork            # older posts, Classwork
uv run --project sync --with pypdf python -m sync.probes.v09_files census.json files/   # documents
```

## Summary

| # | Question | Result |
|---|---|---|
| V1 | Can the class sites be found without a hand-written list? | Yes. SharePoint search for sites whose title starts with `1-2569` returns 10 sites with their group ids. |
| V2 | How does each course store its files? | Mapped, see below. Every course is different, so the sync sorts files by type, not by folder name. |
| V3 | Do recordings have transcripts? | 74 of 76 do. All English, and the speech service was unsure about 1.4% of the words. |
| V4 | Can channel posts be read? | Yes, from the browser's own store. Scrolling a channel to the top loads the rest. |
| V5 | Can all assignments be read? | Yes. All three tabs, with paging, plus instructions, points and attached files from the detail call. |
| V6 | Is the Classwork tab used? | No. Empty in all 9 courses. Skipped. |
| V7 | Is the Class Notebook used? | No. Every notebook was last changed on the day the team was created. Skipped. |
| V8 | Is an expired sign-in noticed? | Yes. A profile with no sign-in is reported in about 20 seconds, for SharePoint and for Teams. |
| V9 | Do Office files convert? | Yes. LibreOffice turned all 24 into PDFs in 7 seconds. |
| V10 | Embedding quality | Failed for Thai (0 of 10). English reaches 9 of 10 only with keyword search added. See below. |
| V11 | Does the search engine fit the bot's image? | Yes. 51 ms at 50,000 chunks with 20 MB of memory. |
| V17 | How many requests at once? | 32 at once drew no pause from SharePoint. 8 is enough. |
| V18 | Odd files | 3 decks over 150 pages, and 27 files that are mostly pictures of pages. |

## V1: finding the courses

`/_api/search/query` with `contentclass:STS_Site Title:1-2569*` returns the sites the signed-in person can open. Searching by title matters: the CSC302 site's address still starts with `2-2024`, because the team is reused every semester.

Each row carries the Microsoft 365 group id. The same id is the `classId` on assignments and the `groupId` on channels, so it joins files, assignments and posts to one course.

A private channel has its own site with no group id (`CSC102 ... Mentoring Session Group 3`). The Teams page did not give up its team list to a network listener, because Teams loads it in a background worker.

## V2: how each course stores files

The whole census of 10 sites took 34 seconds.

| Course | Slides and handouts | Notes |
|---|---|---|
| CSC102 | `Class Materials`, flat, 8 PDFs | |
| CSC219 | `Class Materials`, one dated folder per class (`01-20260907`) | one zip |
| CSC302 | `Class Materials`, a folder per semester, then per week | holds three older semesters too, 124 files, many photos |
| CSC319 | `Class Materials`, one dated folder per class | |
| CSC340 | `Class Materials`, flat, 9 PDFs | two more PDFs in `Documents/General` |
| CSC345 | `Class Materials`, a folder per lecture | PDF, xlsx, doc, docx, csv |
| CSC491 | nothing yet | |
| CSC535 | no `Class Materials`. 7 PDFs in `Documents/General` | |
| LNG320 | `Class Materials`, a folder per week | PDF, docx, 20 photos, files attached to posts |

What every course shares:

- Recordings are in `Documents/General/Recordings` (in `Documents/Recordings` on a private channel site).
- `Student Work` holds the user's own submissions. The sync leaves it out.
- `Class Files/Assignments/<assignment name>` holds the files a teacher attached to an assignment.

File types seen: pdf (most), docx, doc, xlsx, csv, pptx, zip, jpg, png, mp3, txt, loop, ows.

What this means for the sync:

- Walk every library except `Student Work`, and decide what a file is from its type.
- Keep the folder path as the file's section ("Lecture3 -- Knowing your data", "Week 6"), so answers can say where a slide came from.
- Drop files last changed before the semester started. Without this, CSC302 brings in three old semesters.
- Skip photos, archives and media that are not recordings, and name them in the run report.

## V3: transcripts

76 recordings, 2 without a transcript:

- `CSC219__20261005-20261005_132826-Meeting Transcript.mp4`
- `CSC302 Seminar Talk (12026) - Week_3_20260817-...-Meeting Recording.mp4`

The recordings total about 47 GB. The sync never downloads them, only the transcript text.

The 74 transcripts are 25.7 MB of JSON: 183 hours of speech and about 900,000 words.

| Course | Recordings | Hours | Words | Unsure words |
|---|---|---|---|---|
| CSC102 | 9 | 20.0 | 81,273 | 0.4% |
| CSC102 mentoring | 2 | 4.0 | 8,458 | 2.4% |
| CSC219 | 4 | 14.4 | 80,847 | 1.3% |
| CSC302 | 20 | 40.1 | 224,053 | 2.3% |
| CSC319 | 5 | 12.5 | 69,249 | 1.3% |
| CSC340 | 8 | 25.7 | 99,147 | 2.5% |
| CSC345 | 9 | 26.0 | 136,092 | 0.8% |
| CSC535 | 7 | 18.8 | 138,506 | 0.4% |
| LNG320 | 10 | 21.9 | 62,234 | 0.8% |

- Every entry carries the speech service's own confidence from 0 to 1. "Unsure" counts the words in entries below 0.5.
- The speech is English. Thai is 1% of the words in CSC302 and CSC340 and absent elsewhere.
- One CSC102 transcript has under 200 words.
- The speaker name is often the room's account (`sit.lx1003 SIT-STAFF`), so it does not say who the teacher is.
- An entry also has a start and end time, which gives each chunk its place in the recording.

## V4: channel posts

Teams keeps posts in IndexedDB, in the `replychains` store of its `replychain-manager` database, and channels in the `conversations` store. Each thread has its messages with author, time, HTML body, and attached files.

- All 9 class channels were present without opening them one by one.
- A channel holds at most 20 threads until someone scrolls up. Channels with fewer than 20 go back to the team's creation in July.
- Message kinds: `RichText/Html` and `Text` are posts and replies. `Event/Call`, `RichText/Media_CallRecording` and `RichText/Media_CallTranscript` are meeting records and are not posts.
- Scrolling a channel to the top loads every older thread into the store. The four channels that sat at 20 grew to 23 (CSC102), 27 (LNG320), 36 (CSC345) and 157 (CSC302).
- CSC302's posts go back to January 2025, because the team is reused. Posts need the same semester cut as files.
- Opening a channel marks it as read, the same as opening it by hand.

## V5: assignments

- The list call (`/api/v1.0/edu/me/work`) pages with `$skip` as the list is scrolled. Completed returned 79 assignments from 12 classes over 5 calls. Past due was empty.
- The list covers every class the user was ever in, so the sync keeps only the class ids of the indexed courses.
- The list leaves out instructions. Opening an assignment loads `/edu/classes/{id}/assignments/{id}` with instructions, points, rubric and resources.
- A resource has a type (PowerPoint, file, link), a name and a file address. The files are the ones in `Class Files/Assignments/`.
- The same click also loads the user's submission and the class member list. The sync does not keep either.

## V6: Classwork

Every one of the 9 class teams shows the same empty page: "Your course materials aren't here yet". No module list is loaded. The sync skips Classwork.

## V8: expired sign-in

Run against an empty profile, with no window. SharePoint and Teams each raised `SignInNeeded` after about 20 seconds. How long a real sign-in lasts is not known yet. The profile was signed in on 12 October 2026.

## V9 and V18: documents

With the rules from V2 applied, 175 documents (958 MB) are left. The rules dropped 51 files from older semesters, 85 photos, 3 Loop files, 2 Orange files and 1 zip.

| Course | Files | Pages | Pages with no text |
|---|---|---|---|
| CSC102 | 10 | 407 | 7% |
| CSC219 | 9 | 307 | 48% |
| CSC302 | 23 | 658 | 15% |
| CSC319 | 12 | 224 | 25% |
| CSC340 | 11 | 806 | 43% |
| CSC345 | 71 | 838 | 18% |
| CSC535 | 7 | 837 | 53% |
| LNG320 | 32 | 449 | 14% |

- 4,526 pages in total. "No text" is measured on the first 40 pages of each file.
- 27 files (1,016 pages) are mostly pictures of pages. Their text only exists if the model describes each page.
- Three decks are longer than the bot's 150-page limit for describing pages: 167 (CSC340), 252 and 411 (CSC535). The sync needs its own limit.
- LibreOffice converted all 24 Office files (1 doc, 18 docx, 4 xlsx, 1 pptx) in 7 seconds, and pypdf read text from every result.
- A spreadsheet becomes one page, which can cut off a wide sheet. Reading xlsx and csv as tables is safer.
- Some files exist twice, once in `Class Materials` and once under `Class Files/Assignments`. The sync has to notice equal content.
- Downloading one file at a time took 13 minutes for 958 MB. CSC302's seminar decks are about 28 MB each. The first sync needs downloads side by side.
- Found later: the slowness was the browser's own request call. A file's `/content` address answers with a redirect to a link that needs no sign-in, and six plain downloads at once fetched 67 MB in 5.4 seconds.
- A file listing carries a content tag (`cTag`) that only changes when the content does, and so does a transcript listing. The sync uses them to skip what it already has.
- pypdf read every file. A few PDFs printed repair warnings.

## V10: embeddings

`text-embedding-3-small` on the Azure resource, tested on CSC340 and CSC345: 2,828 transcript chunks of about 75 seconds and 1,116 slide pages. Embedding all 3,944 took 759,232 tokens (about $0.015) and 5 minutes at the deployment's limit of 150,000 tokens a minute. A question takes about 55 ms to embed.

`v10_questions.json` asks about 10 places in the material, each in English, in Thai, and in Thai with English terms. A question passes when a chunk with the expected phrase is in the top 6. No course filter was used.

| Numbers per chunk | English | Thai | Mixed |
|---|---|---|---|
| 512 | 7 of 10 | 0 of 10 | 5 of 10 |
| 1536 | 8 of 10 | 0 of 10 | 6 of 10 |

- 1536 numbers pass one more question than 512 in English and in mixed. The keyword index below closes a bigger gap than that, so 512 stays for now and V12 decides.
- A Thai question finds chunks written in Thai, not chunks about the same thing. The lectures are in English, so the top results were the few Thai slides and unrelated speech.
- The fix: the chat model writes the search query in English when it calls the tool. Tested below.

### Thai questions through an English query

gpt-6-luna (reasoning off) was given a `search_course` tool and each of the 30 questions as a chat message. The query it wrote went through the vectors and the word index together. Three wordings of the tool's `query` description:

| Wording of the description | Tool called | English | Thai | Mixed |
|---|---|---|---|---|
| "written in English even when the user wrote in another language. Use the technical terms a lecturer would use" | 30 of 30 | 8 of 10 | 9 of 10 | 7 of 10 |
| "Always in English ... 12 words at most ... Do not list possible answers" | 29 of 30 | 7 of 10 | 8 of 10 | 6 of 10 |
| "Translate it ... Keep the words the user used" | 30 of 30 | not kept | 2 of 10 | 6 of 10 |

- Translating takes Thai from 0 of 10 to 8 or 9 of 10, level with English.
- "Keep the words the user used" made the model pass the Thai through untranslated. The description must not say that.
- The questions that still fail are the same three in every language: the assignment 2 brief, the mushroom dataset, and the missing-values table. They fail on ranking, not on language. Assignment questions get their own lookup tool, and the missing-values question has more than one right place in the lectures, so the test may be too strict there.
- Scores move by one question between runs.
- Material written in Thai (a few slides, about 1% of the speech in two courses) gets an English translation at sync time, so search runs in English only and the original Thai is kept for quoting.

The English questions again, with a keyword index next to the vectors and equal chunks shown once:

| Keyword index | Vectors only | Keywords only | Both, merged by rank |
|---|---|---|---|
| Words with stemming (`porter unicode61`) | 7 of 10 | 9 of 10 | 9 of 10 |
| Trigram | 7 of 10 | 6 of 10 | 8 of 10 |

- The plan had one trigram index. English needs a word index, and trigram stays for Thai text.
- Ten questions is a small set. V12 repeats this with about 60.
- The same slide came back twice, from the copy in `Class Files/Assignments`.

## V11: search engine

Run inside the bot's image (Python 3.12, SQLite 3.46.1) with `sqlite-vec` 0.1.9, 512 numbers per chunk and made-up data.

| Chunks | Search all courses | Search one course | Thai text search | Memory | File |
|---|---|---|---|---|---|
| 10,000 | 12 ms | 1 ms | 4 ms | 20 MB | 25 MB |
| 50,000 | 51 ms | 5 ms | 17 ms | 20 MB | 123 MB |

The target was under 150 ms and under 100 MB. The Thai search matched a Thai word inside a longer run of Thai text.

## V17: throttling

All 74 transcripts, downloaded from inside a SharePoint page:

| At once | Time | Asked to pause |
|---|---|---|
| 4 | 15.3 s | 0 |
| 8 | 5.8 s | 0 |
| 16 | 4.3 s | 0 |
| 32 | 3.7 s | 0 |

The sync uses 8. Going higher saves under 2 seconds.

## First real sync: CSC340 and CSC345

Run on 12 October 2026 with `python -m sync CSC340 CSC345`.

| | CSC340 | CSC345 |
|---|---|---|
| Documents read | 10 | 61 |
| Copies of a file already read | 1 | 10 |
| Page chunks | 677 | 651 |
| Transcripts | 8 | 9 |
| Transcript chunks | 1,316 | 1,504 |

- The first run took 11 minutes and about $0.15 in model calls (225 calls). Most of the time was waiting on the embedding deployment's limit of 150,000 tokens a minute.
- A second run with nothing changed took 3.6 seconds for one course and called no model.
- The course file for both courses is 15.7 MB.
- 70 chunks of CSC340 held Thai and were translated, with the Thai kept beside the English.
- Showing the model the text already extracted from a page cut the page text by 30%, because it stopped repeating it.

Search quality on this file, from `python -m sync.evaluate` with the 30 questions of V10 and 6 questions about things the material does not cover. The chat model writes the query. Three runs:

| Run | English | Thai | Mixed | Not in the material |
|---|---|---|---|---|
| 1 | 9 of 10 | 10 of 10 | 9 of 10 | 5 of 6 |
| 2 | 10 of 10 | 10 of 10 | 9 of 10 | 5 of 6 |
| 3 | 10 of 10 | 10 of 10 | 9 of 10 | 5 of 6 |

What got it there, each measured on the same questions:

- The first wording of the tool's query field let the model pad the query with words like "lecture" and with guessed answers. That scored about 8 of 10 per language and 3 of 6 on the questions with no answer, because the padding looks like every lecture chunk. Asking for a short phrase of the topic's own words fixed both.
- One result per source hid the right part of a three-hour lecture behind another part of it. Two per source, never neighbours, found it.
- Two versions of one handout filled two of the six places with the same page. Equal text now comes back once.
- A search takes about 8 ms.

The one "not in the material" question that still returns results asks about quantum annealing, and a slide does mention quantum computers. The bot has to say that the material only touches on it.

These questions were also the ones the search was tuned on, so the scores are a best case. V12 needs questions the search has not seen, from all courses.

## Not run yet

- V12 to V16 and V19: these test answers, routing, cost and injected instructions, so they need the store and the bot tools built first.

## Other findings

- A site's home page has to be opened once before its API answers. Going straight to an `/_api/` address gives 403. One visit to the tenant root is enough for every site.
- The assignment and channel-message calls cannot be made by a script directly. They need the sign-in token that Teams keeps encrypted. The sync reads what the page itself loads.
