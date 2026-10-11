# Privacy

This page says what ChronoChunk keeps about you, where it goes, and how to delete it.

## What the bot stores

When you talk to the bot (a `/` message, a slash command, a ping or a reply to it), it saves a small file about you on its own server:

- Your Discord user ID and display name.
- Your last 20 messages to the bot and its answers.
- Short facts it picks out of what you tell it, such as "likes hiking", and the topics you talk about.

It uses these to remember you between conversations. The files are not shared with anyone and are not in this repository.

## What the bot reads but does not store

- Other messages in a channel. The bot holds the last 40 per channel in memory so it can follow the conversation. They are never written to disk and are gone when the bot restarts.
- Images, PDFs and files you point it at, and web pages it opens. They are read for that one answer and not saved.

## Logs

The bot writes a log on its server. It records your display name, the time, what each answer cost, the web searches and links it looked up for you, and the songs you asked for. It does not record what you wrote in chat. Log files are deleted after 30 days.

## Who else receives your content

The bot cannot answer without sending some of what you write to other services:

| Service | What it gets | When |
|---|---|---|
| Microsoft Azure OpenAI | Your message, the recent channel messages, the facts stored about you, and any image or file you point the bot at | Every answer |
| Tavily | Your search words, and links the bot cannot open on its own | When the bot searches the web or opens a link |
| The website behind a link | A normal page request from the bot's server | When you give the bot a link |
| mermaid.ink, and kroki.io as a backup | The text of a diagram | When the bot draws a diagram |
| YouTube and Spotify | Your song search or link | When you play music |

## See or delete your data

- `/info` or `/mydata` shows what the bot has stored about you. Only you see the answer.
- `/forget` deletes everything stored about you.
- `/forget <text>` deletes the one fact that matches.

Nothing is deleted on a timer. Your file stays until you run `/forget`, or until the person running the bot removes it.

## Questions

Ask the person who runs the bot on your server, or open an issue at https://github.com/Mykal-Steele/ChronoChunk/issues.
