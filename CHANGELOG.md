# Changelog

Every version of ChronoChunk, newest first. The version number lives in `src/version.py`. The bot shows it in the footer of `/help` and reports it at `/status`.

How the numbers work:

- The first number changes when an update breaks how the bot is used or set up.
- The second number changes when the bot can do something new.
- The third number changes for fixes only.

Nothing was tagged before 2.7.0. The earlier versions were worked out afterwards from the commit history, and each one is tagged at the last commit it covers. The only number written down at the time was the `1.0.0` that `/status` reported from April 2025 on, which is why that release keeps the name.

## 2.7.0 (2026-10-05)

### Added

- The bot knows its own version. `/help` shows it in the footer, `/status` reports it and the startup log prints it.
- This changelog, and a git tag for every version in it.

### Fixed

- `/status` no longer says `1.0.0`, which it had reported since April 2025.

## 2.6.1 (2026-10-05)

Commit `116f22d`.

### Fixed

- The backup diagram renderer gets a 10 second limit and a second try. Before this, a backup that hung added 30 seconds to a reply.

## 2.6.0 (2026-10-05)

Commit `4f29e3a`.

### Added

- `/recent-posts on|off` turns the reading of just-posted images and files on or off in a channel. It needs Manage Channels.
- A backup diagram renderer, kroki.io, for when mermaid.ink is down.
- One log line per reply with the images and files it read, the number of model calls and the estimated cost.
- The tests run on GitHub on every push.

### Changed

- A diagram that fails to parse is drawn again, up to 3 tries, and the bot is told which line broke.
- A failed diagram no longer counts against the daily file limit.
- A flowchart that comes out as a wide strip is drawn the other way round.
- `/tldr` answers stick to what the messages say and stay short.
- Translated documents get their title and headings translated too.

### Fixed

- The bot said a diagram was sent when it never rendered.
- Sequence diagrams showed quotes around names and cut off the text of notes.
- A `/tldr` task list could contain tasks and deadlines nobody wrote.

## 2.5.0 (2026-10-05)

Commit `ee510cf`.

### Added

- `/tldr <count> <request>` answers a question about the last messages or makes a file from them.
- `/tldr` looks at the newest images and files in the messages it reads.
- Pictures inside PDFs are read, up to 10 per request. A scanned PDF is read from its page pictures.
- A message that points at nothing reads the images and files posted just before it.
- Pasting a link to a Discord message also reads that message's files and images.
- `/help` lists the bot's limits, read from the settings that enforce them.
- A typed `/help` shows the same menu as the slash command.

### Changed

- `/tldr` answers as a reply to the message that asked, and the typing indicator shows while it works.
- `/tldr` followed by words and no number is a request about the last 50 messages. It used to answer "gimme a number".
- `/help` has no swearing.

### Fixed

- Diagrams came back as raw Mermaid text. Labels with brackets or with `<T>` in them made the renderer fail or lose text.

## 2.4.0 (2026-10-05)

Commit `cbc794d`.

### Added

- Documents as PDF, DOCX or Markdown, written in clean professional language.
- Diagrams and flowcharts rendered to images.
- `/tldr` and `/usage`, as slash commands and as typed commands.
- `/chat` takes an attached file.
- A spending cap for the AI, 15 dollars a month and 2 dollars a day by default.
- Limits per person: 50 chat messages per 30 minutes, 15 files per day and 6 recaps per hour.
- The writing rules live in `skills/` and can be edited.

### For whoever runs the bot

- New optional settings: `AI_MONTHLY_BUDGET_USD`, `AI_DAILY_BUDGET_USD`, `AI_BUDGET_RESET_DAY`, `AI_CHAT_REASONING_EFFORT` and three `AI_PRICE_*` values.
- A `state/` folder holds the spending ledger.
- The Docker image now installs pandoc, pango and the Noto fonts.

## 2.3.0 (2026-10-05)

Commits `5a98c78` to `4d80068`.

### Added

- The bot reads the message you reply to, with its images, PDFs and text or code files.
- It reads files and images attached to your own message.
- Deployment files for an Azure VM: Docker Compose, cloud-init and an update script.

### Changed

- Answers are sent as real Discord replies, and they can no longer ping anyone.

### Fixed

- Chat history reached the AI in a scrambled order.

## 2.2.0 (2026-07-18)

Commits `8540e6f` to `bb5cbc7`.

### Added

- A link to a Discord message is fetched and read.
- The bot keeps a list of phrases it used recently and avoids repeating them.

### Fixed

- The bot said it could not open a link it had already fetched.
- Raw error text is no longer sent to people.

## 2.1.0 (2026-07-17)

Commits `7935952` to `ec7a9b4`.

### Added

- A person's first AI chat gives them the "I Love Chrono <3" role.
- Recent messages count for more than old ones when the bot reads the chat.

### Changed

- Long dashes are removed from replies.

### Fixed

- The `/info` and `/mydata` slash commands crashed.

## 2.0.0 (2026-07-16)

Commits `c63be50` to `bbe0fa9`.

### Changed, and breaking for whoever runs the bot

- The AI moved from Google Gemini to Azure OpenAI, on a gpt-5-mini deployment. `GEMINI_API_KEY` is no longer used. Set `AZURE_OPENAI_KEY`, `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_DEPLOYMENT`.

### Added

- A Dockerfile.
- The first test suite.
- Replies longer than Discord allows are split into several messages.

### Changed

- The personality prompt was rewritten and is about 70 percent shorter.
- A typed command the bot does not know gets a chat answer, where it used to say "Command not recognized".

### Fixed

- The bot talked about the person it was answering in the third person.
- It forgot the conversation it was in.
- Game state and its own error messages leaked into chat answers.
- Emoji spam, announcing what it was about to do, and bringing old topics back.

## 1.0.0 (2025-04-01)

Commit `bf216cf`.

### Added

- Music: `/music`, `/skip`, `/pause`, `/resume`, `/stop`, `/queue`, `/volume`, `/relate` and `/testaudio`.
- `/help`.
- A `/status` page. It reported version `1.0.0`.
- `run.py` with a clean shutdown and a log file per day.
- A systemd unit and a deploy script.

### For whoever runs the bot

- FFmpeg is needed for music.

## 0.3.0 (2025-03-24)

Commits `59cbe73` to `46ff2ce`.

### Added

- A cache for answers.
- Ready-made fallback lines for when the AI quota runs out.

### Changed

- Answers got longer, 2 to 5 sentences.
- Typed `/command` messages all became chat. The Discord slash commands kept working.
- Requests such as "play a game" are spotted by patterns and no longer by the AI.
- Rate limiting was removed. It came back in 2.4.0.

## 0.2.0 (2025-03-19)

Commits `8ca7867` and `3e3691c`.

### Changed

- The bot was rebuilt as an AI chat bot on Google Gemini, with its own personality and a memory of facts about each person.

### Added

- `/chat`, `/mydata`, `/info`, `/forget` and `/good-boy`.
- The first Discord slash commands.

### Removed

- `/code` no longer splits an attached text file into code blocks. It replies with a link to the source code.

### For whoever runs the bot

- `GEMINI_API_KEY` is needed.
- The health server listens on port 8080. The `PORT` setting is no longer read.

## 0.1.0 (2025-03-07)

Commits `982d07d` to `9cf3252`.

### Added

- The first bot: a number guessing game with `/game`, `/guess` and `/end`.
- `/code` splits an attached text file into Discord code blocks.
- A health page and a redeploy script for hosting on Render.

### Fixed

- The bot answered twice because its startup code ran twice.
