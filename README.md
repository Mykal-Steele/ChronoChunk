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
- `/tldr <count>` - Sum up the last messages in the channel (default 50, max 200)

## Reading messages and files

Reply to any message and start your reply with `/`, for example `/read this` or `/summarize`. The bot reads the message you replied to and answers about it. This works for:

- text messages and link previews
- images (png, jpg, webp)
- PDFs with selectable text
- markdown, text and code files

You can also attach a file or image to your own `/` message. Pinging the bot works the same as starting with `/`.

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
- Tests are in `tests/` directory
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
