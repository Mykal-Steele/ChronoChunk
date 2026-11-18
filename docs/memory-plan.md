# Real memory for ChronoChunk

Goal: the bot remembers like a person. What someone teaches it stays, it finds the memory that fits the conversation instead of the newest few, it sorts its memory over time, and it changes how it behaves from people's reactions.

## What exists (3.23.0)

- Facts about each person, picked out of their messages by the background model. Only the newest 15 are shown to it.
- Past exchanges with each person, and the recent messages of the channel, kept in memory.
- One SQLite file, `state/chronochunk.db`, with daily copies in `state/backups`.
- No place for things people teach it about the world or the group, no search by meaning, nothing that merges or corrects memories.

## Decisions (2026-11-18)

| Question | Answer |
|---|---|
| Where what one person teaches applies | Everywhere, all servers |
| What it remembers | Everything said in the channels it is in, not only messages to it |
| Behavior lessons | Applied automatically, no review step |
| Off-server backups | Cloudflare R2 |

Accepted with these decisions: something said in a small server can come up in a big one, and people who never talk to the bot are remembered. `PRIVACY.md`, `/forget` and the announcement have to say so. Every memory keeps where it came from, so the bot can be careful with personal ones.

## Layers

| # | Layer | What it adds | Status |
|---|---|---|---|
| 0 | Memory that survives | Nightly encrypted copy of the database in R2, 30 days kept | Done 2026-11-18, restore tested |
| 1 | Remember what people teach | `remember` and `recall` tools, each memory with who said it, when and where, corrections replace the old fact | Next |
| 2 | Find the memory that fits | Embeddings (Vertex `gemini-embedding-2`) in `sqlite-vec`, about 10 memories per reply ranked by relevance, recency and importance. Also brings course search back on Vertex | |
| 3 | Episodes and sleep | Everything said in a channel is stored. A nightly job writes a summary of each channel's day, merges duplicate facts, settles contradictions and lets unimportant memories fade | |
| 4 | Learning how to behave | Notices feedback ("stop roasting me", "that's wrong", a thumbs down) and writes a short lesson for that person or the whole server, which goes into its prompt. It never edits its code or its base personality | |
| 5 | Control | `/memory` shows what it knows about you, `/forget` erases it, and admins can review and delete what it learned about the group or the world | |

## Rules that hold in every layer

- Trust: what someone says about themselves is believed. A claim about the world is stored as "X told me", and checked with a web search before the bot states it as fact. One person cannot overwrite a fact others confirmed.
- Every memory has a source (person, server, channel, time) and an importance score.
- Memory stays encrypted at rest where the reply log already is, and in the R2 backups.
- Provider-neutral: works on Vertex, Azure or any OpenAI-compatible API, same as the rest of the bot.

## Cost

- Embeddings: about $0.15 per million tokens. A few thousand messages a day is around $0.30 a month.
- Nightly summaries on `gemini-3.1-flash-lite`: a few cents a day.
- R2: free up to 10 GB. The database is well under 1 MB today.

## How it is checked

A fixed script teaches the bot facts, then asks about them later in other channels and servers, and counts what it remembers and what it makes up. It runs before each layer ships, logged in `run.md`.

## Restoring a backup

On the VM:

```bash
sudo rclone --config /etc/chronochunk/rclone.conf lsl backup:db
sudo rclone --config /etc/chronochunk/rclone.conf copy backup:db/chronochunk-<day>.db /tmp/restore
```

Without the VM: make a new R2 token for `chronochunk-backups`, rebuild `rclone.conf` from `deployment/backup/rclone.conf.example` with the password and salt kept off the server, then run the same commands.
