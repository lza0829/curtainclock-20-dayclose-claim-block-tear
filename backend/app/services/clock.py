from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

# 批窗时间以「观察钟」为准：day + 当日分钟数。
# 真实时间戳（opened_at / closed_at / audit.at）仍用 UTC，两者互不混用。
DEFAULT_DAY = "2026-11-20"
DEFAULT_NOW_MIN = 9 * 60


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_min(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def read_clock(conn: sqlite3.Connection) -> dict:
    day = _setting(conn, "clock_day") or _setting(conn, "fixture_day") or DEFAULT_DAY
    raw = _setting(conn, "clock_min")
    try:
        now_min = int(raw) if raw is not None else DEFAULT_NOW_MIN
    except ValueError:
        now_min = DEFAULT_NOW_MIN
    now_min = max(0, min(24 * 60 - 1, now_min))
    return {"day": day, "now_min": now_min, "label": fmt_min(now_min)}


def write_clock(conn: sqlite3.Connection, day: str, now_min: int) -> None:
    if now_min < 0 or now_min > 24 * 60 - 1:
        raise ValueError("now_min 必须在 0..1439 之间")
    for key, value in (("clock_day", day), ("clock_min", str(now_min))):
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def is_past_end(day: str, end_min: int, clock_day: str, now_min: int) -> bool:
    """到了 end_min（含）即视为过期；扫描以此时刻为界收口。"""
    return day < clock_day or (day == clock_day and end_min <= now_min)


def lease_expired(lease: sqlite3.Row, clock_day: str, now_min: int) -> bool:
    return is_past_end(lease["day"], lease["end_min"], clock_day, now_min)


def audit(
    conn: sqlite3.Connection,
    action: str,
    *,
    actor: str,
    window_code: str | None = None,
    lease_no: str | None = None,
    before_end_min: int | None = None,
    after_end_min: int | None = None,
    detail: str = "",
) -> None:
    conn.execute(
        "INSERT INTO audit_log(at, actor, action, window_code, lease_no, "
        "before_end_min, after_end_min, detail) VALUES(?,?,?,?,?,?,?,?)",
        (
            now_iso(),
            actor,
            action,
            window_code,
            lease_no,
            before_end_min,
            after_end_min,
            detail,
        ),
    )
