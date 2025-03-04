import discord
import os
import random
from discord.ext import commands
from dotenv import load_dotenv
from aiohttp import web

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Set up bot
intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent
bot = commands.Bot(command_prefix="/", intents=intents)

# Store active games
active_games = {}

# Minimal web server for Render health checks
async def health_check(request):
    return web.Response(text="OK")

def setup_web_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    return app

# Event: When the bot is ready
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')
    
    # Start web server for Render health checks
    app = setup_web_server()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
    await site.start()
    print("Web server started for Render health checks")

# Command: Start a new guessing game
@bot.command(name="game")
async def start_guess(ctx, max_range: int):
    if ctx.author.id in active_games:
        await ctx.send("You already have an active game! Finish it before starting a new one. Use `/end` to end the current game.")
        return

    if max_range < 1:
        await ctx.send("Please provide a positive number greater than 1.")
        return

    secret_number = random.randint(1, max_range)  # Generate random number
    active_games[ctx.author.id] = {
        "secret_number": secret_number,
        "attempts_left": 10
    }
    await ctx.send(f"Game Started! I'm thinking of a number between 1 and {max_range}. Start guessing with `/guess <your number>`. You have 10 attempts.")

# Command: End the current game
@bot.command(name="end")
async def end_game(ctx):
    if ctx.author.id in active_games:
        await ctx.send("Thank you for playing. Your current game has been ended.")
        del active_games[ctx.author.id]  # Remove user from active games
    else:
        await ctx.send("You don't have an active game to end.")

# Command: Make a guess
@bot.command(name="guess")
async def guess(ctx, inp: int):
    if ctx.author.id not in active_games:
        await ctx.send("You don't have an active game. Start one with `/game <max_range>`.")
        return

    secret_number = active_games[ctx.author.id]["secret_number"]
    attempts_left = active_games[ctx.author.id]["attempts_left"]

    if inp == secret_number:
        await ctx.send(f"Your guess is correct!!! The correct answer is {secret_number}.")
        del active_games[ctx.author.id]  # End game after correct guess
    else:
        attempts_left -= 1
        active_games[ctx.author.id]["attempts_left"] = attempts_left
        if attempts_left > 0:
            await ctx.send(f"Incorrect guess! You have {attempts_left} attempts left! Try again.")
            if secret_number > inp:
                await ctx.send(f"The correct number is higher.")
            else:
                await ctx.send(f"The correct number is lower.")
        else:
            await ctx.send(f"GAME OVER! The correct number was {secret_number}. PLAY ANOTHER GAME!")
            del active_games[ctx.author.id]  # End game after running out of attempts

# Command: Split code into chunks
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
        file_content = await attachment.read()  # Read and save as file_content
        text = file_content.decode("utf-8")  # Decode to readable text

        chunk_size = 1900  # Max Discord message size is 2000
        chunks = []

        # Split the text into chunks
        while len(text) > chunk_size:
            split_point = text.rfind("\n", 0, chunk_size)  # Find new line and split there
            if split_point == -1:  # If \n not found
                break  

            chunks.append(text[:split_point].strip())  # Add chunk
            text = text[split_point + 1:]  # Skip the \n character

        if text.strip():  
            chunks.append(text.strip())
        
        # Send the split messages
        for chunk in chunks:
            await ctx.send(f"```jsx\n{chunk}\n```")

    except Exception as e:
        await ctx.send(f"ERROR PROCESSING: {e}")  

    await ctx.send("Here is your entire code file split into messages for easy reading.")
    await ctx.send("If you prefer, you can download the original `.txt` file here:", file=await attachment.to_file())

# Run the bot
if __name__ == "__main__":
    bot.run(TOKEN)

# send data to ggl drive -> get link -> send to chat





# #Google drive api
# def authenticate_google_drive():
#     creds = None
#     # Try to load existing credentials
#     if os.path.exists('token.json'):
#         try:
#             creds = Credentials.from_authorized_user_file('token.json', SCOPES)
#         except Exception as e:
#             print(f"Error loading token.json: {e}")
#             creds = None

#     # Check if credentials are valid or can be refreshed
#     if creds:
#         try:
#             # Check if credentials are expired
#             if creds.expired:
#                 if creds.refresh_token:
#                     try:
#                         creds.refresh(Request())
#                         print("Successfully refreshed expired credentials")
#                     except Exception as e:
#                         print(f"Error refreshing token: {e}")
#                         creds = None
#                 else:
#                     print("Credentials expired and no refresh token available")
#                     creds = None
#             else:
#                 print("Using existing valid credentials")
#         except Exception as e:
#             print(f"Error checking credentials: {e}")
#             creds = None