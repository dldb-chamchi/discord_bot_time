# time_utils.py
import datetime as dt

KST = dt.timezone(dt.timedelta(hours=9))

def now_kst() -> dt.datetime:
    return dt.datetime.now(tz=KST)

def iso(dtobj: dt.datetime) -> str:
    return dtobj.astimezone(KST).isoformat()

def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)
