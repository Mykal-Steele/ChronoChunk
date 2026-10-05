# ChronoChunk Discord Bot

A Discord bot that remembers stuff about users and plays games with them. Uses Google's Gemini AI to extract and manage user information.

## Features

- 🎮 Number guessing game
- 🧠 Remembers facts about users from their messages
- 📝 Tracks topics users are interested in
- 🗑️ Let users delete info about themselves
- 💬 Natural conversation style

## Setup

1. Clone the repo:

```bash
git clone https://github.com/Mykal-Steele/ChronoChunk.git
cd chronochunk
```

2. Create a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Create a `.env` file with your tokens:

```
DISCORD_TOKEN=your_discord_token
GEMINI_API_KEY=your_gemini_api_key
```

5. Run the bot:

```bash
python src/bot.py
```

## Commands

- `/game <max>` - Start a number guessing game (1 to max)
- `/guess <number>` - Make a guess in the game
- `/end` - End current game
- `/mydata` - See what info the bot has about you
- `/forget <text>` - Make the bot forget specific info
- `/code` - Format code for Discord (React only rn)
- `/help` - Show every command and the limits below
- `/recent-posts on|off` - Turn reading of just-posted images and files on or off in a channel (needs Manage Channels)
- `/tldr <count>` - Sum up the last messages in the channel (default 50, max 200). It also looks at the newest images and files posted in them.
- `/tldr <count> <request>` - Ask about those messages or have the bot make something from them, for example `/tldr 60 what is the best option?` or `/tldr 55 make a pdf of the design discussed here`

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

## Music Commands

ChronoChunk now supports playing music from YouTube and Spotify links:

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

- Code is in `src/` directory
- The version number is in `src/version.py`. Every change is listed in [CHANGELOG.md](CHANGELOG.md). Change both in the same commit and tag it, for example `git tag v2.7.0`.
- Tests are in `tests/` directory. They run in the bot's Docker image: `docker build -t chronochunk:dev . && docker run --rm -v "$PWD":/app -w /app chronochunk:dev python -m pytest tests/unit tests/integration -q`
- GitHub Actions runs the same tests on every push to `ai/chat` and `main` (`.github/workflows/tests.yml`)
- Config is in `config/` directory
- User data stored in `data/` directory
- Logs go to `logs/` directory

### Project Structure

```
chronochunk/
├── src/
│   ├── bot.py              # Main bot code
│   ├── command_handler.py  # Command handling
│   ├── game_manager.py     # Game logic
│   ├── user_data_manager.py# User data stuff
│   ├── rate_limiter.py     # Rate limiting
│   ├── exceptions.py       # Custom errors
│   └── logger.py           # Logging setup
├── config/
│   └── config.py          # Bot config
├── tests/                 # Tests (TODO)
├── data/                  # User data storage
├── logs/                 # Log files
└── requirements.txt      # Dependencies
```

## Contributing

1. Fork the repo
2. Create feature branch (`git checkout -b feature/cool-new-thing`)
3. Commit changes (`git commit -am 'Added some cool new thing'`)
4. Push branch (`git push origin feature/cool-new-thing`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
