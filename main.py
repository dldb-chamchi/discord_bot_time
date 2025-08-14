import os
import datetime as dt
import discord
from discord.ext import commands, tasks

from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent / ".env"
print(f"[DEBUG] loading env from {env_path}")  # 경로 확인용
load_dotenv(dotenv_path=env_path, override=True)

import os
TOKEN = os.getenv("DISCORD_TOKEN")
print(f"[DEBUG] TOKEN loaded? {'yes' if TOKEN else 'no'}")
# 로컬 편의: .env 로드(서버도 사용 가능)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from bot.storage import StateStore, now_kst, iso

TOKEN = os.getenv("DISCORD_TOKEN", "")
VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "0"))
REPORT_CHANNEL_ID = int(os.getenv("REPORT_CHANNEL_ID", "0"))
DATA_FILE = os.getenv("DATA_FILE", "voice_time.json")

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
store = StateStore(DATA_FILE)

@bot.event
async def on_ready():
    store.load()
    daily_reporter.start()
    print(f"Logged in as {bot.user} (id={bot.user.id})")

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    target_id = VOICE_CHANNEL_ID
    uid = str(member.id)

    before_id = before.channel.id if before.channel else None
    after_id = after.channel.id if after.channel else None

    # 입장
    if before_id != target_id and after_id == target_id:
        store.state["sessions"][uid] = iso(now_kst())
        store.save()
        return

    # 퇴장
    if before_id == target_id and after_id != target_id:
        store.add_session_time(member.id)
        store.state["sessions"].pop(uid, None)
        store.save()
        return

# 매일 23:00 KST(= UTC 14:00), 일요일에만 전송
@tasks.loop(time=dt.time(hour=14, minute=0, tzinfo=dt.timezone.utc))
async def daily_reporter():
    now = now_kst()
    if now.weekday() != 6:
        return

    # 진행중 세션 누적
    for uid in list(store.state["sessions"].keys()):
        store.add_session_time(int(uid), until=now)
        store.state["sessions"][uid] = iso(now)

    # 리포트 생성
    if not store.state["totals"]:
        content = "이번 주 대상 음성 채널 체류 기록이 없습니다."
    else:
        items = sorted(store.state["totals"].items(), key=lambda kv: kv[1], reverse=True)
        lines = ["이번 주 음성 채널 체류 시간 (일~토, 단위: 시간)"]
        for uid, sec in items:
            hours = sec / 3600.0
            lines.append(f"- <@{uid}>: {hours:.2f}h")
        content = "\n".join(lines)

    # 전송
    channel = bot.get_channel(REPORT_CHANNEL_ID) or await bot.fetch_channel(REPORT_CHANNEL_ID)
    try:
        await channel.send(content)
    finally:
        # 주간 초기화
        store.state["totals"] = {}
        store.save()

@bot.command()
@commands.has_permissions(administrator=True)
async def voicetime(ctx):
    """현재까지의 누적 시간 출력"""
    if not store.state["totals"]:
        await ctx.send("현재 누적 데이터가 없습니다.")
        return
    items = sorted(store.state["totals"].items(), key=lambda kv: kv[1], reverse=True)
    lines = []
    for uid, sec in items:
        hours = sec / 3600.0
        lines.append(f"<@{uid}>: {hours:.2f}h")
    await ctx.send("\n".join(lines))

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN 환경변수를 설정하세요 (.env 사용 가능).")
    if not VOICE_CHANNEL_ID or not REPORT_CHANNEL_ID:
        raise SystemExit("VOICE_CHANNEL_ID / REPORT_CHANNEL_ID 환경변수를 설정하세요.")
    bot.run(TOKEN)
