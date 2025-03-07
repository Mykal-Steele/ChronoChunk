import discord
import os
import random
from discord.ext import commands
from dotenv import load_dotenv
from aiohttp import web

# gotta load that token from .env file
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# need this or discord won't let us read messages
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# dict to track all active games per user
active_games = {}

# render keeps pinging this or our bot dies lol
async def health_check(request):
    return web.Response(text="OK")

def setup_web_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    return app

has_started = False  # prevents on_ready from running twice

@bot.event
async def on_ready():
    global has_started
    if has_started:
        return  # already started, don't run again
    has_started = True  # mark it so we don't run twice

    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')

    # setup health check stuff for render hosting
    app = setup_web_server()
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 10000))  # default to 10k if not set
    site = web.TCPSite(runner, host='0.0.0.0', port=port)
    await site.start()
    print("Web server started for Render health checks")


@bot.command(name="game")
async def start_guess(ctx, max_range: int):
    # check if they're already in a game
    if ctx.author.id in active_games:
        await ctx.send("You already have an active game! Finish it before starting a new one. Use `/end` to end the current game.")
        return

    # make sure input isn't stupid
    if max_range < 1:
        await ctx.send("Please provide a positive number greater than 1.")
        return

    # generate random number and set up game
    secret = random.randint(1, max_range)
    active_games[ctx.author.id] = {
        "secret_number": secret,
        "attempts_left": 10  # might make difficulty levels later
    }
    await ctx.send(f"Game Started! I'm thinking of a number between 1 and {max_range}. Start guessing with `/guess <your number>`. You have 10 attempts.")

# let people quit early if they want
@bot.command(name="end")
async def end_game(ctx):
    if ctx.author.id in active_games:
        await ctx.send("Thank you for playing. Your current game has been ended.")
        del active_games[ctx.author.id]
    else:
        await ctx.send("You don't have an active game to end.")

@bot.command(name="guess")
async def guess(ctx, inp: int):
    # can't guess if not playing
    if ctx.author.id not in active_games:
        await ctx.send("You don't have an active game. Start one with `/game <max_range>`.") 
        return

    # get their game info
    game_data = active_games[ctx.author.id]
    secret_number = game_data["secret_number"]
    attempts_left = game_data["attempts_left"]

    # they got it!
    if inp == secret_number:
        await ctx.send(f"Your guess is correct!!! The correct answer is {secret_number}.")
        del active_games[ctx.author.id]
    else:
        # wrong guess, decrease attempts
        attempts_left -= 1
        active_games[ctx.author.id]["attempts_left"] = attempts_left

        if attempts_left > 0:
            # give a hint so they don't get stuck
            response = f"Incorrect guess! You have {attempts_left} attempts left! Try again."
            if secret_number > inp:
                response += " The correct number is higher."
            else:
                response += " The correct number is lower."
            await ctx.send(response)
        else:
            # ran out of tries oof
            await ctx.send(f"GAME OVER! The correct number was {secret_number}. PLAY ANOTHER GAME!")
            del active_games[ctx.author.id]

# splits code so it fits in discord messages
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
        # grab the file content
        content = await attachment.read()
        text = content.decode("utf-8")

        # discord has char limit so gotta split
        MAX_MSG_SIZE = 1900
        chunks = []

        # break at newlines to keep code readable
        while len(text) > MAX_MSG_SIZE:
            split_idx = text.rfind("\n", 0, MAX_MSG_SIZE)
            if split_idx == -1:  # no good place to split found
                break  

            chunks.append(text[:split_idx].strip())
            text = text[split_idx + 1:]

        # add whatever's left
        if text.strip():  
            chunks.append(text.strip())
        
        # send each part with code formatting
        for chunk in chunks:
            await ctx.send(f"```jsx\n{chunk}\n```")

    except Exception as e:
        await ctx.send(f"ERROR PROCESSING: {e}")  

    await ctx.send("Here is your entire code file split into messages for easy reading.")
    await ctx.send("If you prefer, you can download the original `.txt` file here:", file=await attachment.to_file())

# start the bot
if __name__ == "__main__":
    bot.run(TOKEN)