import discord
import os
import random
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Set up bot
intents = discord.Intents.default()
intents.message_content = True # so that bot can read msg

bot = commands.Bot(command_prefix="/", intents=intents)

active_games = {}

# For testing
@bot.event
async def on_ready():
    print(f'We have logged in as {bot.user}')

@bot.command(name="game")
async def start_guess(ctx, max_range: int): # ctx does stuff, i just dont know how it does, but it works
    

    if ctx.author.id in active_games:
        await ctx.send("You already have an active game! Finish it before starting a new one. Or use /end to end the current game") # send msg as bot to chat (ctx.send)
        return

    if max_range < 1:
        await ctx.send("Please provvide a positive number greater than 1.")
        return

    secret_number = random.randint(1, max_range) # get random
    active_games[ctx.author.id] = {
        "secret_number": secret_number,
        "attempts_left": 10 
    }
    await ctx.send(f"Gamee STarted! I'm thinking of a number between 1 and {max_range}. Start guessing with `/guess <your number>`. You have 10 attempts.")

@bot.command(name="end")
async def end_game(ctx):
    if(ctx.author.id in active_games):
        await ctx.send("Thank you for playign. your current game has been ended")
        del active_games[ctx.author.id] # delete cur usr from the dict
        
@bot.command(name="guess")
async def guess(ctx, inp: int):
    

    secret_number = active_games[ctx.author.id]["secret_number"]
    attempts_left = active_games[ctx.author.id]["attempts_left"]

    if inp == secret_number:
        await ctx.send(f" Your guess is corrrect!!! the correct answe is {secret_number}.")
        del active_games[ctx.author.id]  # end game after correct guess
    else:
        attempts_left -= 1
        active_games[ctx.author.id]["attempts_left"] = attempts_left
        if attempts_left > 0:
            await ctx.send(f"INcorrect guess! You have {attempts_left} Attemps left! try agin")
            if(secret_number > inp):
                await ctx.send(f"COrrect number is higher")
            else:
                await ctx.send(f"Correct number is lwoer")
        else:
            await ctx.send(f"GAME OVER! The correct number was {secret_number}. PALY ANOTHER GAMEEEE")
            del active_games[ctx.author.id]  # End game after running out of attempts

# code splitting (python only for now)
@bot.command(name="code")
async def code(ctx):
    if not ctx.message.attachments:
        await ctx.send("Please attach a `.txt` file. Only Python code is SUpported for noww.")
        return
    
    attachment = ctx.message.attachments[0]

    if not attachment.filename.endswith(".txt"):
        await ctx.send("Only `.txt` files are supported.")
        return

    try:
        file_content = await attachment.read() # read and save as file_content
        text = file_content.decode("utf-8") # change to readable text

        chunk_size = 1900  #  size thingy. 2000 max 
        chunks = []

        # this does the splitting
        while len(text) > chunk_size:
            split_point = text.rfind("\n", 0, chunk_size)  # find new line and split there. starts from 0
            if split_point == -1:  # if \n not found
                break  

            chunks.append(text[:split_point].strip())  # begining to the splitpoint
            text = text[split_point + 1:]  # +1 to skip the \n character (go the new line after that)

        if text.strip():  
            chunks.append(text.strip())
        # this print the splitted messages
        for chunk in chunks:
            await ctx.send(f"```py\n{chunk}\n```")


    except Exception as e:
        await ctx.send(f" EERROR PROCSINGG AAHH: {e}")  

    await ctx.send("HERE is your entiree code file split into messaesgess so that it can be easily read without having to \"copyy\" the `.txt` file. Discord converts your code to text if it is over line 150 or so..")

    await ctx.send("If you prefer you can download the original `.txt` file here :) :", file=await attachment.to_file())

# run bot
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