# cogs/voice_time.py
import datetime as dt
from typing import List

import discord
from discord.ext import commands, tasks

from config import VOICE_CHANNEL_ID, REPORT_CHANNEL_ID_ENTER, DATA_FILE
from time_utils import now_kst, iso
from state_store import StateStore

COOLDOWN_SECONDS = 10 * 60  # 10분

class VoiceTimeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = StateStore(DATA_FILE)
        self.store.load()

        self.channel_active = False
        self.last_alert_time: dt.datetime | None = None

        # 태스크 시작
        self.daily_reporter.start()

    def cog_unload(self):
        self.daily_reporter.cancel()

    # --------- 음성 상태 업데이트 ----------
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        target_id = VOICE_CHANNEL_ID
        uid = str(member.id)

        before_id = before.channel.id if before.channel else None
        after_id = after.channel.id if after.channel else None

        # 입장
        if before_id != target_id and after_id == target_id:
            self.store.state["sessions"][uid] = iso(now_kst())
            self.store.save()

            voice_channel = after.channel
            guild = member.guild
            if not voice_channel or not guild:
                return

            members_in_channel = [m for m in voice_channel.members if not m.bot]

            now = now_kst()
            cooldown_ok = (
                self.last_alert_time is None
                or (now - self.last_alert_time).total_seconds() > COOLDOWN_SECONDS
            )

            if not self.channel_active and members_in_channel and cooldown_ok:
                self.channel_active = True
                self.last_alert_time = now

                await discord.utils.sleep_until(discord.utils.utcnow() + dt.timedelta(seconds=1))

                members_not_in_channel = [
                    m for m in guild.members
                    if not m.bot and m not in voice_channel.members
                ]

                report_ch = self.bot.get_channel(REPORT_CHANNEL_ID_ENTER) \
                    or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ENTER)
                header = f'음성 채널 **{voice_channel.name}**에 멤버가 있습니다!'

                if members_not_in_channel:
                    await self._send_mentions_in_chunks(report_ch, members_not_in_channel, header_text=header)
                else:
                    await report_ch.send(header)
            return

        # 퇴장
        if before_id == target_id and after_id != target_id:
            self.store.add_session_time(member.id)
            self.store.state["sessions"].pop(uid, None)
            self.store.save()

            if before.channel and len([m for m in before.channel.members if not m.bot]) == 0:
                self.channel_active = False
            return

    async def _send_mentions_in_chunks(
        self,
        report_ch: discord.abc.Messageable,
        members_to_ping: List[discord.Member],
        header_text: str = "",
        chunk_size: int = 40,
    ):
        for i in range(0, len(members_to_ping), chunk_size):
            chunk = members_to_ping[i : i + chunk_size]
            mention_list = " ".join(m.mention for m in chunk)
            text = f"{mention_list}\n{header_text}" if header_text else mention_list
            await report_ch.send(text)

    # --------- 주간 리포트 (일요일 23:00 KST = 14:00 UTC) ----------
    @tasks.loop(time=dt.time(hour=14, minute=0, tzinfo=dt.timezone.utc))
    async def daily_reporter(self):
        now = now_kst()
        if now.weekday() != 6:
            return

        # 진행 중 세션 반영
        for uid in list(self.store.state["sessions"].keys()):
            self.store.add_session_time(int(uid), until=now)
            self.store.state["sessions"][uid] = iso(now)

        # 리포트 내용
        if not self.store.state["totals"]:
            content = "이번 주 대상 음성 채널 체류 기록이 없습니다."
        else:
            items = sorted(self.store.state["totals"].items(), key=lambda kv: kv[1], reverse=True)
            lines = ["이번 주 음성 채널 체류 시간 (일~토, 단위: 시간)"]
            for uid, sec in items:
                hours = sec / 3600.0
                lines.append(f"- <@{uid}>: {hours:.2f}h")
            content = "\n".join(lines)

        channel = self.bot.get_channel(REPORT_CHANNEL_ID_ENTER) \
            or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ENTER)
        try:
            await channel.send(content)
        finally:
            self.store.state["totals"] = {}
            self.store.save()

    # --------- 관리자용 voicetime 명령 ----------
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def voicetime(self, ctx: commands.Context):
        if not self.store.state["totals"]:
            await ctx.send("현재 누적 데이터가 없습니다.")
            return
        items = sorted(self.store.state["totals"].items(), key=lambda kv: kv[1], reverse=True)
        lines = []
        for uid, sec in items:
            hours = sec / 3600.0
            lines.append(f"<@{uid}>: {hours:.2f}h")
        await ctx.send("\n".join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceTimeCog(bot))
