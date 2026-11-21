from __future__ import annotations
from app.services.settings_util import get_setting

import sqlite3

from app.services import clock as clock_svc


def room_active(conn: sqlite3.Connection, slot_code: str) -> bool:
    row = conn.execute(
        "SELECT active FROM slots WHERE code=?", (slot_code,)
    ).fetchone()
    return bool(row and row["active"] == 1)


def teacher_active(conn: sqlite3.Connection, executor_code: str) -> bool:
    row = conn.execute(
        "SELECT active FROM executors WHERE code=?", (executor_code,)
    ).fetchone()
    return bool(row and row["active"] == 1)


def overlap(
    a_start: int, a_end: int, b_start: int, b_end: int
) -> bool:
    return a_start < b_end and b_start < a_end


def resource_busy(
    conn: sqlite3.Connection,
    *,
    kind: str,
    resource_code: str,
    day: str,
    start_min: int,
    end_min: int,
    exclude_session: str | None = None,
    clock: dict | None = None,
) -> sqlite3.Row | None:
    """占用判定只看「当前仍在时间窗内」的 open 租约。

    已过 end_min 但仍 live 的租约由到点扫描收口，不再阻挡下窗；
    若扫描尚未跑到，这里同样不计为占用，避免下窗被无法解释地卡住。
    """
    clock = clock or clock_svc.read_clock(conn)
    col = "slot_code" if kind == "room" else "executor_code"
    rows = conn.execute(
        f"SELECT * FROM leases WHERE {col}=? AND day=? AND status='open'",
        (resource_code, day),
    ).fetchall()
    for r in rows:
        if exclude_session and r["window_code"] == exclude_session:
            continue
        if clock_svc.lease_expired(r, clock["day"], clock["now_min"]):
            continue
        if overlap(start_min, end_min, r["start_min"], r["end_min"]):
            return r
    return None


def readiness(
    conn: sqlite3.Connection, session: sqlite3.Row, clock: dict | None = None
) -> dict:
    clock = clock or clock_svc.read_clock(conn)
    reasons: list[str] = []
    if not room_active(conn, session["slot_code"]):
        reasons.append("算力槽未启用")
    if not teacher_active(conn, session["executor_code"]):
        reasons.append("执行器未启用")
    if clock_svc.is_past_end(
        session["day"], session["end_min"], clock["day"], clock["now_min"]
    ):
        reasons.append(f"已过结束时刻 {clock_svc.fmt_min(session['end_min'])}")

    busy_room = resource_busy(
        conn,
        kind="room",
        resource_code=session["slot_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
        exclude_session=session["code"],
        clock=clock,
    )
    if busy_room:
        reasons.append(f"算力槽已被租约 {busy_room['lease_no']} 占用")
    busy_teacher = resource_busy(
        conn,
        kind="teacher",
        resource_code=session["executor_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
        exclude_session=session["code"],
        clock=clock,
    )
    if busy_teacher:
        reasons.append(f"执行器已被租约 {busy_teacher['lease_no']} 占用")
    return {
        "ready": len(reasons) == 0,
        "reasons": reasons,
        "past_end": clock_svc.is_past_end(
            session["day"], session["end_min"], clock["day"], clock["now_min"]
        ),
    }
