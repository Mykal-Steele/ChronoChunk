# Changelog

Every version of ChronoChunk, newest first. The version number lives in `src/version.py`. The bot shows it in the footer of `/help` and reports it at `/status`.

How the numbers work:

- The first number changes when an update breaks how the bot is used or set up.
- The second number changes when the bot can do something new.
- The third number changes for fixes only.

Nothing was tagged before 2.7.0. The earlier versions were worked out afterwards from the commit history, and each one is tagged at the last commit it covers. The only number written down at the time was the `1.0.0` that `/status` reported from April 2025 on, which is why that release keeps the name.

## 3.11.0 (2026-10-12)

### Added

- The live bot can be tested by another bot. With `TEST_BOT_ID` and `TEST_CHANNEL_ID` set, the bot answers that one bot in that one channel. It still ignores every other bot, and that bot everywhere else. With either one empty, which is the default, nothing changes. `/setup` shows whether it is on.

## 3.10.1 (2026-10-12)

### Fixed

- The bot no longer waits for Discord to confirm the typing indicator before it starts on an answer. On the server that wait was about 0.3 seconds of every reply, and over 2.5 seconds on 2 of the first 15. The indicator still shows.
- A typing indicator that Discord refuses no longer costs the answer.

## 3.10.0 (2026-10-12)

### Added

- `/stats` shows everything the bot has used since its spending record began: model calls, tokens in and out, and the AI cost, in all, today, over the last 7 days and this month. It also shows the busiest day, the split by job and by model, how many people and facts it remembers, and how many PDFs it has described. It works typed as well. No name and no user id is in it.
- The bot copies its database to `state/backups` at startup and once a day, and keeps the newest 7 copies. A damaged file or a bad change can be undone from the copy of the day before. `/stats` shows the day of the last copy.

### Changed

- `/setup` ends with a short version of those totals and points to `/stats`.
- `/setup` no longer shows the path of the database file or of the deploy script.
- The privacy page says that the copies keep something you delete for up to 7 more days.

## 3.9.0 (2026-10-12)

### Added

- `/setup` shows where the bot runs and the settings it runs with: the host, the Python and discord.py versions, uptime and memory, both AI models, the budgets and the limits. It works typed as well. No key, token, address or user id is in it.
- On an Azure VM the bot asks Azure for the VM's name, size and region at startup, so `/setup` names the real machine. Anywhere else it says it is not on an Azure VM.
- The README has a "Where it runs" section: the server, its folders, every setting, how to deploy a change and how to set up a new server.

## 3.8.0 (2026-10-12)

### Added

- The log has one `Answered` line per answer that says how long each step took: reading the channel history, reading what the message points at, the model and its tools, and sending the answer. It also says how long the message took to reach the bot. A slow reply can be traced to the step that was slow.
- The `Reply for` line says how much of the time went to tools, not counting the model calls a tool makes.

## 3.7.1 (2026-10-12)

### Fixed

- `/music` and `/relate` in a DM answer that you have to be in a voice channel. Before, they failed with "something broke".
- A song that ends just after the bot leaves the voice channel no longer raises an error in the player.

### Changed

- Every pull request is checked by a linter (ruff) and a type checker (pyright) before the tests run. Their first pass found the two problems above. Settings are in `pyproject.toml`.

## 3.7.0 (2026-10-12)

### Changed

- The descriptions the bot writes of a PDF's pages are kept in the database for 7 days after they were last used, for up to 200 files. A restart or a deploy no longer makes the bot read every slide deck again, and a deck one person has asked about is ready for everyone else that week. `PDF_NOTES_KEEP_DAYS` sets the number of days, and 0 keeps them in memory only, as before.
- A stored description is found by the file's hash. No filename and no user is stored with it.

## 3.6.0 (2026-10-12)

### Added

- `/usage` is personal. It shows what you have left of each allowance (messages, PDFs read in full, files made, web lookups, recaps) and how much of your AI limit and the bot's is used, as bars with a reset time. The slash command answers only to you.
- Every person has their own AI limit: $0.20 a day and $0.60 over any 7 days by default (`AI_USER_DAILY_BUDGET_USD`, `AI_USER_WEEKLY_BUDGET_USD`). One person can no longer use up the bot for everyone. A heavy study day costs about $0.13, so normal use stays far below it.
- Past 80% of the daily limit, one reply that day ends with a small heads-up. At the limit the bot says when it resets.
- Two new daily counts: 10 PDFs read in full and 20 web lookups. A PDF that the bot has already described is free. Past the PDF count a PDF is read from its text and first pictures, and the bot says why.
- `AI_UNLIMITED_USER_IDS` lists Discord user ids the AI limits do not apply to.

