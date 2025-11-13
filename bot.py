# bot.py
import asyncio
import discord
from discord.ext import commands

from config import DISCORD_TOKEN

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

async def main():
    async with bot:
        # Cog 로드
        await bot.load_extension("cogs.voice_time")
        await bot.load_extension("cogs.mention_shortcut")
        await bot.load_extension("cogs.menu_commands")
        await bot.load_extension("cogs.notion_watcher")

        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
