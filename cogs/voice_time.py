# cogs/voice_time.py
import datetime as dt
import asyncio  # [추가] 딜레이 기능을 위해 필요
from typing import List

import discord
from discord.ext import commands, tasks

from config import VOICE_CHANNEL_ID, REPORT_CHANNEL_ID_ENTER, DATA_FILE, REPORT_CHANNEL_ID_ALARM
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

        self.daily_reporter.start()

    def cog_unload(self):
        self.daily_reporter.cancel()

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

        # 1. 입장 (Enter)
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

        # 2. 퇴장 (Leave)
        if before_id == target_id and after_id != target_id:
            # 세션 기록 저장
            self.store.add_session_time(member.id)
            self.store.state["sessions"].pop(uid, None)
            self.store.save()

            if before.channel and len([m for m in before.channel.members if not m.bot]) == 0:
                self.channel_active = False

            # [핵심] 30초 딜레이 후 알림 발송 로직
            if hasattr(self.bot, 'active_schedules') and member.id in self.bot.active_schedules:
                # 30초 대기
                await asyncio.sleep(30)

                # 30초 후 현재 상태 다시 확인 (유저가 다시 들어왔는지 체크)
                # member 객체는 옛날 정보일 수 있으므로, 길드에서 최신 멤버 정보를 다시 가져옴
                current_member = member.guild.get_member(member.id)
                
                # 유저가 서버를 나갔거나(None), 
                # 음성 채널에 없거나, 
                # 음성 채널에 있어도 우리 타겟 채널이 아니라면 -> 알림 발송 대상
                is_back_in_channel = False
                if current_member and current_member.voice and current_member.voice.channel:
                    if current_member.voice.channel.id == target_id:
                        is_back_in_channel = True
                
                # 이미 돌아왔다면 알림 취소
                if is_back_in_channel:
                    return

                # 여전히 나가 있다면 일정 체크 후 알림
                scheduled_end = self.bot.active_schedules[member.id]
                now = now_kst()
                
                if now < scheduled_end:
                    time_diff = scheduled_end - now
                    minutes_left = int(time_diff.total_seconds() / 60)
                    
                    if minutes_left > 1:
                        alarm_ch = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) \
                                   or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
                        
                        if alarm_ch:
                            msg = (
                                f"🚨 **{member.mention} 님, 어디 가시나요?**\n"
                                f"아직 일정이 **{minutes_left}분** 남았습니다!\n"
                                f"목표 시간: {scheduled_end.strftime('%H:%M')}"
                            )
                            await alarm_ch.send(msg)
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

    @tasks.loop(time=dt.time(hour=14, minute=0, tzinfo=dt.timezone.utc))
    async def daily_reporter(self):
        now = now_kst()
        if now.weekday() != 6:
            return

        for uid in list(self.store.state["sessions"].keys()):
            self.store.add_session_time(int(uid), until=now)
            self.store.state["sessions"][uid] = iso(now)

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
    