### Changed

- The spending ledger records who each model call was for and what it was doing (chat, facts, document, pdf_notes, music).
- The bot-wide caps of $2 a day and $15 a month stay as the backstop.

## 3.5.0 (2026-10-11)

### Changed

- The bot keeps its data in one SQLite database, `state/chronochunk.db`, in place of loose JSON files: what it remembers about people, the AI spending ledger, the channel switches and the rate limit counts. SQLite runs inside the bot, so there is no extra server.
- On first start the bot copies the old files into the database: every `user_data/<id>.json`, `state/ai_usage.json` and `state/channel_settings.json`. The old files are left where they are and are no longer read.
- The spending ledger keeps a row per day and per model, where it used to keep one running total.
- The message, file and recap limits survive a restart. A deploy used to hand everyone a fresh allowance.

### Fixed

- Two things changing one person's data at the same moment can no longer overwrite each other. Each change is its own small transaction. The workaround that kept `/forget` from being undone is replaced by a check inside the database.
- A new like, dislike or belonging no longer deletes an older one. Only a new age or a new home replaces the old value.
- A sentence that starts with "You are" is no longer mistaken for someone's age.

### For whoever runs the bot

- Nothing to do. The database is created in the `state` folder that already survives rebuilds.
- To go back to 3.4.0, the JSON files are still there, as they were at the moment of the upgrade.

## 3.4.0 (2026-10-11)

### Added

- The bot reads slides properly. When someone attaches, replies to or links a PDF, it looks at a picture of each page once and writes down what the page shows: text inside pictures, tables, formulas, charts and diagrams. Questions are then answered from the PDF's text plus those descriptions. On two real lecture decks it answered 18 of 18 questions about pictures, tables and charts, against 2 of 18 before.
- `PDF_PAGE_NOTES` chooses how PDFs are read. `auto` (the default) describes pages that hold a picture, a drawing or almost no text, which is nearly every slide and nearly no page of a plain report. `on` describes every page. `off` is the old behaviour.

### Changed

- PDFs are read up to 150 pages, up from 60.
- The first question about a PDF waits for the descriptions: about 13 seconds for 21 slides and under a minute for 90. Later questions about the same file are faster than before, because the descriptions are kept in memory for 6 hours. They are never written to disk.
- A described PDF no longer sends its embedded pictures to the model, since the descriptions cover them.
- `/tldr` and the reading of just-posted files still read a PDF's text and first pictures only.

### For whoever runs the bot

- New package: `pypdfium2`, which turns PDF pages into pictures.
- Describing a deck costs about the same as five questions about it: $0.003 for 21 slides and $0.012 for 88 on gpt-6-luna. Each question then costs 26% to 42% more than before, because the descriptions are part of the prompt.

## 3.3.0 (2026-10-11)

### Added

- `/model` shows which AI model the bot is running on, the model it uses for memory, and the bot's version. It works as a slash command and typed.

### Changed

- The `openai` package moves from 2.28 to 3.26. Chat, image reading, web search, diagrams, documents, fact extraction and the music lookup were all run against the live service on the new version.

## 3.2.0 (2026-10-11)

### Changed

- The bot's own server now runs on gpt-6-luna instead of gpt-5-mini. Five models were tested on the bot's real prompts and tools. gpt-6-luna picked the right tool 14 times out of 14, answered in about 2 seconds against 3.4, and a typical reply cost about a tenth as much. Its roasts are shorter and a little tamer. The switch is two lines in `deployment/docker-compose.yml`, and the code still defaults to gpt-5-mini.
- gpt-6-luna and gpt-5.6-luna only accept tool calls with reasoning switched off, so the compose file sets `AI_CHAT_REASONING_EFFORT` to `none`.
- The spending cap knows the prices of gpt-6-luna and gpt-5.6-luna, including their extra charge for writing the prompt cache.

### For whoever runs the bot

- Copy the new `deployment/docker-compose.yml` to the server. It needs a `gpt-6-luna` deployment on the Azure resource.

## 3.1.1 (2026-10-11)

Nothing changes for people using the bot. What is sent to the model is identical to 3.1.0.

### Changed

