# cogs/notion_watcher.py

import asyncio
import aiohttp
import json
import os
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
    return str(val).strip() if val else ""

class NotionWatcherCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # [핵심] 파일 위치를 data 폴더 내부로 지정 (재배포 시 삭제 방지)
        self.db_file = "data/notion_db.json"
        
        self.last_notion_row_ids: Set[str] = set()
        self.last_feature_status_by_id: Dict[str, str] = {}
        self.last_board_row_ids: Set[str] = set()
        self.last_schedule_row_ids: Set[str] = set()

        self.load_state()

    def load_state(self):
        """파일에서 상태 복구"""
        if not os.path.exists(self.db_file):
            print(f"[NOTION] {self.db_file} 파일이 없어 새로 시작합니다.")
            return

        try:
            with open(self.db_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.last_notion_row_ids = set(data.get("features", []))
                self.last_feature_status_by_id = data.get("feature_statuses", {})
                self.last_board_row_ids = set(data.get("boards", []))
                self.last_schedule_row_ids = set(data.get("schedules", []))
            print(f"[NOTION] {self.db_file} 로드 완료.")
        except Exception as e:
            print(f"[NOTION] 로드 중 오류: {e}")

    def save_state(self):
        """파일에 상태 저장"""
        data = {
            "features": list(self.last_notion_row_ids),
            "feature_statuses": self.last_feature_status_by_id,
            "boards": list(self.last_board_row_ids),
            "schedules": list(self.last_schedule_row_ids)
        }
        try:
            # data 폴더가 없을 경우를 대비해 폴더 생성
            os.makedirs(os.path.dirname(self.db_file), exist_ok=True)
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[NOTION] 저장 중 오류: {e}")

    async def cog_load(self) -> None:
        if NOTION_TOKEN and NOTION_DATABASE_FEATURE_ID:
            self.notion_update_poller.start()
        else:
            print("[NOTION] 설정 부족으로 폴링 안 함")

    def cog_unload(self) -> None:
        if self.notion_update_poller.is_running():
            self.notion_update_poller.cancel()

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
                    return []
                data = await resp.json()
                return data.get("results", [])
        except Exception:
            return []

    @tasks.loop(seconds=60)
    async def notion_update_poller(self):
        if not NOTION_TOKEN:
            return

        print("[NOTION] poller tick")

        try:
            async with aiohttp.ClientSession() as session:
                # 1. Feature DB
                if NOTION_DATABASE_FEATURE_ID:
                    rows = await self._fetch_notion_db(session, NOTION_DATABASE_FEATURE_ID)
                    new_row_ids = {row["id"] for row in rows}
                    only_new = new_row_ids - self.last_notion_row_ids
                    
                    if only_new:
                        print(f"[NOTION] New rows detected. Waiting 20s...")
                        await asyncio.sleep(20)
                        rows = await self._fetch_notion_db(session, NOTION_DATABASE_FEATURE_ID)
                        # ID 재계산 안 함 (ID는 불변)

                    # 신규 알림
                    if only_new:
                        new_req = []
                        new_comp = []
                        for row in rows:
                            if row["id"] not in only_new: continue
                            
                            rid = row["id"]
                            props = row.get("properties", {})
                            
                            # 상태
                            status_names = []
                            st = props.get("상태")
                            if not st:
                                for v in props.values():
                                    if isinstance(v, dict) and v.get("type") in ("status", "select", "multi_select"):
                                        st = v; break
                            if st:
                                t = st.get("type")
                                if t == "status":
                                    n = st.get("status", {}).get("name")
                                    if n: status_names.append(n)
                                elif t == "select":
                                    n = st.get("select", {}).get("name")
                                    if n: status_names.append(n)
                                elif t == "multi_select":
                                    for o in st.get("multi_select", []):
                                        if o.get("name"): status_names.append(o.get("name"))

                            # 내용/설명
                            c_txt = "(내용 없음)"
                            cp = props.get("내용")
                            if cp and cp.get("type") == "title":
                                c_txt = "".join([x.get("plain_text","") for x in cp.get("title",[])]).strip() or "(내용 없음)"
                            elif cp and cp.get("type") == "rich_text":
                                c_txt = "".join([x.get("plain_text","") for x in cp.get("rich_text",[])]).strip() or "(내용 없음)"
                            
                            d_txt = "(설명 없음)"
                            dp = props.get("설명") or props.get("Description")
                            if dp and dp.get("type") == "rich_text":
                                d_txt = "".join([x.get("plain_text","") for x in dp.get("rich_text",[])]).strip() or "(설명 없음)"

                            line = f"- {c_txt} — {d_txt}"
                            if _any_completed(status_names): new_comp.append(line)
                            else: new_req.append(line)

                            if status_names: self.last_feature_status_by_id[rid] = ",".join(status_names)

                        ch = self.bot.get_channel(REPORT_CHANNEL_ID_FEATURE) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_FEATURE)
                        if new_req: await ch.send("\n".join(["기능 요청이 들어왔습니다 ✨"] + new_req))
                        if new_comp: await ch.send("\n".join(["기능이 추가됐습니다 ✅"] + new_comp))

                    # 상태 변경 알림
                    st_change = []
                    for row in rows:
                        rid = row["id"]
                        props = row.get("properties", {})
                        
                        status_names = []
                        st = props.get("상태")
                        if not st:
                            for v in props.values():
                                if isinstance(v, dict) and v.get("type") in ("status", "select", "multi_select"):
                                    st = v; break
                        if st:
                            t = st.get("type")
                            if t == "status":
                                n = st.get("status", {}).get("name")
                                if n: status_names.append(n)
                            elif t == "select":
                                n = st.get("select", {}).get("name")
                                if n: status_names.append(n)
                            elif t == "multi_select":
                                for o in st.get("multi_select", []):
                                    if o.get("name"): status_names.append(o.get("name"))

                        prev = self.last_feature_status_by_id.get(rid)
                        if prev is None: # 최초 로딩 시 알림 방지
                            if status_names: self.last_feature_status_by_id[rid] = ",".join(status_names)
                            continue

                        prev_c = _any_completed([p.strip() for p in (prev.split(",") if prev else [])])
                        curr_c = _any_completed(status_names)

                        if curr_c and not prev_c:
                             # 내용/설명 추출
                            c_txt = "(내용 없음)"
                            cp = props.get("내용")
                            if cp and cp.get("type") == "title":
                                c_txt = "".join([x.get("plain_text","") for x in cp.get("title",[])]).strip() or "(내용 없음)"
                            elif cp and cp.get("type") == "rich_text":
                                c_txt = "".join([x.get("plain_text","") for x in cp.get("rich_text",[])]).strip() or "(내용 없음)"
                            
                            d_txt = "(설명 없음)"
                            dp = props.get("설명") or props.get("Description")
                            if dp and dp.get("type") == "rich_text":
                                d_txt = "".join([x.get("plain_text","") for x in dp.get("rich_text",[])]).strip() or "(설명 없음)"
                            
                            st_change.append(f"- {c_txt} — {d_txt}")

                        if status_names: self.last_feature_status_by_id[rid] = ",".join(status_names)

                    if st_change:
                        ch = self.bot.get_channel(REPORT_CHANNEL_ID_FEATURE) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_FEATURE)
                        await ch.send("\n".join(["기능이 추가됐습니다 ✅"] + st_change))
                    
                    if only_new or st_change or (new_row_ids != self.last_notion_row_ids):
                        self.last_notion_row_ids = new_row_ids
                        self.save_state()

                # 3. Board DB
                if NOTION_DATABASE_BOARD_ID and REPORT_CHANNEL_ID_ALARM:
                    rows = await self._fetch_notion_db(session, NOTION_DATABASE_BOARD_ID)
                    ids = {r["id"] for r in rows}
                    new_ids = ids - self.last_board_row_ids
                    if new_ids:
                        ch = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
                        await ch.send("게시판에 새로운 글이 올라왔습니다.")
                        self.last_board_row_ids = ids
                        self.save_state()

                # 4. Schedule DB
                if NOTION_DATABASE_SCHEDULE_ID and REPORT_CHANNEL_ID_ALARM:
                    rows = await self._fetch_notion_db(session, NOTION_DATABASE_SCHEDULE_ID)
                    ids = {r["id"] for r in rows}
                    new_ids = ids - self.last_schedule_row_ids
                    if new_ids:
                        print(f"[NOTION] New schedule. Waiting 20s...")
                        await asyncio.sleep(20)
                        rows = await self._fetch_notion_db(session, NOTION_DATABASE_SCHEDULE_ID)
                        ids = {r["id"] for r in rows} # 재계산
                        new_ids = ids - self.last_schedule_row_ids
                        
                        if new_ids:
                            lines = ["새 일정이 등록되었습니다 📅"]
                            for row in rows:
                                if row["id"] not in new_ids: continue
                                props = row.get("properties", {})
                                
                                # 날짜
                                d_str = ""
                                dp = props.get("날짜")
                                if not dp:
                                    for v in props.values():
                                        if isinstance(v, dict) and v.get("type") == "date": dp=v; break
                                if dp and dp.get("type")=="date":
                                    d = dp.get("date") or {}
                                    s = _trim_to_minute(d.get("start"))
                                    e = _trim_to_minute(d.get("end"))
                                    d_str = s if not e else f"{s} ~ {e}"
                                
                                # 태그
                                tags = []
                                tp = props.get("태그")
                                if not tp:
                                    for v in props.values():
                                        if isinstance(v, dict) and v.get("type") == "multi_select": tp=v; break
                                if tp and tp.get("type")=="multi_select":
                                    for o in tp.get("multi_select",[]):
                                        if o.get("name"): tags.append(o.get("name"))
                                
                                t_str = ", ".join(tags) if tags else "(태그 없음)"
                                lines.append(f"- {t_str} — {d_str}" if d_str else f"- {t_str}")

                            ch = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) or await self.bot.fetch_channel(REPORT_CHANNEL_ID_ALARM)
                            await ch.send("\n".join(lines))
                            
                        self.last_schedule_row_ids = ids
                        self.save_state()

        except Exception as e:
            print(f"[NOTION] Error: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(NotionWatcherCog(bot))