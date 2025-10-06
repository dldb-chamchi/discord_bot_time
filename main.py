import os
import json
import asyncio
import datetime as dt
from pathlib import Path
from typing import Dict, Any

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from menu_recommender import MenuRecommender

# =========================
# 환경 설정
# =========================
env_path = Path(__file__).resolve().parent / ".env"
print(f"[DEBUG] loading env from {env_path}")
load_dotenv(dotenv_path=env_path, override=True)

TOKEN = os.getenv("DISCORD_TOKEN", "")
VOICE_CHANNEL_ID = int(os.getenv("VOICE_CHANNEL_ID", "0"))
REPORT_CHANNEL_ID_ENTER = int(os.getenv("REPORT_CHANNEL_ID_ENTER", "0"))
REPORT_CHANNEL_ID_TOEIC = int(os.getenv("REPORT_CHANNEL_ID_TOEIC", "0"))
DATA_FILE = os.getenv("DATA_FILE", "voice_time.json")
MENTION_CHANNEL_ID = int(os.getenv("MENTION_CHANNEL_ID", "0"))

if not TOKEN:
    raise SystemExit("DISCORD_TOKEN 환경변수를 설정하세요 (.env 사용 가능).")
if not VOICE_CHANNEL_ID or not REPORT_CHANNEL_ID_ENTER or not REPORT_CHANNEL_ID_TOEIC:
    raise SystemExit("VOICE_CHANNEL_ID / REPORT_CHANNEL_ID 환경변수를 설정하세요.")

# =========================
# 유틸리티
# =========================
KST = dt.timezone(dt.timedelta(hours=9))

def now_kst() -> dt.datetime:
    return dt.datetime.now(tz=KST)

def iso(dtobj: dt.datetime) -> str:
    return dtobj.astimezone(KST).isoformat()

def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)

