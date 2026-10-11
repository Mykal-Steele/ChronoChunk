# ChronoChunk Discord Bot

A Discord bot with a personality. It chats, remembers things about the people it talks to, reads images and files, writes documents, searches the web and plays music. The AI is gpt-5-mini on Azure OpenAI.

## Features

- Chat in the bot's own voice, with memory of who it is talking to
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

## Commands

Slash commands:

- `/chat <message>` - Talk to the bot. You can attach an image or a file.
- `/tldr <count>` - Sum up the last messages in the channel (default 50, max 200). It also looks at the newest images and files posted in them.
- `/tldr <count> <request>` - Ask about those messages or have the bot make something from them, for example `/tldr 60 what is the best option?` or `/tldr 55 make a pdf of the design discussed here`
- `/info` or `/mydata` - See what the bot has stored about you. Only you see the answer.
- `/forget` - Delete everything the bot has stored about you. `/forget <text>` deletes one fact.
- `/game <max>`, `/guess <number>`, `/end` - The number guessing game
- `/usage` - How much of the AI budget is used
- `/recent-posts on|off` - Turn reading of just-posted images and files on or off in a channel (needs Manage Channels)
- `/help` - Show every command and the limits below
- `/code` - Link to this repository
- The music commands are listed under [Music commands](#music-commands).

You can also type to the bot without picking a slash command. A message that starts with `/`, a ping, or a reply to one of its messages is chat. `/tldr`, `/usage` and `/help` work typed as well.

## Reading messages and files

Reply to any message and start your reply with `/`, for example `/read this` or `/summarize`. The bot reads the message you replied to and answers about it. This works for:

- text messages and link previews
- images (png, jpg, webp)
- PDFs: the text, and the pictures inside them such as charts, photos and scanned pages
- markdown, text and code files

You can also attach a file or image to your own `/` message. Pinging the bot works the same as starting with `/`. The `/chat` command takes an optional file too.

You do not have to reply to something that was just posted. If your message points at nothing, the bot reads the images and files from the last 5 messages (up to 30 minutes old), so dropping a screenshot and then typing `/explain this` works. Pasting a link to a Discord message makes the bot read that message's files and images too.

Reading recent posts means other people's images and files go to the model when someone chats right after them. Someone who can manage a channel can turn that off there with `/recent-posts off`. Replies, attachments and `/tldr` keep working.

Limits (also shown by `/help`):

- Images up to 5 MB each, files up to 15 MB each.
- 3 images and 4 files per message, plus up to 10 pictures from inside PDFs.
- PDFs are read up to 60 pages. Long files are cut at 30,000 characters.
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

The bot keeps a small file about each person it talks to: their last 20 messages to it, its answers, and facts it picked up. `/info` shows yours and `/forget` deletes it. [PRIVACY.md](PRIVACY.md) lists everything that is stored and which outside services receive your messages.

## AI budget and limits

The bot stops calling the model when it reaches its spending cap, and starts again when the cap resets.

| Setting | Default | Meaning |
|---|---|---|
| `AI_MONTHLY_BUDGET_USD` | 15 | Hard cap per billing cycle |
| `AI_DAILY_BUDGET_USD` | 2 | Hard cap per day |
| `AI_BUDGET_RESET_DAY` | 14 | Day of the month the cycle restarts |
| `AI_CHAT_REASONING_EFFORT` | low | Reasoning effort for chat replies |

Set these in `.env`. Costs are estimated from token counts at gpt-5-mini prices and kept in `state/ai_usage.json`. `/usage` shows the current totals. Each reply also writes one log line with the images and files it read, the number of model calls and the estimated cost.

Per user, the bot answers 50 messages per 30 minutes and makes 15 files per day, 2 per reply.

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

- GitHub Actions runs the same tests on every pull request and on every push to `main` (`.github/workflows/tests.yml`).
- Changes are made on a branch, such as `feature/web-search` or `fix/spotify-links`, and reach `main` through a pull request once the tests pass. The server runs `main`.
- Packages are pinned. `requirements.in` lists what the bot imports and `requirements.txt` is built from it. To change a version, edit `requirements.in` and run the command at the top of that file. `requirements-dev.in` works the same way for the test packages.
- Logs go to `logs/chronochunk.log`. A new file starts each day and files older than 30 days are deleted.

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
│   ├── ai_response_handler.py # Talks to the model and runs its tools
│   ├── document_builder.py    # PDF, DOCX and diagrams
│   ├── web_tools.py           # Web search and link reading
│   ├── slash_commands.py      # Slash commands
│   ├── command_handler.py     # Typed /tldr, /usage and /help
│   ├── user_data_manager.py   # What is stored about each user
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
