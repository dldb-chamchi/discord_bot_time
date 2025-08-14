import os
import json
import datetime as dt
from typing import Dict, Any

KST = dt.timezone(dt.timedelta(hours=9))

def now_kst() -> dt.datetime:
    return dt.datetime.now(tz=KST)

def iso(dtobj: dt.datetime) -> str:
    return dtobj.astimezone(KST).isoformat()

def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)

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
