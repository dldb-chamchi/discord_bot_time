# cogs/mention_shortcut.py
import discord
from discord.ext import commands

from config import MENTION_CHANNEL_ID

class MentionShortcutCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 1. 봇이 보낸 메시지는 무시
        if message.author.bot:
            return
            
        # 2. '!'로 시작하지 않으면 무시 (봇이 알아서 처리함)
        content = (message.content or "").strip()
        if not content.startswith("!"):
            return

        # 3. DM 메시지는 무시 (봇이 알아서 처리함)
        if not message.guild:
            return

        # 4. '!' 뒤에 내용이 없으면 무시
        raw = content[1:].strip()
        if not raw:
            return

        base_cmd = raw.split()[0].lower()

        # [핵심] 이미 존재하는 명령어(menu, voicetime 등)라면
        # 여기서 아무것도 하지 말고 함수를 종료해야 합니다.
        # 그래야 봇이 기본 기능으로 딱 한 번만 실행합니다.
        if base_cmd in {"menu", "voicetime"}:
            return

        # ---------------------------------------------------------
        # 아래는 명령어가 아닐 때만 작동하는 '멘션 단축키' 기능입니다.
        # ---------------------------------------------------------

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
            return
        elif len(exact_matches) > 1:
            await reply_candidates(exact_matches)
            return

        # 부분 일치 확인
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

        # [중요] 함수 끝에 있던 await self.bot.process_commands(message) 삭제됨
        # 이제 봇이 알아서 처리하므로 강제로 시키지 않습니다.


async def setup(bot: commands.Bot):
    await bot.add_cog(MentionShortcutCog(bot))