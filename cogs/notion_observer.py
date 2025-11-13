# cogs/notion_watcher.py
import asyncio
from typing import Dict, Set

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
from time_utils import _  # 필요하면 time_utils에 헬퍼 추가

# 기존 코드에 있던 헬퍼들(_is_completed_status, _any_completed, _trim_to_minute)
# 을 이 파일로 그대로 가져오시면 됩니다.

class NotionWatcherCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_notion_row_ids: Set[str] = set()
        self.last_feature_status_by_id: Dict[str, str] = {}
        self.last_board_row_ids: Set[str] = set()
        self.last_schedule_row_ids: Set[str] = set()

        self.notion = NotionClient(auth=NOTION_TOKEN) if NOTION_TOKEN else None

        if self.notion and NOTION_DATABASE_FEATURE_ID:
            self.notion_update_poller.start()

    def cog_unload(self):
        self.notion_update_poller.cancel()

    @tasks.loop(seconds=60)
    async def notion_update_poller(self):
        # 여기 안에 지금 쓰시던 긴 내용(Feature/Board/Schedule 쿼리 + 알림 전송)을
        # self.notion, self.last_... 멤버들을 사용하도록만 고쳐서 그대로 넣으시면 됩니다.
        ...
        # (기존 코드 거의 그대로 복붙 가능, 전역 변수 -> self.멤버 로만 바꾸시면 됩니다.)

async def setup(bot: commands.Bot):
    await bot.add_cog(NotionWatcherCog(bot))
