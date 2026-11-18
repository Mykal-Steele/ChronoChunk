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
| 1 | Remember what people teach | `remember` and `recall` tools, each memory with who said it, when and where, corrections replace the old fact. Memories that share words with the message go in front of the model before it answers | Done 2026-11-19 (3.25.0) |
| 2 | Find the memory that fits | Next. Embeddings (Vertex `gemini-embedding-2`) in `sqlite-vec`, about 10 memories per reply ranked by relevance, recency and importance. Also brings course search back on Vertex | |
| 3 | Episodes and sleep | Everything said in a channel is stored. A nightly job writes a summary of each channel's day, merges duplicate facts, settles contradictions and lets unimportant memories fade | |
| 4 | Learning how to behave | Notices feedback ("stop roasting me", "that's wrong", a thumbs down) and writes a short lesson for that person or the whole server, which goes into its prompt. It never edits its code or its base personality | |
| 5 | Control | `/memory` shows what it knows about you, `/forget` erases it, and admins can review and delete what it learned about the group or the world | |

## Rules that hold in every layer

- Trust: what someone says about themselves is believed. A claim about the world is stored as "X told me", and checked with a web search before the bot states it as fact. One person cannot overwrite a fact others confirmed.
- Every memory has a source (person, server, channel, time) and an importance score.
- Where it is stored: memories, like facts and conversations, are plain SQLite in `state/chronochunk.db` on the VM, which only root and the bot's user can read. The reply log is encrypted row by row. Every copy that leaves the VM (the R2 backups) is encrypted. Encrypting the database itself would mean a key on the same disk, so it would not protect against anyone who can already read the file.
- Provider-neutral: works on Vertex, Azure or any OpenAI-compatible API, same as the rest of the bot.

## Cost

- Embeddings: about $0.15 per million tokens. A few thousand messages a day is around $0.30 a month.
- Nightly summaries on `gemini-3.1-flash-lite`: a few cents a day.
- R2: free up to 10 GB. The database is well under 1 MB today.

## How it is checked

A fixed script teaches the bot facts, then asks about them later in other channels and servers, and counts what it remembers and what it makes up. It runs before each layer ships, logged in `run.md`.

## Restoring a backup

What the nightly job keeps in R2, under rclone's `backup:` remote (encrypted, kept 30 days):

- `db/chronochunk-<day>.db`: the bot's memory, one copy per day.
- `replies/replies-<day>.sqlite3`: the reply log. Its rows are encrypted with the key below.
- `keys/reply_log.key`: that key, sent again every night so R2 never expires it.

On the VM, pick a day from the list, then put the three files where the bot reads them. The current files are moved to `before-restore` first, not deleted:

```bash
C="sudo rclone --config /etc/chronochunk/rclone.conf"
S=/opt/chronochunk/data/state
$C lsl backup:
cd /opt/chronochunk && sudo docker compose stop bot
sudo mkdir -p $S/before-restore && sudo mv $S/chronochunk.db* $S/replies.sqlite3 $S/reply_log.key $S/before-restore/
$C copyto backup:db/chronochunk-<day>.db $S/chronochunk.db
$C copyto backup:replies/replies-<day>.sqlite3 $S/replies.sqlite3
$C copyto backup:keys/reply_log.key $S/reply_log.key
sudo chown -R 1000:1000 $S && sudo chmod 600 $S/replies.sqlite3 $S/reply_log.key
sudo docker compose start bot
```

The folder has to stay writable by the bot's user (uid 1000): SQLite writes a small file next to a database in WAL mode even to read it. If `REPLY_LOG_KEY` is set in `.env`, the bot uses that key and not the file.

Check it worked:

- `sudo docker logs --tail 20 chronochunk` shows "Logged in as ChronoChunk".
- `sudo docker compose exec bot python -m src.reply_log --tail 1` prints the newest reply in plain text, so the key matches.
- `/info` in Discord shows what the bot remembers about you.

Without the VM: build a new one with "Setting up a new server" in the README, make a new R2 token for `chronochunk-backups`, write `rclone.conf` from `deployment/backup/rclone.conf.example` with the password and salt kept off the server, then run the same commands.

Tested on 2026-11-18 by restoring into a scratch folder on the VM: the bot's own code opened the database (integrity ok, 5 users, 43 conversations) and decrypted the reply log with the restored key.
