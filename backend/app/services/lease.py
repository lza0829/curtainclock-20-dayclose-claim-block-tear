from __future__ import annotations

import sqlite3

from app.config import POLICY
from app.services import clock as clock_svc
from app.services.ready import readiness, resource_busy


def _bump(conn: sqlite3.Connection, key: str, delta: int) -> int:
    row = conn.execute("SELECT value FROM watermarks WHERE key=?", (key,)).fetchone()
    cur = int(row["value"]) if row else 0
    nxt = cur + delta
    conn.execute(
        "INSERT INTO watermarks(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, nxt, clock_svc.now_iso()),
    )
    return nxt


def _set_watermark(conn: sqlite3.Connection, key: str, value: int) -> None:
    conn.execute(
        "INSERT INTO watermarks(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, clock_svc.now_iso()),
    )


def _watermark(conn: sqlite3.Connection, key: str) -> int:
    row = conn.execute("SELECT value FROM watermarks WHERE key=?", (key,)).fetchone()
    return int(row["value"]) if row else 0


def _conflict_mode(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM settings WHERE key='conflict_mode'"
    ).fetchone()
    return row["value"] if row else "reject"


def actual_open_leases(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT COUNT(*) c FROM leases WHERE status='open'").fetchone()["c"]
    )


def reconcile_open_watermark(conn: sqlite3.Connection) -> dict:
    """水位以租约表实际占用数为准；漂移时纠正并留审计。"""
    actual = actual_open_leases(conn)
    watermark = _watermark(conn, "open_leases")
    if watermark != actual:
        _set_watermark(conn, "open_leases", actual)
        clock_svc.audit(
            conn,
            "watermark_reconcile",
            actor="system",
            detail=f"open_leases 水位 {watermark} → 实际 {actual}",
        )
        return {"watermark": actual, "actual": actual, "reconciled": True}
    return {"watermark": watermark, "actual": actual, "reconciled": False}


def sweep_expired(
    conn: sqlite3.Connection,
    clock: dict | None = None,
    *,
    actor: str = "sweeper",
) -> list[dict]:
    """到点扫描：把已过 end_min 的 open 租约收口（关租约、关批窗、减水位）。

    这是本部署唯一的口径——到点后占用只能由扫描/人工关窗收口，
    不允许静默延长 end_min。扫描与开窗都在 BEGIN IMMEDIATE 事务里执行，
    二者互斥，不会出现扫到一半被新开窗双占。
    """
    clock = clock or clock_svc.read_clock(conn)
    rows = conn.execute(
        "SELECT * FROM leases WHERE status='open' "
        "AND (day < ? OR (day = ? AND end_min <= ?)) "
        "ORDER BY day, end_min, id",
        (clock["day"], clock["day"], clock["now_min"]),
    ).fetchall()

    closed: list[dict] = []
    for lease in rows:
        conn.execute(
            "UPDATE leases SET status='closed', closed_at=? WHERE lease_no=? AND status='open'",
            (clock_svc.now_iso(), lease["lease_no"]),
        )
        conn.execute(
            "UPDATE windows SET status='done' WHERE code=?",
            (lease["window_code"],),
        )
        _bump(conn, "open_leases", -1)
        detail = (
            f"到点扫描：end_min={clock_svc.fmt_min(lease['end_min'])} "
            f"≤ 观察钟 {clock['label']}"
        )
        clock_svc.audit(
            conn,
            "sweep_close",
            actor=actor,
            window_code=lease["window_code"],
            lease_no=lease["lease_no"],
            detail=detail,
        )
        closed.append(
            {
                "lease_no": lease["lease_no"],
                "window_code": lease["window_code"],
                "end_min": lease["end_min"],
                "reason": detail,
            }
        )

    reconcile_open_watermark(conn)
    return closed


