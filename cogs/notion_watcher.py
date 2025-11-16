# cogs/notion_watcher.py

import asyncio
import aiohttp  # 라이브러리 직접 요청용
from typing import Dict, Set, List, Optional, Any

from discord.ext import commands, tasks

from config import (
    NOTION_TOKEN,
    NOTION_DATABASE_FEATURE_ID,
    NOTION_DATABASE_BOARD_ID,
    NOTION_DATABASE_SCHEDULE_ID,
    REPORT_CHANNEL_ID_FEATURE,
    REPORT_CHANNEL_ID_ALARM,
)

# ===== 헬퍼 함수들 =====

def _is_completed_status(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    return ("완료" in n) or (n in {"done", "completed", "complete"})

def _any_completed(status_names: List[str]) -> bool:
    return any(_is_completed_status(n) for n in status_names)

def _trim_to_minute(iso_str: str) -> str:
    if not iso_str:
        return ""
    if "T" in iso_str:
        date_part, time_part = iso_str.split("T", 1)
        hhmm = time_part[:5]
        return f"{date_part} {hhmm}"
    return iso_str

def _clean_env(val: Optional[str]) -> str:
    """환경변수 공백 제거"""
    return str(val).strip() if val else ""

class NotionWatcherCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_notion_row_ids: Set[str] = set()
        self.last_feature_status_by_id: Dict[str, str] = {}
        self.last_board_row_ids: Set[str] = set()
        self.last_schedule_row_ids: Set[str] = set()

    async def cog_load(self) -> None:
        if NOTION_TOKEN and NOTION_DATABASE_FEATURE_ID:
            self.notion_update_poller.start()
            print("[NOTION] notion_update_poller started")
        else:
            print("[NOTION] 설정 부족으로 폴링을 시작하지 않습니다.")

    def cog_unload(self) -> None:
        if self.notion_update_poller.is_running():
            self.notion_update_poller.cancel()
            print("[NOTION] notion_update_poller stopped")

    # [핵심] 라이브러리 대신 직접 요청을 보내는 헬퍼 함수
    async def _fetch_notion_db(self, session: aiohttp.ClientSession, db_id: str) -> List[Dict[str, Any]]:
        clean_db_id = _clean_env(db_id)
        if not clean_db_id:
            return []
            
        url = f"https://api.notion.com/v1/databases/{clean_db_id}/query"
        headers = {
            "Authorization": f"Bearer {_clean_env(NOTION_TOKEN)}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        payload = {
            "page_size": 50,
            "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}]
        }

        try:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"[NOTION] Error {resp.status} requesting {clean_db_id}: {error_text}")
                    return []
                data = await resp.json()
                return data.get("results", [])
        except Exception as e:
            print(f"[NOTION] Request Exception: {e}")
            return []

    # =========================
    # 노션 업데이트 폴링
    # =========================
    @tasks.loop(seconds=60)
    async def notion_update_poller(self):
        if not NOTION_TOKEN:
            return

        print("[NOTION] poller tick")

        try:
            async with aiohttp.ClientSession() as session:
                # ---------------------------------------------------------
                # 1. FEATURE DB 조회
                # ---------------------------------------------------------
                if NOTION_DATABASE_FEATURE_ID:
                    rows = await self._fetch_notion_db(session, NOTION_DATABASE_FEATURE_ID)
                    new_row_ids = {row["id"] for row in rows}

                    # 신규 행 감지
                    only_new = new_row_ids - self.last_notion_row_ids
                    if only_new:
                        print(f"[NOTION] New rows detected ({len(only_new)}). Waiting 20s...")
                        await asyncio.sleep(20)
                        # 다시 조회
                        rows = await self._fetch_notion_db(session, NOTION_DATABASE_FEATURE_ID)

                    # 1-1) 신규 행 처리
                    if only_new:
                        new_request_lines = []
                        new_completed_lines = []

                        for row in rows:
                            if row["id"] not in only_new:
                                continue
                            
                            rid = row["id"]
                            props = row.get("properties", {})
                            
                            # 상태 추출
                            status_names = []
                            status_prop = props.get("상태")
                            # (간소화된 로직)
                            if not status_prop:
                                for v in props.values():
                                    if isinstance(v, dict) and v.get("type") in ("status", "select", "multi_select"):
                                        status_prop = v
                                        break
                            
                            if status_prop:
                                ptype = status_prop.get("type")
                                if ptype == "status":
                                    name = status_prop.get("status", {}).get("name")
                                    if name: status_names.append(name)
                                elif ptype == "select":
                                    name = status_prop.get("select", {}).get("name")
                                    if name: status_names.append(name)
                                elif ptype == "multi_select":
                                    for opt in status_prop.get("multi_select", []):
                                        if opt.get("name"): status_names.append(opt.get("name"))

                            # 내용 추출
                            content_text = "(내용 없음)"
                            c_prop = props.get("내용")
                            if c_prop and c_prop.get("type") == "title":
                                arr = c_prop.get("title", [])
                                content_text = "".join([t.get("plain_text", "") for t in arr]).strip() or "(내용 없음)"
                            elif c_prop and c_prop.get("type") == "rich_text":
                                arr = c_prop.get("rich_text", [])
                                content_text = "".join([t.get("plain_text", "") for t in arr]).strip() or "(내용 없음)"

                            # 설명 추출
                            desc_text = "(설명 없음)"
                            d_prop = props.get("설명") or props.get("Description")
                            if d_prop and d_prop.get("type") == "rich_text":
                                arr = d_prop.get("rich_text", [])
                                desc_text = "".join([t.get("plain_text", "") for t in arr]).strip() or "(설명 없음)"

                            line = f"- {content_text} — {desc_text}"

                            if _any_completed(status_names):
                                new_completed_lines.append(line)
                            else:
                                new_request_lines.append(line)
                            
                            if status_names:
                                self.last_feature_status_by_id[rid] = ",".join(status_names)

                        channel = self.bot.get_channel(REPORT_CHANNEL_ID_FEATURE) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_FEATURE)
                        if new_request_lines:
                            await channel.send("\n".join(["기능 요청이 들어왔습니다 ✨"] + new_request_lines))
                        if new_completed_lines:
                            await channel.send("\n".join(["기능이 추가됐습니다 ✅"] + new_completed_lines))

                    # 1-2) 상태 변경 감지
                    status_change_lines = []
                    for row in rows:
                        rid = row["id"]
                        props = row.get("properties", {})
                        
                        status_names = []
                        status_prop = props.get("상태")
                        if not status_prop:
                             for v in props.values():
                                if isinstance(v, dict) and v.get("type") in ("status", "select", "multi_select"):
                                    status_prop = v
                                    break
                        if status_prop:
                            ptype = status_prop.get("type")
                            if ptype == "status":
                                name = status_prop.get("status", {}).get("name")
                                if name: status_names.append(name)
                            elif ptype == "select":
                                name = status_prop.get("select", {}).get("name")
                                if name: status_names.append(name)
                            elif ptype == "multi_select":
                                for opt in status_prop.get("multi_select", []):
                                    if opt.get("name"): status_names.append(opt.get("name"))

                        prev = self.last_feature_status_by_id.get(rid)
                        prev_comp = _any_completed([p.strip() for p in (prev.split(",") if prev else [])])
                        curr_comp = _any_completed(status_names)

                        if curr_comp and not prev_comp:
                             # 내용 추출 (중복 제거)
                            content_text = "(내용 없음)"
                            c_prop = props.get("내용")
                            if c_prop and c_prop.get("type") == "title":
                                arr = c_prop.get("title", [])
                                content_text = "".join([t.get("plain_text", "") for t in arr]).strip() or "(내용 없음)"
                            elif c_prop and c_prop.get("type") == "rich_text":
                                arr = c_prop.get("rich_text", [])
                                content_text = "".join([t.get("plain_text", "") for t in arr]).strip() or "(내용 없음)"

                            desc_text = "(설명 없음)"
                            d_prop = props.get("설명") or props.get("Description")
                            if d_prop and d_prop.get("type") == "rich_text":
                                arr = d_prop.get("rich_text", [])
                                desc_text = "".join([t.get("plain_text", "") for t in arr]).strip() or "(설명 없음)"

                            status_change_lines.append(f"- {content_text} — {desc_text}")

                        if status_names:
                            self.last_feature_status_by_id[rid] = ",".join(status_names)

                    if status_change_lines:
                        channel = self.bot.get_channel(REPORT_CHANNEL_ID_FEATURE) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_FEATURE)
                        await channel.send("\n".join(["기능이 추가됐습니다 ✅"] + status_change_lines))
                    
                    self.last_notion_row_ids = new_row_ids

                # ---------------------------------------------------------
                # 3. BOARD DB
                # ---------------------------------------------------------
                if NOTION_DATABASE_BOARD_ID and REPORT_CHANNEL_ID_ALARM:
                    board_rows = await self._fetch_notion_db(session, NOTION_DATABASE_BOARD_ID)
                    board_ids = {r["id"] for r in board_rows}
                    board_new = board_ids - self.last_board_row_ids
                    
                    if board_new:
                        channel = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
                        await channel.send("게시판에 새로운 글이 올라왔습니다.")
                    
                    self.last_board_row_ids = board_ids

                # ---------------------------------------------------------
                # 4. SCHEDULE DB
                # ---------------------------------------------------------
                if NOTION_DATABASE_SCHEDULE_ID and REPORT_CHANNEL_ID_ALARM:
                    sched_rows = await self._fetch_notion_db(session, NOTION_DATABASE_SCHEDULE_ID)
                    sched_ids = {r["id"] for r in sched_rows}
                    sched_new = sched_ids - self.last_schedule_row_ids
                    
                    if sched_new:
                        print(f"[NOTION] New schedule items ({len(sched_new)}). Waiting 20s...")
                        await asyncio.sleep(20)
                        sched_rows = await self._fetch_notion_db(session, NOTION_DATABASE_SCHEDULE_ID)

                        lines = ["새 일정이 등록되었습니다 📅"]
                        for row in sched_rows:
                             if row["id"] not in sched_new: continue
                             props = row.get("properties", {})
                             
                             # 날짜
                             date_str = ""
                             d_prop = props.get("날짜")
                             if not d_prop:
                                 for v in props.values(): 
                                     if isinstance(v, dict) and v.get("type") == "date": 
                                         d_prop = v; break
                             if d_prop and d_prop.get("type") == "date":
                                 d = d_prop.get("date") or {}
                                 s = _trim_to_minute(d.get("start"))
                                 e = _trim_to_minute(d.get("end"))
                                 date_str = s if not e else f"{s} ~ {e}"
                            
                             # 태그
                             tags = []
                             t_prop = props.get("태그")
                             if not t_prop:
                                 for v in props.values():
                                     if isinstance(v, dict) and v.get("type") == "multi_select":
                                         t_prop = v; break
                             if t_prop and t_prop.get("type") == "multi_select":
                                 for opt in t_prop.get("multi_select", []):
                                     if opt.get("name"): tags.append(opt.get("name"))
                             
                             tag_str = ", ".join(tags) if tags else "(태그 없음)"
                             lines.append(f"- {tag_str} — {date_str}" if date_str else f"- {tag_str}")

                        channel = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
                        await channel.send("\n".join(lines))

                    self.last_schedule_row_ids = sched_ids

        except Exception as e:
            print(f"[NOTION] Error: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(NotionWatcherCog(bot))