# =========================
# 상태 저장소
# =========================
class StateStore:
    def __init__(self, data_file: str):
        self.data_file = data_file
        self.state: Dict[str, Dict[str, Any]] = {
            "totals": {},   # user_id(str) -> 누적 초(int)
            "sessions": {}  # user_id(str) -> 시작시각(ISO str)
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

# =========================
# 디스코드 봇 설정
# =========================
intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = True        # 서버 멤버 전체 접근에 필수
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
store = StateStore(DATA_FILE)
recommender = MenuRecommender()

# 채널 상태 관리
channel_active = False          # 대상 음성 채널에 현재 사람이 있는지
last_alert_time: dt.datetime | None = None
COOLDOWN_SECONDS = 10 * 60      # 10분

# =========================
# 헬퍼: 멘션 분할 전송(2000자 제한 대비)
# =========================
async def send_mentions_in_chunks(report_ch: discord.abc.Messageable, members_to_ping: list[discord.Member], header_text: str = "", chunk_size: int = 40):
    for i in range(0, len(members_to_ping), chunk_size):
        chunk = members_to_ping[i:i+chunk_size]
        mention_list = " ".join(m.mention for m in chunk)
        text = f"{mention_list}\n{header_text}" if header_text else mention_list
        await report_ch.send(text)

# =========================
# 이벤트: 준비 완료
# =========================
@bot.event
async def on_ready():
    store.load()

    # 선택) 대규모 서버에서 멤버 캐시 프리페치
    # 필요 없으면 아래 블록을 주석 처리해도 됨
    for g in bot.guilds:
        try:
            # 최신 discord.py에서는 async iterator 사용
            async for _ in g.fetch_members(limit=None):
                pass
        except Exception:
            pass

    daily_reporter.start()
    ##### 추가된 부분 시작 #####
    scheduled_message.start() # 새로 추가한 정기 메시지 태스크를 시작합니다.
    ##### 추가된 부분 끝 #####
    
    print(f"Logged in as {bot.user} (id={bot.user.id})")

    # 슬래시 명령 동기화
    try:
        synced = await bot.tree.sync()
        print(f"[DEBUG] slash commands synced: {len(synced)}")
    except Exception as e:
        print(f"[DEBUG] slash sync error: {e}")

# =========================
# 이벤트: 일반 메시지 처리 (닉네임 멘션 단축키)
# 예) "!홍길동" 입력 시 서버의 해당 멤버를 멘션
# =========================
@bot.event
async def on_message(message: discord.Message):
    # 봇 메시지/DM은 무시하고, 기존 명령어는 그대로 처리
    if message.author.bot:
        return
    content = (message.content or "").strip()
    if not content.startswith("!"):
        await bot.process_commands(message)
        return

    # DM 채널이면 통과
    if not message.guild:
        await bot.process_commands(message)
        return

    # 명령어 이름 추출 (형식: !이름 [추가메시지])
    raw = content[1:].strip()
    if not raw:
        await bot.process_commands(message)
        return

    # 기존 접두사 명령어는 패스
    base_cmd = raw.split()[0].lower()
    if base_cmd in {"menu", "voicetime"}:
        await bot.process_commands(message)
        return

    # 닉네임/표시명/유저명 매칭으로 멘션 시도
    def normalize(s: str) -> str:
        return (s or "").replace(" ", "").lower()

    # 이름만 사용 (고정 메시지 전송)
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
            normalize(display) == target_n or
            normalize(uname) == target_n or
            normalize(gname) == target_n
        )

    members = [m for m in message.guild.members if not m.bot]
    exact_matches = [m for m in members if is_match(m)]

    # 멘션을 보낼 대상 텍스트 채널 결정
    if MENTION_CHANNEL_ID:
        target_ch = bot.get_channel(MENTION_CHANNEL_ID) or await bot.fetch_channel(MENTION_CHANNEL_ID)
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
        await bot.process_commands(message)
        return
    elif len(exact_matches) > 1:
        # 동일 표기 다수면 후보 안내
        await reply_candidates(exact_matches)
        await bot.process_commands(message)
        return

    # 부분 일치로 보조 탐색
    def is_partial(m: discord.Member) -> bool:
        display = getattr(m, "display_name", "")
        uname = getattr(m, "name", "")
        gname = getattr(m, "global_name", None) or ""
        return (
            target_n in normalize(display) or
            target_n in normalize(uname) or
            target_n in normalize(gname)
        )

    partials = [m for m in members if is_partial(m)]
    if len(partials) == 1:
        await target_ch.send(compose_with_extra(partials[0].mention))
    elif len(partials) > 1:
        await reply_candidates(partials)
    else:
        await target_ch.send("해당 이름을 가진 멤버를 찾지 못했습니다.")

    # 다른 명령어 계속 처리
    await bot.process_commands(message)

# =========================
# 이벤트: 음성 상태 업데이트
# =========================
@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    global channel_active, last_alert_time

    target_id = VOICE_CHANNEL_ID
    uid = str(member.id)

    before_id = before.channel.id if before.channel else None
    after_id = after.channel.id if after.channel else None

    # ---------- 입장 처리 ----------
    if before_id != target_id and after_id == target_id:
        store.state["sessions"][uid] = iso(now_kst())
        store.save()

        voice_channel = after.channel
        guild = member.guild
        if not voice_channel or not guild:
            return

        # 현재 채널의 (봇 제외) 멤버
        members_in_channel = [m for m in voice_channel.members if not m.bot]

        now = now_kst()
        cooldown_ok = (
            last_alert_time is None or
            (now - last_alert_time).total_seconds() > COOLDOWN_SECONDS
        )

        # 아무도 없다가 처음 들어온 경우 + 쿨다운 통과
        if not channel_active and members_in_channel and cooldown_ok:
            channel_active = True
            last_alert_time = now

            # 동시 입장 보정
            await asyncio.sleep(1)

            # 길드 전체 멤버 중에서 "해당 음성 채널에 없는" 멤버(봇 제외)
            members_not_in_channel = [
                m for m in guild.members
                if not m.bot and m not in voice_channel.members
            ]

            report_ch = bot.get_channel(REPORT_CHANNEL_ID_ENTER) or await bot.fetch_channel(REPORT_CHANNEL_ID_ENTER)
            header = f'음성 채널 **{voice_channel.name}**에 멤버가 있습니다!'

            if members_not_in_channel:
                await send_mentions_in_chunks(report_ch, members_not_in_channel, header_text=header, chunk_size=40)
            else:
                # 멘션할 대상이 없더라도 헤더는 남김
                await report_ch.send(header)

        return

    # ---------- 퇴장 처리 ----------
    if before_id == target_id and after_id != target_id:
        store.add_session_time(member.id)
        store.state["sessions"].pop(uid, None)
        store.save()

        # 채널이 비었으면 상태 초기화
        if before.channel and len([m for m in before.channel.members if not m.bot]) == 0:
            channel_active = False

        return

