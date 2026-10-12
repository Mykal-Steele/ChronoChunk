# ChronoChunk Discord Bot

A Discord bot with a personality. It chats, remembers things about the people it talks to, reads images and files, writes documents, searches the web and plays music. The AI runs on Azure OpenAI: gpt-6-luna for chat and gpt-5-nano for background jobs, or any other deployment you name in the settings.

## Features

- Chat in the bot's own voice, with memory of who it is talking to
- Uses the server's own emoji in chat, knows what each one shows, and never sends one that would show up broken
- Reacts to a message with an emoji now and then, on top of its reply
- Reads images, PDFs, text and code files, and sums up a channel with `/tldr`
- Writes PDFs, Word files and diagrams
- Opens web links and searches the web
- Plays music in a voice channel
- A number guessing game
- A spending cap on the AI, and limits per user

## Setup

The bot runs in Docker. You need a Discord bot token and an Azure OpenAI deployment.

1. Clone the repo:

```bash
git clone https://github.com/Mykal-Steele/ChronoChunk.git
cd ChronoChunk
```

2. Create a `.env` file:

```
DISCORD_TOKEN=your_discord_token
AZURE_OPENAI_KEY=your_azure_openai_key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/openai/v1
AZURE_OPENAI_DEPLOYMENT=gpt-5-mini
TAVILY_API_KEY=your_tavily_key
```

`TAVILY_API_KEY` is optional. Without it the bot opens ordinary web pages but cannot search, and cannot read PDF links or pages that need JavaScript.

3. In the Discord developer portal, turn on the Message Content and Server Members intents for the bot.

4. Build and run:

```bash
docker build -t chronochunk .
docker run -d --name chronochunk --env-file .env chronochunk
```

To run it on a server and keep its data between rebuilds, use `deployment/docker-compose.yml`. The comments at the top of that file say where each file goes. `deployment/cloud-init.yml` sets up a fresh Azure VM for it.

The bot runs inside the container as user 1000, not root. The folders mounted for `user_data`, `logs` and `state` must belong to that user: `sudo chown -R 1000:1000 data`.

## Where it runs

The live bot runs on one small Azure VM. Type `/setup` in Discord to see the same facts from the running bot, with the values it is using right now.

| What | Value |
| --- | --- |
| Host | Azure VM `chronochunk-vm`, size `Standard_B2ats_v2` (1 GiB of RAM), region `southeastasia`, Ubuntu 24.04 |
| Swap | 2 GiB, because image builds need more than 1 GiB |
| Runs in | Docker Compose, from `deployment/docker-compose.yml`. One container named `chronochunk`. |
| Container limits | 640 MB of memory, 256 processes, user 1000, no Linux capabilities. It restarts by itself unless stopped. |
| Health check | `http://localhost:8080/health` once a minute. `/status` on the same port gives the version and uptime as JSON. |
| Chat model | Azure OpenAI deployment `gpt-6-luna`, with reasoning switched off (`none`) |
| Background model | `gpt-5-nano`, which picks facts out of messages after the answer is sent |
| Data | One SQLite file, `state/chronochunk.db`, kept on the host so a rebuild does not touch it |
| Backups | A copy of that file in `state/backups` at startup and once a day. The newest 7 are kept. They are on the same disk, so they do not cover losing the VM. |
| Deploys from | The `main` branch |

### Folders on the server

Everything is under `/opt/chronochunk`:

- `app/` is a git checkout of `main`. The image is built from it.
- `docker-compose.yml` is a copy of `deployment/docker-compose.yml`. Copy it again when that file changes.
- `.env` holds the secrets. It is not in the repo.
- `data/user_data`, `data/logs` and `data/state` are mounted into the container and belong to user 1000.

### Settings

Secrets go in `.env` on the server:

| Name | What it is |
| --- | --- |
| `DISCORD_TOKEN` | The bot's Discord token |
| `AZURE_OPENAI_KEY` | Key for the Azure OpenAI resource |
| `AZURE_OPENAI_ENDPOINT` | The resource's address, ending in `/openai/v1` |
| `TAVILY_API_KEY` | Key for web search. Optional. |

These three are set in `docker-compose.yml`, which wins over `.env`:

