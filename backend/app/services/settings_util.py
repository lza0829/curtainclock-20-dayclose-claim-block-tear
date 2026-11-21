from __future__ import annotations

import json
import sqlite3


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def put_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_json(conn: sqlite3.Connection, key: str, default=None):
    raw = get_setting(conn, key, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def put_json(conn: sqlite3.Connection, key: str, value) -> None:
    put_setting(conn, key, json.dumps(value, ensure_ascii=False))


def is_day_closed(conn: sqlite3.Connection, day: str) -> bool:
    """批窗日界：该日是否已日切收口（存在日切回执即视为日界已切）。

    日界状态唯一由日切回执（settings 表 day_close:<day>）派生，
    批窗预检与开窗都用它拦截「已切日再开窗」，与租约表互证。
    """
    return get_json(conn, f"day_close:{day}") is not None
