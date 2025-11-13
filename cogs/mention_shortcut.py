# cogs/mention_shortcut.py
import discord
from discord.ext import commands

from config import MENTION_CHANNEL_ID

class MentionShortcutCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        content = (message.content or "").strip()
        if not content.startswith("!"):
            await self.bot.process_commands(message)
            return

        if not message.guild:  # DM
            await self.bot.process_commands(message)
            return

        raw = content[1:].strip()
        if not raw:
            await self.bot.process_commands(message)
            return

        base_cmd = raw.split()[0].lower()
        if base_cmd in {"menu", "voicetime"}:
            await self.bot.process_commands(message)
            return

        def normalize(s: str) -> str:
            return (s or "").replace(" ", "").lower()

        if " " in raw:
            target = raw.split(" ", 1)[0]
        else:
            target = raw
        target_n = normalize(target)

        def is_match(m: discord.Member) -> bool:
            display = getattr(m, "display_name", "")
            uname = getattr(m, "name", "")
            gname = getattr(m, "global_name", None) or ""
            return (
                normalize(display) == target_n
                or normalize(uname) == target_n
                or normalize(gname) == target_n
            )

        members = [m for m in message.guild.members if not m.bot]
        exact_matches = [m for m in members if is_match(m)]

        if MENTION_CHANNEL_ID:
            target_ch = self.bot.get_channel(MENTION_CHANNEL_ID) \
                or await self.bot.fetch_channel(MENTION_CHANNEL_ID)
        else:
            target_ch = message.channel

        async def reply_candidates(cands: list[discord.Member]):
            names = ", ".join(m.display_name for m in cands[:5])
            more = " 등" if len(cands) > 5 else ""
            await target_ch.send(f"여러 명이 일치합니다: {names}{more}")

        def compose_with_extra(mention: str) -> str:
            return f"{mention}님 디스코드 확인하세요!"

        if len(exact_matches) == 1:
            await target_ch.send(compose_with_extra(exact_matches[0].mention))
            await self.bot.process_commands(message)
            return
        elif len(exact_matches) > 1:
            await reply_candidates(exact_matches)
            await self.bot.process_commands(message)
            return

        def is_partial(m: discord.Member) -> bool:
            display = getattr(m, "display_name", "")
            uname = getattr(m, "name", "")
            gname = getattr(m, "global_name", None) or ""
            return (
                target_n in normalize(display)
                or target_n in normalize(uname)
                or target_n in normalize(gname)
            )

        partials = [m for m in members if is_partial(m)]
        if len(partials) == 1:
            await target_ch.send(compose_with_extra(partials[0].mention))
        elif len(partials) > 1:
            await reply_candidates(partials)
        else:
            await target_ch.send("해당 이름을 가진 멤버를 찾지 못했습니다.")

        await self.bot.process_commands(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(MentionShortcutCog(bot))
