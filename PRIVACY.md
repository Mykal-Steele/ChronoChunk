# Privacy

This page says what ChronoChunk keeps about you, where it goes, and how to delete it.

## What the bot stores

When you talk to the bot (a `/` message, a slash command, a ping or a reply to it), it saves a few rows about you in a database on its own server:

- Your Discord user ID and display name.
- Your last 20 messages to the bot and its answers.
- Short facts it picks out of what you tell it, such as "likes hiking", and the topics you talk about.

- What you teach it. When you tell the bot something worth keeping ("remember that the exam moved to tuesday", "i'm allergic to peanuts"), it saves that sentence with your name, the server, the channel and the time. These memories are shared by every server the bot is in, so it can tell someone in another server "Slade told me he is allergic to peanuts". A correction replaces the old memory. Only you can change what you said about yourself.

It uses these to remember you between conversations: the facts and your last 3 exchanges with the bot are given to the AI model when you talk to it. The database is not shared with anyone and is not in this repository. It also keeps the time of your recent messages, files, PDF reads and web lookups for up to a day, to enforce the limits in `/help`, and a daily total of what your requests cost in AI spending, which is what `/usage` shows you.

## What the bot reads, and how long it keeps it

- Every message in the server channels it can read, including from people who never talk to it. The bot saves each one with the author's name and ID, the server, the channel and the time, for `CHANNEL_LOG_KEEP_DAYS` (30 days by default), and then deletes it. An edit in Discord changes the saved text and a deleted message is deleted here too. DMs are not saved this way.
- A summary of each channel's day. Each night, for every channel with at least 5 messages that day, the AI model gets that day's messages and writes a few lines on what was said, with names. The summaries are kept after the messages are deleted. The bot only uses one when answering in the same server, for someone who can see the channel it came from. `/forget` deletes every summary of a day you spoke on, so it also takes out what others said that day.
- How you want the bot to act. When you tell it to act differently ("stop roasting me", "keep it short"), or the night finds that in the day's messages or in a 👍 or 👎 you put on its reply, it saves a short lesson with your name. Your reactions on its replies are saved with the message for the same 30 days. A lesson about you applies in every server, and one for a server only there. `/forget` deletes the lessons about you and the ones you asked for.
- The bot also holds the last 50 messages per channel in memory so it can follow the conversation. The recent messages sent to the model are kept, encrypted, in the reply log described below for up to `REPLY_LOG_KEEP_DAYS` (30 days by default).
- A message you did not address to the bot, when someone called it in that channel in the last 30 seconds. The bot may answer it. If it does, the message, your display name, the conversation context and the answer are kept in the encrypted reply log described below for up to `REPLY_LOG_KEEP_DAYS`. That reply is not recorded with your Discord user ID.
- Images, PDFs and files you point it at, and web pages it opens. The files themselves are not saved. The text the bot read from them, and the answer it wrote, are kept in the reply log described below, encrypted, for up to `REPLY_LOG_KEEP_DAYS`.
- For a PDF, the bot writes a description of each page so it can answer about charts and tables. It keeps those descriptions in its database for 7 days after they were last used, so follow-up questions are fast and the same file does not have to be read again. A description is stored under a fingerprint of the file, with no filename and no link to who sent it. The PDF itself is not kept.
- Course material. The bot holds a copy of the class files, lecture transcripts and assignments of the courses its owner syncs from Microsoft Teams. Names that Teams marks in a lecture transcript are taken out, which misses a name written like any other word. An assignment is kept as course material only: nothing about who handed in what, no grades and no class members. This copy is the same for everyone and holds nothing about the people who ask.
- Course mode. When you end a message with `-c` or a course code, the bot keeps what it looked up for you in memory for an hour, so you can keep asking. It is never written to disk, and it is gone after the hour or when the bot restarts.
- Server emoji. The bot looks at the picture of each custom emoji once and keeps a few words on what it shows, with the emoji's id and name. No message and no person is stored with it.

## Logs

The bot writes a log on its server. It records your display name, the time, what each answer cost, the web searches and links it looked up for you, and the songs you asked for. This rotating text log does not record what you wrote in chat. A separate encrypted reply log does, as described under "See or delete your data". Log files are deleted after 30 days.

## Who else receives your content

The bot cannot answer without sending some of what you write to other services:

| Service | What it gets | When |
|---|---|---|
| The AI provider (Google Vertex AI, Gemini, since November 2026) | Your message, the recent channel messages, the facts stored about you, memories that match your message, your last 3 exchanges with the bot, and any image or file you point the bot at | Every answer |
| The AI provider | Each memory you teach, and your message, to turn them into embeddings so the bot can find memories by meaning | When something is remembered, on every answer once there are memories, and when the bot starts, for memories that were saved without one or before a model change |
| The AI provider | One channel's messages from one day, to write that day's summary, and pairs of memories that say nearly the same thing, to merge repeats and corrections | Once a night |
| The AI provider | The picture and name of each custom server emoji | Once per new emoji |
| The AI provider | Your message, to turn it into a search of the course material, and the passages that search found | When your message is in course mode |
| Tavily | Your search words, and links the bot cannot open on its own | When the bot searches the web or opens a link |
| The website behind a link | A normal page request from the bot's server | When you give the bot a link |
| mermaid.ink, and kroki.io as a backup | The text of a diagram | When the bot draws a diagram |
| YouTube and Spotify | Your song search or link | When you play music |
| Cloudflare R2 | Encrypted copies of the database (profiles, facts, memories, conversations, saved channel messages and day summaries) and of the encrypted reply log. They are encrypted on the bot's server before they leave, and R2 deletes them after 30 days | Every night |

## See or delete your data

- `/info` or `/mydata` shows what the bot has stored about you. Only you see the answer.
- Notes you ask the bot to keep. It asks whether a note is public, so anyone can ask for it, or private, so only you get it back. A private note is still stored on the bot's server like everything else, and the person running the bot can read the database.
- `/memory` shows what the bot remembers about you, with a code for each item, and `/memory delete:<code>` deletes one. A server manager can see and delete what it learned in their server with `/memory view:server`.
- `/forget` deletes everything stored about you, including what you taught the bot, your saved channel messages, the day summaries you are in, the lessons about you or from you, and the encrypted reply-log rows tied to your user hash.
- `/forget <text>` deletes the one fact that matches.

Your profile, facts and conversations stay until you run `/forget`, or until the person running the bot removes them. The encrypted reply log is kept for `REPLY_LOG_KEEP_DAYS` (30 days by default) and then deleted. Each row holds the message that asked for a reply, the recent channel messages that went to the model, any file, PDF or web-page text the bot read, and the reply. `/forget` deletes the rows tied to your user hash, so a message of yours that appears inside someone else's row stays until that row expires. By default the encryption key is stored next to the database, so the encryption protects the database file on its own, not someone who can read the whole server.

The bot copies its database once a day, on the same server, so a damaged file does not lose everyone's data. It keeps 7 copies. Each night those copies and the reply log are also sent, encrypted, to Cloudflare R2, which keeps them for 30 days, so the data survives losing the server. What you delete with `/forget` is gone from the database at once and from the copies on the server 7 days later. R2 deletes each copy 30 days after it was uploaded, and a copy can be uploaded up to 6 days late after an outage, so it is gone from R2 within 37 days. Only the person running the bot can read the R2 copies.

## Questions

Ask the person who runs the bot on your server, or open an issue at https://github.com/Mykal-Steele/ChronoChunk/issues.