- The things the model can do (make a document, draw a diagram, open a link, search the web) are separate tools in `src/tools/`, each with the same small interface. Before this they were branches inside the reply code. A new ability is a new file plus one line in `default_tools()`.

## 3.1.0 (2026-10-11)

### Added

- A second, cheaper model for work that happens behind the reply. Set `AZURE_OPENAI_BACKGROUND_DEPLOYMENT` to a gpt-5-nano deployment and the bot picks facts out of messages with it. On a test set of 14 messages it found the same facts as gpt-5-mini, in 0.8 seconds against 2.8, at about one eighteenth of the cost. Without the setting nothing changes.
- The spending cap prices each call by the model that answered it.

### Changed

- The music search rewrite uses low reasoning effort. It picked the same songs in testing and no longer runs into its 5 second limit. It stays on gpt-5-mini, because gpt-5-nano picked wrong songs.

### For whoever runs the bot

- Copy the new `deployment/docker-compose.yml` to the server. It sets `AZURE_OPENAI_BACKGROUND_DEPLOYMENT`.

## 3.0.1 (2026-10-11)

### Fixed

- `/chat` sends its answer before it saves the conversation. Saving also picks facts out of the message, which is a second model call, and people waited for it.

### Changed

- The log line for each reply says how long the reply took and how much of that was spent waiting for the model.

## 3.0.0 (2026-10-11)

The first number changed because an existing install needs two steps before this version starts. See "Upgrading".

### Upgrading

- The bot no longer runs as root inside its container. Give the data folders to user 1000 once: `sudo chown -R 1000:1000 /opt/chronochunk/data`.
- Copy the new `deployment/docker-compose.yml` to the server. It holds the memory cap and the security settings.
- For web search, add `TAVILY_API_KEY` to `.env`.
- The server now runs the `main` branch. Switch its checkout once: `git -C /opt/chronochunk/app checkout main`.

### Added

- The bot opens web links. Give it a link with a request, such as `/sum up https://...`, and it reads the page and answers from it. The page is downloaded from the bot's own server. Pages that come back nearly empty, PDFs and sites that block plain downloads are read through Tavily instead.
- The bot searches the web when asked about something recent or told to look something up. It ends the answer with the links it used. Search needs `TAVILY_API_KEY` in `.env`. Without the key the bot still opens ordinary pages.
- A document made in the same reply is written from what the bot looked up.
- `/help` has a section about the web and a section about what the bot stores.
- `PRIVACY.md` lists what the bot stores about people, which outside services receive their messages, and how to delete it.

### Changed

- The container runs as an unprivileged user, cannot write to its own code, has no Linux capabilities and is capped at 640 MB of memory.
- aiohttp, requests, urllib3 and idna are upgraded past known security bugs. PyNaCl stays at 1.5.0, which has two known bugs, because discord.py does not allow a newer one yet.
- Every Python package is pinned. `requirements.in` lists the packages the bot imports and `requirements.txt` is built from it. Eleven packages nothing imported are gone. The test packages moved to `requirements-dev.txt` and a `test` stage in the Dockerfile.
- One log file, `logs/chronochunk.log`, that starts fresh each day. Files older than 30 days are deleted. Before this a log file grew for as long as the bot stayed up and nothing was ever deleted.
- Error messages from commands no longer swear.
- Command code that could not be reached is gone: the typed versions of the game, data and music commands had been switched off since the slash commands took over. `/testaudio` is gone too.

### Fixed

- Two people's saved chats were in the public repository. They are removed from the branch. They are still in the git history.
- `/forget` with nothing after it deletes everything stored about the person. Before this it said "wiped all your data" but kept their last 20 saved conversations.
- `/info` shows the facts when only one or two are stored. It used to say the bot knew nothing.
- A `/forget` sent while the bot is still picking facts out of a message is no longer undone when that finishes.
- `/info`, `/mydata` and `/forget` answer only to the person who asked. Before this the facts were posted to the whole channel.
- A Spotify link plays the right song. The lookup needed a package that was never installed, so the bot searched YouTube for the link's ID instead of the title. The lookup also no longer freezes the bot while it waits for Spotify.
- The bot leaves a voice channel after 10 minutes alone or idle. The check ran on a second music player that never had anything in it.

### Limits

- One reply makes at most 4 web lookups, and each page is cut at 12,000 characters.
- Links to the server itself, to private networks and to Azure's metadata address are refused, including after a redirect.
- Answers that used the web are not cached, so the same question is looked up again.

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
