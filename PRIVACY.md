# Privacy

This page says what ChronoChunk keeps about you, where it goes, and how to delete it.

## What the bot stores

When you talk to the bot (a `/` or `!` message, a slash command, a ping or a reply to it), it saves a few rows about you in a database on its own server:

- Your Discord user ID and display name.
- Your last 20 messages to the bot and its answers.
- Short facts it picks out of what you tell it, such as "likes hiking", and the topics you talk about.

It uses these to remember you between conversations: the facts and your last 3 exchanges with the bot are given to the AI model when you talk to it. The database is not shared with anyone and is not in this repository. It also keeps the time of your recent messages, files, PDF reads and web lookups for up to a day, to enforce the limits in `/help`, and a daily total of what your requests cost in AI spending, which is what `/usage` shows you.

## What the bot reads but does not store

- Other messages in a channel. The bot holds the last 50 per channel in memory so it can follow the conversation. They are never written to disk and are gone when the bot restarts.
- A message you did not address to the bot, when someone called it in that channel in the last 30 seconds. The bot may answer it. The message goes to the AI model like the rest of the recent channel messages, and nothing about you is saved.
- Images, PDFs and files you point it at, and web pages it opens. They are read for the answer and not saved.
- For a PDF, the bot writes a description of each page so it can answer about charts and tables. It keeps those descriptions in its database for 7 days after they were last used, so follow-up questions are fast and the same file does not have to be read again. A description is stored under a fingerprint of the file, with no filename and no link to who sent it. The PDF itself is not kept.
- Course material. The bot holds a copy of the class files, lecture transcripts and assignments of the courses its owner syncs from Microsoft Teams. Names that Teams marks in a lecture transcript are taken out, which misses a name written like any other word. An assignment is kept as course material only: nothing about who handed in what, no grades and no class members. This copy is the same for everyone and holds nothing about the people who ask.
- Course mode. When you end a message with `-c` or a course code, the bot keeps what it looked up for you in memory for an hour, so you can keep asking. It is never written to disk, and it is gone after the hour or when the bot restarts.
- Server emoji. The bot looks at the picture of each custom emoji once and keeps a few words on what it shows, with the emoji's id and name. No message and no person is stored with it.

## Logs

The bot writes a log on its server. It records your display name, the time, what each answer cost, the web searches and links it looked up for you, and the songs you asked for. It does not record what you wrote in chat. Log files are deleted after 30 days.

## Who else receives your content

The bot cannot answer without sending some of what you write to other services:

| Service | What it gets | When |
|---|---|---|
| Microsoft Azure OpenAI | Your message, the recent channel messages, the facts stored about you, your last 3 exchanges with the bot, and any image or file you point the bot at | Every answer |
| Microsoft Azure OpenAI | The picture and name of each custom server emoji | Once per new emoji |
| Microsoft Azure OpenAI | Your message, to turn it into a search of the course material, and the passages that search found | When your message is in course mode |
| Tavily | Your search words, and links the bot cannot open on its own | When the bot searches the web or opens a link |
| The website behind a link | A normal page request from the bot's server | When you give the bot a link |
| mermaid.ink, and kroki.io as a backup | The text of a diagram | When the bot draws a diagram |
| YouTube and Spotify | Your song search or link | When you play music |

## See or delete your data

- `/info` or `/mydata` shows what the bot has stored about you. Only you see the answer.
- `/forget` deletes everything stored about you.
- `/forget <text>` deletes the one fact that matches.

Nothing about you is deleted on a timer. Your rows stay until you run `/forget`, or until the person running the bot removes them.

The bot copies its database once a day, on the same server, so a damaged file does not lose everyone's data. It keeps 7 copies. What you delete with `/forget` is gone from the database at once and from the last copy 7 days later. The copies are not shared with anyone.

## Questions

Ask the person who runs the bot on your server, or open an issue at https://github.com/Mykal-Steele/ChronoChunk/issues.
