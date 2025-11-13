# bot.py
import discord
from discord.ext import commands

# 필요하면 여기서 config를 import 해서 써도 됩니다.
# from config import DISCORD_TOKEN

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})")

    # 슬래시 명령 동기화
    try:
        synced = await bot.tree.sync()
        print(f"[DEBUG] slash commands synced: {len(synced)}")
    except Exception as e:
        print(f"[DEBUG] slash sync error: {e}")
