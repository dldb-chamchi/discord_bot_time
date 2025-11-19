import json
import random
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

DATA_DIR = Path(__file__).parent / "data"
MENUS_FILE = DATA_DIR / "menus_kr.json"
HISTORY_FILE = DATA_DIR / "menu_history.json"

COOLDOWN_SECONDS = 3 * 24 * 60 * 60  # 최근 3일 회피

def _load_json(path: Path, default):
    # 디버깅을 위해 절대 경로를 출력합니다.
    abs_path = path.resolve()
    print(f"[DEBUG] JSON 파일 로드 시도: {abs_path}")

    if not path.exists():
        print(f"[DEBUG] 오류: 파일을 찾을 수 없습니다.")
        return default
    
    print(f"[DEBUG] 성공: 파일을 찾았습니다.")
    
    try:
        with path.open("r", encoding="utf-8") as f:
            content = f.read()
            if not content.strip():
                print("[DEBUG] 오류: 파일 내용이 비어있습니다.")
                return default
            
            data = json.loads(content)
            print(f"[DEBUG] 성공: JSON 파싱 완료. {len(data)}개의 메뉴를 불러왔습니다.")
            return data
    except json.JSONDecodeError as e:
        print(f"[DEBUG] 오류: JSON 형식이 올바르지 않습니다. {e}")
        return default
    except Exception as e:
        print(f"[DEBUG] 오류: 파일을 읽는 중 예외가 발생했습니다. {e}")
        return default

def _save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

class MenuRecommender:
    def __init__(self, menus_path: Path = MENUS_FILE, history_path: Path = HISTORY_FILE):
        self.menus_path = menus_path
        self.history_path = history_path
        self.menus: List[Dict[str, Any]] = _load_json(self.menus_path, [])
        self.history: Dict[str, List[Dict[str, Any]]] = _load_json(self.history_path, {})

    def reload(self):
        self.menus = _load_json(self.menus_path, [])

    def _is_recent(self, item_name: str, scope_key: str) -> bool:
        now = time.time()
        entries = self.history.get(scope_key, [])
        return any(e["name"] == item_name and (now - e["ts"]) < COOLDOWN_SECONDS for e in entries)

    def _record(self, item_name: str, scope_key: str):
        now = time.time()
        entries = self.history.setdefault(scope_key, [])
        entries.append({"name": item_name, "ts": now})
        # 오래된 기록 정리
        self.history[scope_key] = [e for e in entries if (now - e["ts"]) < (COOLDOWN_SECONDS * 2)]
        _save_json(self.history_path, self.history)

    def recommend(self, guild_id: Optional[int], user_id: Optional[int]) -> Optional[Dict[str, Any]]:
        candidates = self.menus[:]
        if not candidates:
            return None

        scope_keys = []
        if guild_id:
            scope_keys.append(f"guild:{guild_id}")
        if user_id:
            scope_keys.append(f"user:{user_id}")

        non_recent = [m for m in candidates if not any(self._is_recent(m["name"], k) for k in scope_keys)]
        pool = non_recent if non_recent else candidates

        choice = random.choice(pool)
        for k in scope_keys:
            self._record(choice["name"], k)
        return choice
    