# cogs/notion_watcher.py

import asyncio
from typing import Dict, Set, List

from discord.ext import commands, tasks
from notion_client import AsyncClient as NotionClient

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
    """ISO 문자열을 'YYYY-MM-DD HH:MM' 형태로 자르기"""
    if not iso_str:
        return ""
    if "T" in iso_str:
        date_part, time_part = iso_str.split("T", 1)
        hhmm = time_part[:5]
        return f"{date_part} {hhmm}"
    return iso_str


class NotionWatcherCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # 상태 저장용
        self.last_notion_row_ids: Set[str] = set()
        self.last_feature_status_by_id: Dict[str, str] = {}
        self.last_board_row_ids: Set[str] = set()
        self.last_schedule_row_ids: Set[str] = set()

    async def cog_load(self) -> None:
        """Cog가 로드될 때 호출됨 (discord.py 2.x)"""
        if NOTION_TOKEN and NOTION_DATABASE_FEATURE_ID:
            self.notion_update_poller.start()
            print("[NOTION] notion_update_poller started")
        else:
            print("[NOTION] NOTION_TOKEN 또는 NOTION_DATABASE_FEATURE_ID가 설정되지 않아 폴링을 시작하지 않습니다.")

    def cog_unload(self) -> None:
        """Cog가 언로드될 때 호출"""
        if self.notion_update_poller.is_running():
            self.notion_update_poller.cancel()
            print("[NOTION] notion_update_poller stopped")

    # =========================
    # 노션 업데이트 폴링
    # =========================
    @tasks.loop(seconds=60)
    async def notion_update_poller(self):
        if not NOTION_TOKEN or not NOTION_DATABASE_FEATURE_ID:
            return  # 설정이 없으면 아무 것도 안 함

        print("[NOTION] poller tick")  # 디버그용

        try:
            notion = NotionClient(auth=NOTION_TOKEN)

            # [DEBUG] URL 확인을 위한 코드 추가
            debug_path = f"databases/{NOTION_DATABASE_FEATURE_ID}/query"
            print(f"[DEBUG] Requesting Path: '{debug_path}'")

            # ---------------------------------------------------------
            # 1. FEATURE DB 조회
            # ---------------------------------------------------------
            feature_res = await notion.request(
                path=debug_path,
                method="post",
                body={
                    "page_size": 50,
                    "sorts": [
                        {"timestamp": "last_edited_time", "direction": "descending"}
                    ],
                },
            )
            rows = feature_res.get("results", [])
            new_row_ids = {row["id"] for row in rows}

            # 신규 행 감지
            only_new = new_row_ids - self.last_notion_row_ids
            if only_new:
                print(f"[NOTION][FEATURE] New rows detected ({len(only_new)}). Waiting 20s for user input...")
                await asyncio.sleep(20)

                # 최신 데이터 다시 조회
                feature_res = await notion.request(
                    path=debug_path,
                    method="post",
                    body={
                        "page_size": 50,
                        "sorts": [
                            {"timestamp": "last_edited_time", "direction": "descending"}
                        ],
                    },
                )
                rows = feature_res.get("results", [])

            # 1-1) FEATURE: 신규 행 처리 (완료/요청 분기)
            if only_new:
                new_request_lines: List[str] = []
                new_completed_lines: List[str] = []

                for row in rows:
                    if row["id"] not in only_new:
                        continue

                    rid = row["id"]
                    props = row.get("properties", {})

                    # --- 상태 이름 추출 ---
                    status_names: List[str] = []
                    status_prop = props.get("상태") or {}
                    if not status_prop:
                        for _, v in props.items():
                            if isinstance(v, dict) and v.get("type") in ("status", "select", "multi_select"):
                                status_prop = v
                                break

                    if isinstance(status_prop, dict):
                        ptype = status_prop.get("type")
                        if ptype == "status":
                            st = status_prop.get("status") or {}
                            val = (st.get("name") or "").strip()
                            if val:
                                status_names.append(val)
                        elif ptype == "select":
                            st = status_prop.get("select") or {}
                            val = (st.get("name") or "").strip()
                            if val:
                                status_names.append(val)
                        elif ptype == "multi_select":
                            arr = status_prop.get("multi_select", []) or []
                            for opt in arr:
                                val = (opt.get("name") or "").strip()
                                if val:
                                    status_names.append(val)

                    # --- 내용 추출 ---
                    content_text = ""
                    content_prop = props.get("내용") or {}
                    if isinstance(content_prop, dict):
                        ctype = content_prop.get("type")
                        if ctype == "rich_text":
                            arr = content_prop.get("rich_text", [])
                            content_text = "".join([t.get("plain_text", "") for t in arr]).strip()
                        elif ctype == "title":
                            arr = content_prop.get("title", [])
                            content_text = "".join([t.get("plain_text", "") for t in arr]).strip()
                    if not content_text:
                        content_text = "(내용 없음)"

                    # --- 설명 추출 ---
                    desc_text = ""
                    desc_prop = props.get("설명") or props.get("Description") or {}
                    if isinstance(desc_prop, dict) and desc_prop.get("type") == "rich_text":
                        arr = desc_prop.get("rich_text", [])
                        desc_text = "".join([t.get("plain_text", "") for t in arr]).strip()
                    if not desc_text:
                        desc_text = "(설명 없음)"

                    line = f"- {content_text} — {desc_text}"

                    # 완료 여부에 따라 분류
                    if _any_completed(status_names):
                        new_completed_lines.append(line)
                    else:
                        new_request_lines.append(line)

                    # 상태 저장
                    if status_names:
                        self.last_feature_status_by_id[rid] = ",".join(status_names)

                channel = self.bot.get_channel(REPORT_CHANNEL_ID_FEATURE) or await self.bot.fetch_channel(
                    REPORT_CHANNEL_ID_FEATURE
                )
                if new_request_lines:
                    header = "기능 요청이 들어왔습니다 ✨"
                    await channel.send("\n".join([header] + new_request_lines))
                if new_completed_lines:
                    header = "기능이 추가됐습니다 ✅"
                    await channel.send("\n".join([header] + new_completed_lines))

            # 1-2) FEATURE: 상태 변경 감지
            status_change_lines: List[str] = []
            for row in rows:
                rid = row["id"]
                props = row.get("properties", {})

                # 상태 추출
                status_names: List[str] = []
                status_prop = props.get("상태") or {}
                if not status_prop:
                    for _, v in props.items():
                        if isinstance(v, dict) and v.get("type") in ("status", "select", "multi_select"):
                            status_prop = v
                            break
                if isinstance(status_prop, dict):
                    ptype = status_prop.get("type")
                    if ptype == "status":
                        st = status_prop.get("status") or {}
                        val = (st.get("name") or "").strip()
                        if val:
                            status_names.append(val)
                    elif ptype == "select":
                        st = status_prop.get("select") or {}
                        val = (st.get("name") or "").strip()
                        if val:
                            status_names.append(val)
                    elif ptype == "multi_select":
                        arr = status_prop.get("multi_select", []) or []
                        for opt in arr:
                            val = (opt.get("name") or "").strip()
                            if val:
                                status_names.append(val)

                prev = self.last_feature_status_by_id.get(rid)
                prev_completed = _any_completed(
                    [p.strip() for p in (prev.split(",") if prev else [])]
                )
                curr_completed = _any_completed(status_names)

                if curr_completed and not prev_completed:
                    # 내용 추출
                    content_text = ""
                    content_prop = props.get("내용") or {}
                    if isinstance(content_prop, dict):
                        ctype = content_prop.get("type")
                        if ctype == "rich_text":
                            arr = content_prop.get("rich_text", [])
                            content_text = "".join([t.get("plain_text", "") for t in arr]).strip()
                        elif ctype == "title":
                            arr = content_prop.get("title", [])
                            content_text = "".join([t.get("plain_text", "") for t in arr]).strip()
                    if not content_text:
                        content_text = "(내용 없음)"

                    # 설명 추출
                    desc_text = ""
                    desc_prop = props.get("설명") or props.get("Description") or {}
                    if isinstance(desc_prop, dict) and desc_prop.get("type") == "rich_text":
                        arr = desc_prop.get("rich_text", [])
                        desc_text = "".join([t.get("plain_text", "") for t in arr]).strip()
                    if not desc_text:
                        desc_text = "(설명 없음)"

                    status_change_lines.append(f"- {content_text} — {desc_text}")

                if status_names:
                    self.last_feature_status_by_id[rid] = ",".join(status_names)

            if status_change_lines:
                header = "기능이 추가됐습니다 ✅"
                channel = self.bot.get_channel(REPORT_CHANNEL_ID_FEATURE) or await self.bot.fetch_channel(
                    REPORT_CHANNEL_ID_FEATURE
                )
                await channel.send("\n".join([header] + status_change_lines))

            # ---------------------------------------------------------
            # 3. BOARD DB: 신규 글 감지  (REST로 변경)
            # ---------------------------------------------------------
            if NOTION_DATABASE_BOARD_ID and REPORT_CHANNEL_ID_ALARM:
                try:
                    board_res = await notion.request(
                        path=f"databases/{NOTION_DATABASE_BOARD_ID}/query",
                        method="post",
                        body={
                            "page_size": 20,
                            "sorts": [
                                {"timestamp": "last_edited_time", "direction": "descending"}
                            ],
                        },
                    )
                    board_rows = board_res.get("results", [])
                    board_ids = {row["id"] for row in board_rows}
                    board_new = board_ids - self.last_board_row_ids

                    if board_new:
                        channel = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) or await self.bot.fetch_channel(
                            REPORT_CHANNEL_ID_ALARM
                        )
                        msg = "게시판에 새로운 글이 올라왔습니다."
                        await channel.send(msg)

                    self.last_board_row_ids = board_ids
                except Exception as e:
                    print(f"[NOTION][BOARD] Error: {e}")

            # ---------------------------------------------------------
            # 4. SCHEDULE DB: 신규 일정 감지  (REST로 변경)
            # ---------------------------------------------------------
            if NOTION_DATABASE_SCHEDULE_ID and REPORT_CHANNEL_ID_ALARM:
                try:
                    sched_res = await notion.request(
                        path=f"databases/{NOTION_DATABASE_SCHEDULE_ID}/query",
                        method="post",
                        body={
                            "page_size": 20,
                            "sorts": [
                                {"timestamp": "last_edited_time", "direction": "descending"}
                            ],
                        },
                    )
                    sched_rows = sched_res.get("results", [])
                    sched_ids = {row["id"] for row in sched_rows}
                    sched_new = sched_ids - self.last_schedule_row_ids

                    if sched_new:
                        print(f"[NOTION][SCHEDULE] New items detected ({len(sched_new)}). Waiting 20s...")
                        await asyncio.sleep(20)

                        # 다시 조회
                        sched_res = await notion.request(
                            path=f"databases/{NOTION_DATABASE_SCHEDULE_ID}/query",
                            method="post",
                            body={
                                "page_size": 20,
                                "sorts": [
                                    {"timestamp": "last_edited_time", "direction": "descending"}
                                ],
                            },
                        )
                        sched_rows = sched_res.get("results", [])

                    if sched_new:
                        lines: List[str] = ["새 일정이 등록되었습니다 📅"]
                        for row in sched_rows:
                            if row["id"] not in sched_new:
                                continue

                            props = row.get("properties", {})

                            # 날짜 추출
                            date_str = ""
                            date_prop = props.get("날짜") or {}
                            if not date_prop:
                                for _, v in props.items():
                                    if isinstance(v, dict) and v.get("type") == "date":
                                        date_prop = v
                                        break
                            if isinstance(date_prop, dict) and date_prop.get("type") == "date":
                                d = date_prop.get("date") or {}
                                start = _trim_to_minute(d.get("start") or "")
                                end = _trim_to_minute(d.get("end") or "")
                                date_str = start if not end else f"{start} ~ {end}"

                            # 태그 추출
                            tags: List[str] = []
                            tag_prop = props.get("태그") or {}
                            if not tag_prop:
                                for _, v in props.items():
                                    if isinstance(v, dict) and v.get("type") == "multi_select":
                                        tag_prop = v
                                        break
                            if isinstance(tag_prop, dict) and tag_prop.get("type") == "multi_select":
                                for opt in tag_prop.get("multi_select", []) or []:
                                    name = (opt.get("name") or "").strip()
                                    if name:
                                        tags.append(name)

                            tag_str = ", ".join(tags) if tags else "(태그 없음)"
                            if date_str:
                                lines.append(f"- {tag_str} — {date_str}")
                            else:
                                lines.append(f"- {tag_str}")

                        channel = self.bot.get_channel(REPORT_CHANNEL_ID_ALARM) or await self.bot.fetch_channel(
                            REPORT_CHANNEL_ID_ALARM
                        )
                        await channel.send("\n".join(lines))

                    self.last_schedule_row_ids = sched_ids
                except Exception as e:
                    print(f"[NOTION][SCHEDULE] Error: {e}")

            # 마지막에 FEATURE DB의 ID 집합 동기화
            self.last_notion_row_ids = new_row_ids

        except Exception as e:
            print(f"[NOTION] Error: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(NotionWatcherCog(bot))