# =========================
# 주간 리포트(일요일 23:00 KST = 14:00 UTC)
# =========================
@tasks.loop(time=dt.time(hour=14, minute=0, tzinfo=dt.timezone.utc))
async def daily_reporter():
    now = now_kst()
    if now.weekday() != 6:
        return

    # 진행 중 세션 반영
    for uid in list(store.state["sessions"].keys()):
        store.add_session_time(int(uid), until=now)
        store.state["sessions"][uid] = iso(now)

    # 리포트 본문
    if not store.state["totals"]:
        content = "이번 주 대상 음성 채널 체류 기록이 없습니다."
    else:
        items = sorted(store.state["totals"].items(), key=lambda kv: kv[1], reverse=True)
        lines = ["이번 주 음성 채널 체류 시간 (일~토, 단위: 시간)"]
        for uid, sec in items:
            hours = sec / 3600.0
            lines.append(f"- <@{uid}>: {hours:.2f}h")
        content = "\n".join(lines)

    # 전송 및 초기화
    channel = bot.get_channel(REPORT_CHANNEL_ID_ENTER) or await bot.fetch_channel(REPORT_CHANNEL_ID_ENTER)
    try:
        await channel.send(content)
    finally:
        store.state["totals"] = {}
        store.save()

##### 추가된 부분 시작 #####
# =========================
# 정기 메시지 (월 수 토 22:00 KST = 13:00 UTC)
# =========================
@tasks.loop(time=dt.time(hour=13, minute=0, tzinfo=dt.timezone.utc))
async def scheduled_message():
    now = now_kst()
    # now.weekday()는 월요일=0, 화요일=1, ..., 토요일=5, 일요일=6
    if now.weekday() in [0, 2, 5]: # 월, 수, 토
        channel = bot.get_channel(REPORT_CHANNEL_ID_TOEIC) or await bot.fetch_channel(REPORT_CHANNEL_ID_TOEIC)
        message = "🔥 토익 인증~ 12시 전까지 노션에다가 인증 올리기!🔥"
        await channel.send(message)
##### 추가된 부분 끝 #####

# =========================
# 명령어: 누적 시간 조회
# =========================
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

@bot.tree.command(name="menu", description="무작위로 메뉴를 추천합니다.")
async def menu(interaction: discord.Interaction):
    recommender.reload()
    gid = interaction.guild_id
    uid = interaction.user.id if interaction.user else None
    picked = recommender.recommend(guild_id=gid, user_id=uid)
    if not picked:
        await interaction.response.send_message("추천할 메뉴가 없습니다.", ephemeral=True)
        return
    await interaction.response.send_message(f"오늘은 **{picked['name']}** 어떠세요?")

@bot.command(name="menu")
async def menu_prefix(ctx: commands.Context):
    recommender.reload()
    gid = ctx.guild.id if ctx.guild else None
    uid = ctx.author.id if ctx.author else None
    picked = recommender.recommend(guild_id=gid, user_id=uid)
    if not picked:
        await ctx.send("추천할 메뉴가 없습니다.")
        return
    await ctx.send(f"오늘은 **{picked['name']}** 어떠세요?")

# =========================
# 실행
# =========================
if __name__ == "__main__":
    bot.run(TOKEN)