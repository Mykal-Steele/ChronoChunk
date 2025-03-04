import discord
import os
import random
from discord.ext import commands
from dotenv import load_dotenv
from aiohttp import web

# grab token from .env
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# need message content for commands to work
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# keep track of who's playing what game
active_games = {}

# health check endpoint for render - they ping this to make sure bot is alive
async def health_check(request):
    return web.Response(text="OK")

def setup_web_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    return app

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    
    # need this for render hosting or it'll shut down after 5min
    app = setup_web_server()
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 10000))  # fallback to 10k if no port specified
    site = web.TCPSite(runner, host='0.0.0.0', port=port)
    await site.start()
    print("Web server started for Render health checks")

@bot.command(name="game")
async def start_guess(ctx, max_range: int):
    # check if they're already playing something
    if ctx.author.id in active_games:
        await ctx.send("You already have an active game! Finish it before starting a new one. Use `/end` to end the current game.")
        return

    # sanity check lol
    if max_range < 1:
        await ctx.send("Please provide a positive number greater than 1.")
        return

    # pick a number and save game state
    secret = random.randint(1, max_range)
    active_games[ctx.author.id] = {
        "secret_number": secret,
        "attempts_left": 10  # might make this configurable later
    }
    await ctx.send(f"Game Started! I'm thinking of a number between 1 and {max_range}. Start guessing with `/guess <your number>`. You have 10 attempts.")

# let people bail if they want
@bot.command(name="end")
async def end_game(ctx):
    if ctx.author.id in active_games:
        await ctx.send("Thank you for playing. Your current game has been ended.")
        del active_games[ctx.author.id]
    else:
        await ctx.send("You don't have an active game to end.")

@bot.command(name="guess")
async def guess(ctx, inp: int):
    # make sure they started a game first
    if ctx.author.id not in active_games:
        await ctx.send("You don't have an active game. Start one with `/game <max_range>`.") 
        return

    # grab their game data
    game_data = active_games[ctx.author.id]
    secret_number = game_data["secret_number"]
    attempts_left = game_data["attempts_left"]

    # winner!
    if inp == secret_number:
        await ctx.send(f"Your guess is correct!!! The correct answer is {secret_number}.")
        del active_games[ctx.author.id]
    else:
        # wrong answer, count down attempts
        attempts_left -= 1
        active_games[ctx.author.id]["attempts_left"] = attempts_left

        if attempts_left > 0:
            # give them a hint to keep it moving
            response = f"Incorrect guess! You have {attempts_left} attempts left! Try again."
            if secret_number > inp:
                response += " The correct number is higher."
            else:
                response += " The correct number is lower."
            await ctx.send(response)
        else:
            # game over :(
            await ctx.send(f"GAME OVER! The correct number was {secret_number}. PLAY ANOTHER GAME!")
            del active_games[ctx.author.id]

# code splitter - helps break up large files to fit in discord messages
# TODO: add support for other file types besides .jsx
@bot.command(name="code")
async def code(ctx):
    if not ctx.message.attachments:
        await ctx.send("Please attach a `.txt` file. Only React (.jsx) code is supported for now.")
        return
    
    attachment = ctx.message.attachments[0]

    if not attachment.filename.endswith(".txt"):
        await ctx.send("Only `.txt` files are supported.")
        return

    try:
        # read the file they attached
        content = await attachment.read()
        text = content.decode("utf-8")

        # discord has a message size limit around 2000 chars
        # playing it safe with 1900 to leave room for markdown
        MAX_MSG_SIZE = 1900
        chunks = []

        # split text into chunks that fit in discord messages
        # trying to split at line breaks to keep code readable
        while len(text) > MAX_MSG_SIZE:
            split_idx = text.rfind("\n", 0, MAX_MSG_SIZE)
            if split_idx == -1:  # couldn't find a good split point
                break  

            chunks.append(text[:split_idx].strip())
            text = text[split_idx + 1:]

        # don't forget the last piece
        if text.strip():  
            chunks.append(text.strip())
        
        # send each chunk with proper formatting
        for chunk in chunks:
            await ctx.send(f"```jsx\n{chunk}\n```")

    except Exception as e:
        await ctx.send(f"ERROR PROCESSING: {e}")  

    await ctx.send("Here is your entire code file split into messages for easy reading.")
    await ctx.send("If you prefer, you can download the original `.txt` file here:", file=await attachment.to_file())

# fire it up! letssgoo
if __name__ == "__main__":
    bot.run(TOKEN)