| Name | Value on the server |
| --- | --- |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-6-luna` |
| `AI_CHAT_REASONING_EFFORT` | `none` |
| `AZURE_OPENAI_BACKGROUND_DEPLOYMENT` | `gpt-5-nano` |

Everything else uses the default from `config/config.py` unless `.env` sets it:

| Name | Default | What it does |
| --- | --- | --- |
| `AI_MONTHLY_BUDGET_USD` | 15 | The bot stops calling the model past this much in a billing month |
| `AI_DAILY_BUDGET_USD` | 2 | The same, per day |
| `AI_BUDGET_RESET_DAY` | 14 | Day of the month the billing month starts |
| `AI_USER_DAILY_BUDGET_USD` | 0.20 | What one person may use in a day |
| `AI_USER_WEEKLY_BUDGET_USD` | 0.60 | What one person may use over 7 days |
| `AI_UNLIMITED_USER_IDS` | empty | Discord user ids the two limits above skip, separated by commas |
| `PDF_PAGE_NOTES` | `auto` | `auto`, `on` or `off`: whether the bot describes PDF pages from pictures of them |
| `PDF_NOTES_KEEP_DAYS` | 7 | Days a PDF's page descriptions are kept |
| `LOG_RETENTION_DAYS` | 30 | Days a log file is kept |
| `TEST_BOT_ID`, `TEST_CHANNEL_ID` | empty | For testing the live bot: the one other bot it answers, and the one channel it answers it in. Both must be set. |

The message, file, PDF, web and recap limits are in `RATE_LIMITS` in the same file.

### Deploying a change

1. Merge the pull request into `main`.
2. If `deployment/docker-compose.yml` changed, copy it to `/opt/chronochunk/docker-compose.yml` on the server.
3. Run the update script on the server. It pulls `main`, rebuilds the image and restarts the container:

```bash
ssh azureuser@<server address> /opt/chronochunk/update.sh
```

4. Check it came back: `docker logs --tail 50 chronochunk`, or `/setup` in Discord, which shows the new version and a fresh uptime.

### Setting up a new server

1. Create the VM with the first-boot file. It installs Docker, adds the swap file, makes the folders and clones the repo:

```bash
az vm create -g ChronoChunk -n chronochunk-vm --image Ubuntu2404 --size Standard_B2ats_v2 \
  --admin-username azureuser --generate-ssh-keys --custom-data deployment/cloud-init.yml
