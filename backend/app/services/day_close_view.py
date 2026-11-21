"""日切（day-close）：以「租约重放」为准的收口口径（全局唯一）。

正确性口径：当日结束后不再存在跨日 open 租约。
- 日切在同一个 BEGIN IMMEDIATE 写事务里，把 day <= 切日 的全部 open 租约
  强制收口（关租约、关批窗、水位 -1、逐笔审计），与开窗/扫描互斥，
  不会出现「日切与开窗交错留下半截释放」。
- 回执 released_count 必须能由租约表重放：回执逐笔列出释放的租约号，
  重放校验 = 列出的租约均已 closed 且无任何 day <= 切日 的 open 残留。
- 强制收口后若仍有残留 open，则抛错整体回滚，绝不落「假日切」回执。
- 水位跟释放：每笔收口由 close_lease_row 精确 -1（rowcount 守卫），
  结束后再以租约表实际占用 reconcile，并发双跑不会双减。
- 幂等：重复日切同一日，本次释放 0 笔，回执累计不变、水位不动。
"""
from __future__ import annotations

import sqlite3

from app.services import clock as clock_svc
from app.services import lease as lease_svc
from app.services.settings_util import get_json, put_json

RECEIPT_PREFIX = "day_close:"


def _receipt_key(day: str) -> str:
    return f"{RECEIPT_PREFIX}{day}"


def _cross_day_open(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    """跨日占用扫描：切日（含）之前仍 open 的租约。"""
    return conn.execute(
        "SELECT * FROM leases WHERE status='open' AND day <= ? "
        "ORDER BY day, end_min, id",
        (day,),
    ).fetchall()


def run_day_close(conn: sqlite3.Connection, day: str) -> dict:
    """对 day 执行日切：同事务强制收口跨日占用并落可重放回执。

    本函数必须在 BEGIN IMMEDIATE 写事务内调用（路由层保证）。
    """
    clock = clock_svc.read_clock(conn)
    if day > clock["day"]:
        raise ValueError(
            f"观察钟仍在 {clock['day']} {clock['label']}，不能对未到日界 {day} 日切"
        )

    # 1) 同事务强制收口全部跨日 open 租约（与开窗/扫描互斥，无半截）。
    released_now: list[str] = []
    for lease in _cross_day_open(conn, day):
        if lease_svc.close_lease_row(
            conn,
            lease,
            actor="day_close",
            action="day_close_release",
            detail=(
                f"日切 {day} 强制收口：租约日 {lease['day']} "
                f"{clock_svc.fmt_min(lease['start_min'])}–"
                f"{clock_svc.fmt_min(lease['end_min'])} 跨日占用释放"
            ),
        ):
            released_now.append(lease["lease_no"])

    # 2) 口径校验：收口后不得再有跨日 open 残留；有残留则抛错回滚，
    #    不落回执、不改日界，绝不允许「回执已释、租约仍 open」。
    residual = [r["lease_no"] for r in _cross_day_open(conn, day)]
    if residual:
        raise ValueError(
            f"日切 {day} 收口后仍有跨日 open 租约 {residual}，整体回滚"
        )

    # 3) 水位跟释放：以租约表实际占用为准 reconcile（并发双跑也不双减）。
    watermark = lease_svc.reconcile_open_watermark(conn)

    # 4) 落回执（幂等累计）：重复日切只累加 runs，不重复计释放数。
    receipt = get_json(conn, _receipt_key(day)) or {
        "day": day,
        "released": [],
        "runs": 0,
        "first_at": clock_svc.now_iso(),
    }
    receipt["released"] = receipt["released"] + released_now
    receipt["released_count"] = len(receipt["released"])
    receipt["runs"] = receipt["runs"] + 1
    receipt["last_at"] = clock_svc.now_iso()
    receipt["open_leases_after"] = watermark["actual"]
    put_json(conn, _receipt_key(day), receipt)

    clock_svc.audit(
        conn,
        "day_close",
        actor="day_close",
        detail=(
            f"日切 {day} 完成：本次释放 {len(released_now)} 笔，"
            f"累计 {receipt['released_count']} 笔，第 {receipt['runs']} 次执行，"
            f"在途水位 {watermark['actual']}"
        ),
    )

    return {
        "ok": True,
        "day": day,
        "released_now": len(released_now),
        "released_count": receipt["released_count"],
        "released": receipt["released"],
        "runs": receipt["runs"],
        "open_leases": watermark["actual"],
        "verify": verify_day_close(conn, day),
    }


def verify_day_close(conn: sqlite3.Connection, day: str) -> dict:
    """重放校验：回执释放数必须能由租约表重放出来。

    - 回执逐笔列出的租约必须全部 closed；
    - released_count 必须等于列出笔数；
    - 不得存在 day <= 切日 的 open 残留。
    """
    receipt = get_json(conn, _receipt_key(day))
    if not receipt:
        return {"day": day, "ok": False, "reason": "无日切回执", "residual_open": 0}
    listed = receipt.get("released", [])
    not_closed: list[str] = []
    for lease_no in listed:
        row = conn.execute(
            "SELECT status FROM leases WHERE lease_no=?", (lease_no,)
        ).fetchone()
        if row is None or row["status"] != "closed":
            not_closed.append(lease_no)
    residual = [r["lease_no"] for r in _cross_day_open(conn, day)]
    count_match = receipt.get("released_count") == len(listed)
    ok = count_match and not not_closed and not residual
    return {
        "day": day,
        "ok": ok,
        "released_count": receipt.get("released_count", 0),
        "listed": len(listed),
        "count_match": count_match,
        "not_closed": not_closed,
        "residual_open": len(residual),
        "residual": residual,
    }


def latest_day_close(conn: sqlite3.Connection) -> dict | None:
    """总览用：最近一次日切的回执 + 实时重放校验（不信缓存、当场重放）。"""
    rows = conn.execute(
        "SELECT key FROM settings WHERE key LIKE ?",
        (f"{RECEIPT_PREFIX}%",),
    ).fetchall()
    days = sorted(r["key"][len(RECEIPT_PREFIX):] for r in rows)
    if not days:
        return None
    day = days[-1]
    receipt = get_json(conn, _receipt_key(day)) or {}
    verify = verify_day_close(conn, day)
    return {
        "day": day,
        "released_count": receipt.get("released_count", 0),
        "runs": receipt.get("runs", 0),
        "at": receipt.get("last_at"),
        "ok": verify["ok"],
        "residual_open": verify["residual_open"],
        "not_closed": verify["not_closed"],
    }
