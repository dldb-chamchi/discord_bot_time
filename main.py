import os
import datetime as dt
import discord
from discord.ext import commands, tasks
from pathlib import Path
from dotenv import load_dotenv

# ===== 환경 설정 =====
env_path = Path(__file__).resolve().parent / ".env"
print(f"[DEBUG] loading env from {env_path}")
load_dotenv(dotenv_path=env_path, override=True)

TOKEN = os.getenv("DISCORD_TOKEN", "")
VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "0"))
REPORT_CHANNEL_ID = int(os.getenv("REPORT_CHANNEL_ID", "0"))
DATA_FILE = os.getenv("DATA_FILE", "voice_time.json")

if not TOKEN:
    raise SystemExit("DISCORD_TOKEN 환경변수를 설정하세요 (.env 사용 가능).")
if not VOICE_CHANNEL_ID or not REPORT_CHANNEL_ID:
    raise SystemExit("VOICE_CHANNEL_ID / REPORT_CHANNEL_ID 환경변수를 설정하세요.")

# ===== 유틸리티 =====
KST = dt.timezone(dt.timedelta(hours=9))

def now_kst() -> dt.datetime:
    return dt.datetime.now(tz=KST)

def iso(dtobj: dt.datetime) -> str:
    return dtobj.astimezone(KST).isoformat()

def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)

# ===== 상태 저장 =====
import json
from typing import Dict, Any

class StateStore:
    def __init__(self, data_file: str):
        self.data_file = data_file
        self.state: Dict[str, Dict[str, Any]] = {
            "totals": {},
            "sessions": {}
        }

    def load(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.state["totals"] = data.get("totals", {})
                    self.state["sessions"] = data.get("sessions", {})
            except Exception:
                pass

    def save(self):
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False)

    def add_session_time(self, user_id: int, until: dt.datetime | None = None):
        uid = str(user_id)
        start_iso = self.state["sessions"].get(uid)
        if not start_iso:
            return
        start = parse_iso(start_iso)
        end = until or now_kst()
        elapsed = int((end - start).total_seconds())
        if elapsed > 0:
            self.state["totals"][uid] = self.state["totals"].get(uid, 0) + elapsed

# ===== 디스코드 봇 설정 =====
intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
store = StateStore(DATA_FILE)

# 채널 상태 관리 변수
channel_active = False
last_alert_time = None
COOLDOWN_SECONDS = 10 * 60  # 10분

@bot.event
async def on_ready():
    store.load()
    daily_reporter.start()
    print(f"Logged in as {bot.user} (id={bot.user.id})")

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    global channel_active, last_alert_time

    target_id = VOICE_CHANNEL_ID
    uid = str(member.id)

    before_id = before.channel.id if before.channel else None
    after_id = after.channel.id if after.channel else None

    # 입장 처리
    if before_id != target_id and after_id == target_id:
        store.state["sessions"][uid] = iso(now_kst())
        store.save()

        channel = after.channel
        members = [m for m in channel.members if not m.bot]  # 봇 제외

        # 아무도 없다가 처음 들어온 경우 & 쿨다운 체크
        now = dt.datetime.now(tz=KST)
        cooldown_ok = (
            last_alert_time is None or
            (now - last_alert_time).total_seconds() > COOLDOWN_SECONDS
        )

        if not channel_active and members and cooldown_ok:
            channel_active = True
            last_alert_time = now

            mention_list = " ".join(m.mention for m in members)
            report_ch = bot.get_channel(REPORT_CHANNEL_ID) or await bot.fetch_channel(REPORT_CHANNEL_ID)
            await report_ch.send(f"{mention_list}\n음성 채널 **{channel.name}**에 멤버가 있습니다!")

        return

    # 퇴장 처리
    if before_id == target_id and after_id != target_id:
        store.add_session_time(member.id)
        store.state["sessions"].pop(uid, None)
        store.save()

        # 채널이 비었으면 상태 초기화
        if before.channel and len([m for m in before.channel.members if not m.bot]) == 0:
            channel_active = False

        return

# ===== 주간 리포트 =====
@tasks.loop(time=dt.time(hour=14, minute=0, tzinfo=dt.timezone.utc))
async def daily_reporter():
    now = now_kst()
    if now.weekday() != 6:
        return

    for uid in list(store.state["sessions"].keys()):
        store.add_session_time(int(uid), until=now)
        store.state["sessions"][uid] = iso(now)

    if not store.state["totals"]:
        content = "이번 주 대상 음성 채널 체류 기록이 없습니다."
    else:
        items = sorted(store.state["totals"].items(), key=lambda kv: kv[1], reverse=True)
        lines = ["이번 주 음성 채널 체류 시간 (일~토, 단위: 시간)"]
        for uid, sec in items:
            hours = sec / 3600.0
            lines.append(f"- <@{uid}>: {hours:.2f}h")
        content = "\n".join(lines)

    channel = bot.get_channel(REPORT_CHANNEL_ID) or await bot.fetch_channel(REPORT_CHANNEL_ID)
    try:
        await channel.send(content)
    finally:
        store.state["totals"] = {}
        store.save()

# ===== 명령어 =====
@bot.command()
@commands.has_permissions(administrator=True)
async def voicetime(ctx):
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
    bot.run(TOKEN)