```

2. Copy `deployment/docker-compose.yml` and `deployment/update.sh` to `/opt/chronochunk/`, and create `/opt/chronochunk/.env` with the secrets above.
3. Give the data folders to the container's user: `sudo chown -R 1000:1000 /opt/chronochunk/data`.
4. Start it: `cd /opt/chronochunk && docker compose up -d --build`.

## Commands

Slash commands:

- `/chat <message>` - Talk to the bot. You can attach an image or a file.
- `/tldr <count>` - Sum up the last messages in the channel (default 50, max 200). It also looks at the newest images and files posted in them.
- `/tldr <count> <request>` - Ask about those messages or have the bot make something from them, for example `/tldr 60 what is the best option?` or `/tldr 55 make a pdf of the design discussed here`
- `/info` or `/mydata` - See what the bot has stored about you. Only you see the answer.
- `/forget` - Delete everything the bot has stored about you. `/forget <text>` deletes one fact.
- `/game <max>`, `/guess <number>`, `/end` - The number guessing game
- `/usage` - What you have left today: messages, PDFs, files, web lookups and your AI limit. Only you see the answer.
- `/model` - Which AI model the bot is running on
- `/setup` - Where the bot runs and the settings it runs with, for developers
- `/stats` - Everything the bot has used so far: AI calls, tokens and cost, by day, by job and by model
- `/recent-posts on|off` - Turn reading of just-posted images and files on or off in a channel (needs Manage Channels)
- `/help` - Show every command and the limits below
- `/code` - Link to this repository
- The music commands are listed under [Music commands](#music-commands).

You can also type to the bot without picking a slash command. A message that starts with `/`, a ping, or a reply to one of its messages is chat. `/tldr`, `/usage`, `/model`, `/setup`, `/stats` and `/help` work typed as well.

## Reading messages and files

Reply to any message and start your reply with `/`, for example `/read this` or `/summarize`. The bot reads the message you replied to and answers about it. This works for:

- text messages and link previews
- images (png, jpg, webp)
- PDFs: the text, plus a description the bot writes of each page from a picture of it, so tables, charts, diagrams and scanned pages are read too
- markdown, text and code files

You can also attach a file or image to your own `/` message. Pinging the bot works the same as starting with `/`. The `/chat` command takes an optional file too.

You do not have to reply to something that was just posted. If your message points at nothing, the bot reads the images and files from the last 5 messages (up to 30 minutes old), so dropping a screenshot and then typing `/explain this` works. Pasting a link to a Discord message makes the bot read that message's files and images too.

Reading recent posts means other people's images and files go to the model when someone chats right after them. Someone who can manage a channel can turn that off there with `/recent-posts off`. Replies, attachments and `/tldr` keep working.

Limits (also shown by `/help`):

- Images up to 5 MB each, files up to 15 MB each.
- 3 images and 4 files per message.
- PDFs are read up to 150 pages. Long text files are cut at 30,000 characters.
- The first question about a PDF takes longer, up to a minute for a long slide deck, while the bot looks at every page. Later questions about the same file are quick, for anyone, for 7 days after it was last used (`PDF_NOTES_KEEP_DAYS`). Set `PDF_PAGE_NOTES` to `off` in `.env` to skip this and read only the text and the first 10 pictures, or to `on` to describe every page of every PDF.
- From recent posts: the last 5 messages, 30 minutes old at most, 2 images and 1 file (cut at 12,000 characters).
- `/tldr`: 5 to 200 messages, plus the newest 6 images and 3 files in them (each cut at 6,000 characters), and 6 recaps per hour per user.
- GIFs, videos, voice messages and Word, Excel or PowerPoint files are not read.

The numbers live at the top of `src/message_context.py` and in `RATE_LIMITS` in `config/config.py`. `/help` reads them from there.

## Writing, documents and diagrams

Ask for an email, a proposal, a report, a cover letter or meeting notes and the bot writes it in clean professional English (or the language you asked in). Chat keeps the bot's usual personality. Work text does not.

- Ask for a file and it attaches one: `/write a one page proposal for X as a pdf`, `/turn this into meeting notes as docx`.
- Formats are PDF, DOCX and Markdown. Say "one page" and a PDF is fitted to one page when the text allows it.
- Ask for a diagram or flowchart and it sends a rendered image. Diagrams inside documents are rendered too.
- Diagrams are rendered by the public service mermaid.ink, so the diagram text leaves your server. When mermaid.ink is down, the public service kroki.io is used instead.
- A flowchart that comes out as a wide strip is drawn the other way round so the text stays readable.

How documents are written is set by the Markdown files in `skills/`. Edit them to change the voice or the rules:

- `skills/writer.md` - the writer's role and output format
- `skills/business-writing.md` - structure for emails, proposals, reports and other documents
- `skills/no-ai-tells.md` - words and patterns the writer must never use

## The web

The bot can open links and search the web.

- Give it a link with a request and it reads the page: `/sum up https://example.com/article`.
- Ask about something recent, or tell it to look something up, and it searches: `/search the latest on X`. The answer ends with the links it used.
- Ask for a file in the same message and the document is written from what it found: `/search X and make a one page pdf`.
- Search uses [Tavily](https://tavily.com). Put the key in `.env` as `TAVILY_API_KEY`. Without a key the bot cannot search, and it opens ordinary pages only: PDF links and pages that need JavaScript are read through Tavily.
- A page is downloaded from the bot's own server. When that fails or the page comes back nearly empty, Tavily reads it instead. That covers PDFs and most pages that need JavaScript.
- One reply makes at most 4 lookups and each page is cut at 12,000 characters. The numbers are at the top of `src/web_tools.py` and `src/ai_response_handler.py`.
- Links to the server itself, private networks and the cloud metadata address are refused.
- The bot cannot log in to sites. Pages behind a login or a paywall mostly come back empty.

## Privacy

The bot keeps a small record of each person it talks to: their last 20 messages to it, its answers, and facts it picked up. `/info` shows yours and `/forget` deletes it. [PRIVACY.md](PRIVACY.md) lists everything that is stored and which outside services receive your messages.

## AI budget and limits

The bot stops calling the model when it reaches its spending cap, and starts again when the cap resets.

| Setting | Default | Meaning |
|---|---|---|
| `AI_MONTHLY_BUDGET_USD` | 15 | Hard cap per billing cycle |
| `AI_DAILY_BUDGET_USD` | 2 | Hard cap per day |
| `AI_BUDGET_RESET_DAY` | 14 | Day of the month the cycle restarts |
| `AZURE_OPENAI_DEPLOYMENT` | gpt-5-mini | The chat model. `deployment/docker-compose.yml` sets it to gpt-6-luna |
| `AI_USER_DAILY_BUDGET_USD` | 0.20 | What one person may spend per day |
| `AI_USER_WEEKLY_BUDGET_USD` | 0.60 | What one person may spend over any 7 days |
| `AI_UNLIMITED_USER_IDS` | empty | Discord user ids, separated by commas, with no personal limit |
| `AI_CHAT_REASONING_EFFORT` | low | Reasoning effort for chat replies. Must be `none` for gpt-6-luna and gpt-5.6-luna, which do not take tool calls with reasoning on |
| `AZURE_OPENAI_BACKGROUND_DEPLOYMENT` | empty | A cheaper deployment, such as gpt-5-nano, for picking facts out of messages. Empty uses the main one |

Set these in `.env`. Costs are estimated from token counts and each model's price, and kept in the database, one row per day and model. `/usage` shows the current totals. Each reply also writes one log line with the images and files it read, the number of model calls and the estimated cost.

Per user, the bot answers 50 messages per 30 minutes, makes 15 files per day (2 per reply), reads 10 new PDFs in full per day and makes 20 web lookups per day. `/usage` shows each person what they have left.

## Music commands

The bot plays music in the voice channel you are in. A Spotify link is looked up by its title and artist and played from YouTube.

- `/music <link or search>` - Play music from YouTube or Spotify
- `/skip` - Skip to the next song
- `/pause` - Pause the current song
- `/resume` - Resume playback
- `/stop` - Stop playback and leave the voice channel
- `/queue` - Show the current queue
- `/volume <0-100>` - Set the volume
- `/relate` - Play a song related to the current one (YouTube recommendations)

Examples:

- `/music https://www.youtube.com/watch?v=dQw4w9WgXcQ` - Play from YouTube URL
- `/music https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT` - Play from Spotify URL
- `/music lofi beats` - Search and play
- `/relate` - Get a related song to the currently playing one

## Development

- The version number is in `src/version.py`. Every change is listed in [CHANGELOG.md](CHANGELOG.md). Change both in the same commit and tag it, for example `git tag v3.0.0`.
- Tests run in the test stage of the bot's Docker image:

```bash
docker build --target test -t chronochunk:dev .
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/app -w /app chronochunk:dev python -m pytest tests/unit tests/integration -q -p no:cacheprovider
```

- The same image has the linter and the type checker. Their settings are in `pyproject.toml`:

```bash
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/app -w /app chronochunk:dev ruff check .
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/app -w /app chronochunk:dev pyright
```

- GitHub Actions runs the linter, the type checker and the tests on every pull request and on every push to `main` (`.github/workflows/tests.yml`).
- Changes are made on a branch, such as `feature/web-search` or `fix/spotify-links`, and reach `main` through a pull request once the tests pass. The server runs `main`.
- Packages are pinned. `requirements.in` lists what the bot imports and `requirements.txt` is built from it. To change a version, edit `requirements.in` and run the command at the top of that file. `requirements-dev.in` works the same way for the test packages.
- Data is in one SQLite file, `state/chronochunk.db`. To look inside: `sqlite3 state/chronochunk.db ".tables"`. The schema is the `MIGRATIONS` list in `src/database.py`. To change it, add a new entry at the end.
- The bot copies that file to `state/backups/chronochunk-<day>.db` at startup and once a day, and keeps 7 copies. To go back to one: stop the bot, delete `state/chronochunk.db-wal` and `state/chronochunk.db-shm` if they exist, copy the backup over `state/chronochunk.db`, and start the bot.
- Logs go to `logs/chronochunk.log`. A new file starts each day and files older than 30 days are deleted.
- Every answer writes an `Answered <name> in ...` line to the log with the seconds each step took: history, context, model and tools, and send. Start there when a reply was slow.

### Project structure

```
ChronoChunk/
├── run.py                     # Starts the bot
├── config/
│   ├── config.py              # Settings and limits
│   └── ai_config.py           # The bot's personality prompt
├── skills/                    # Rules the document writer follows
├── src/
│   ├── bot.py                 # Wires the parts together
│   ├── message_processor.py   # Decides which messages get an answer
│   ├── message_handler.py     # Channel history and context
│   ├── message_context.py     # Reads replies, images and files
│   ├── pdf_notes.py           # Describes PDF pages from pictures of them
│   ├── ai_response_handler.py # Talks to the model and runs the reply loop
│   ├── tools/                 # What the model can do: one file per group of tools
│   │   ├── base.py            # The interface every tool implements
│   │   ├── documents.py       # create_document and create_diagram
│   │   └── web.py             # open_url and web_search
│   ├── document_builder.py    # Turns Markdown into PDF, DOCX and diagram images
│   ├── web_tools.py           # Fetches pages and calls the search service
│   ├── slash_commands.py      # Slash commands
│   ├── command_handler.py     # Typed /tldr, /usage and /help
│   ├── database.py            # The SQLite database and its schema
│   ├── user_store.py          # What is stored about each user
│   ├── user_data_manager.py   # Picks facts about users out of what they say
│   ├── legacy_import.py       # One-time copy of the old JSON files into the database
│   ├── usage_guard.py         # AI spending cap
│   ├── rate_limiter.py        # Limits per user
│   ├── music_manager.py       # Music
│   ├── game_manager.py        # Guessing game
│   └── web_server.py          # Health check page
├── tests/
├── deployment/                # Docker Compose and VM setup
├── requirements.in            # Packages the bot imports
└── requirements.txt           # Every package, pinned
```

## Contributing

1. Fork the repo
2. Create feature branch (`git checkout -b feature/cool-new-thing`)
3. Commit changes (`git commit -am 'Added some cool new thing'`)
4. Push branch (`git push origin feature/cool-new-thing`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