def open_lease(conn: sqlite3.Connection, window_code: str) -> dict:
    # 扫描臂：开窗事务先收口过期占用，保证「到点后下窗可开」，
    # 且与扫描共用同一写事务锁，不会双占。
    clock = clock_svc.read_clock(conn)
    swept = sweep_expired(conn, clock, actor="open:auto_sweep")

    session = conn.execute(
        "SELECT * FROM windows WHERE code=?", (window_code,)
    ).fetchone()
    if session is None:
        raise ValueError(f"批窗不存在：{window_code}")
    if session["status"] == "live":
        raise ValueError("批窗已在占用中，勿重复开窗")
    if session["status"] == "done":
        raise ValueError("批窗已结束")
    if clock_svc.is_past_end(
        session["day"], session["end_min"], clock["day"], clock["now_min"]
    ):
        raise ValueError(
            f"批窗已过结束时刻 {clock_svc.fmt_min(session['end_min'])}，不能开窗"
        )

    ready = readiness(conn, session, clock=clock)
    if not ready["ready"]:
        mode = _conflict_mode(conn)
        for reason in ready["reasons"]:
            kind = "room" if "算力槽" in reason else ("teacher" if "执行器" in reason else "ready")
            resource = (
                session["slot_code"]
                if kind == "room"
                else (session["executor_code"] if kind == "teacher" else window_code)
            )
            conn.execute(
                "INSERT INTO conflicts(kind, window_code, resource_code, day, start_min, end_min, reason, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    kind,
                    window_code,
                    resource,
                    session["day"],
                    session["start_min"],
                    session["end_min"],
                    reason,
                    clock_svc.now_iso(),
                ),
            )
            _bump(conn, "conflict_count", 1)
        return {
            "ok": False,
            "error": "；".join(ready["reasons"]),
            "conflict_mode": mode,
            "swept": swept,
        }

    if resource_busy(
        conn,
        kind="room",
        resource_code=session["slot_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
    ) or resource_busy(
        conn,
        kind="teacher",
        resource_code=session["executor_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
    ):
        raise ValueError("资源刚被占用，开窗失败")

    n = conn.execute("SELECT COUNT(*) c FROM leases").fetchone()["c"] + 1
    lease_no = f"L-{session['day'].replace('-', '')}-{n:04d}"
    conn.execute(
        "INSERT INTO leases(lease_no, window_code, slot_code, executor_code, day, start_min, end_min, status, opened_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            lease_no,
            window_code,
            session["slot_code"],
            session["executor_code"],
            session["day"],
            session["start_min"],
            session["end_min"],
            "open",
            clock_svc.now_iso(),
        ),
    )
    conn.execute("UPDATE windows SET status='live' WHERE code=?", (window_code,))
    _bump(conn, "open_leases", 1)
    reconcile_open_watermark(conn)
    clock_svc.audit(
        conn,
        "open_lease",
        actor="operator",
        window_code=window_code,
        lease_no=lease_no,
        detail=(
            f"开窗占用 {clock_svc.fmt_min(session['start_min'])}–"
            f"{clock_svc.fmt_min(session['end_min'])}"
        ),
    )
    return {
        "ok": True,
        "lease_no": lease_no,
        "window_code": window_code,
        "status": "open",
        "open_leases": actual_open_leases(conn),
        "swept": swept,
    }


def close_lease(conn: sqlite3.Connection, lease_no: str, *, actor: str = "operator") -> dict:
    lease = conn.execute(
        "SELECT * FROM leases WHERE lease_no=?", (lease_no,)
    ).fetchone()
    if lease is None:
        raise ValueError(f"租约不存在：{lease_no}")
    if lease["status"] != "open":
        raise ValueError("租约不在占用态")
    conn.execute(
        "UPDATE leases SET status='closed', closed_at=? WHERE lease_no=?",
        (clock_svc.now_iso(), lease_no),
    )
    conn.execute(
        "UPDATE windows SET status='done' WHERE code=?",
        (lease["window_code"],),
    )
    _bump(conn, "open_leases", -1)
    reconcile_open_watermark(conn)
    clock_svc.audit(
        conn,
        "manual_close",
        actor=actor,
        window_code=lease["window_code"],
        lease_no=lease_no,
        detail="人工关窗释放",
    )
    return {
        "lease_no": lease_no,
        "status": "closed",
        "open_leases": actual_open_leases(conn),
    }


def extend_lease(
    conn: sqlite3.Connection, lease_no: str, minutes: int, *, actor: str = "operator"
) -> dict:
    """延租接口。本部署为扫描臂，延租一律拒绝且绝不改 end_min。

    返回结构化拒绝（由路由在事务提交后转 409），以保证拒绝行为本身留审计。
    """
    lease = conn.execute(
        "SELECT * FROM leases WHERE lease_no=?", (lease_no,)
    ).fetchone()
    if lease is None:
        raise ValueError(f"租约不存在：{lease_no}")
    clock_svc.audit(
        conn,
        "extend_refused",
        actor=actor,
        window_code=lease["window_code"],
        lease_no=lease_no,
        before_end_min=lease["end_min"],
        after_end_min=lease["end_min"],
        detail=f"策略={POLICY}：到点扫描关窗，拒绝延租 {minutes} 分钟；end_min 未改动",
    )
    return {
        "ok": False,
        "error": (
            f"当前策略为「到点扫描关窗」（{POLICY}）：不支持延租，"
            "到点后由扫描或人工关窗收口"
        ),
        "policy": POLICY,
        "end_min": lease["end_min"],
    }
