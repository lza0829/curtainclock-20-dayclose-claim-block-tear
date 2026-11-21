from __future__ import annotations
from app.services import day_close_view

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import POLICY
from app.db import connect, transaction
from app.services import clock as clock_svc
from app.services import lease as lease_svc

router = APIRouter(tags=["leases"])


class OpenBody(BaseModel):
    window_code: str


class CloseBody(BaseModel):
    lease_no: str


class ExtendBody(BaseModel):
    lease_no: str
    minutes: int


@router.get("/leases")
def list_leases(day: str | None = None):
    """租约列表；可按日筛选（?day=YYYY-MM-DD），用于与日切回执逐日互证。"""
    with transaction() as conn:
        clock = clock_svc.read_clock(conn)
        lease_svc.reconcile_open_watermark(conn)
        if day:
            rows = conn.execute(
                "SELECT * FROM leases WHERE day=? ORDER BY id DESC", (day,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM leases ORDER BY id DESC").fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["overdue_live"] = (
                r["status"] == "open"
                and clock_svc.lease_expired(r, clock["day"], clock["now_min"])
            )
            out.append(item)
        return out


@router.get("/conflicts")
def list_conflicts():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM conflicts ORDER BY id DESC LIMIT 100"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/audit")
def list_audit(limit: int = 100):
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/leases/open")
def open_lease(body: OpenBody):
    try:
        with transaction() as conn:
            result = lease_svc.open_lease(conn, body.window_code)
        if result.get("ok") is False:
            raise HTTPException(409, result.get("error") or "开窗冲突")
        return result
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/leases/close")
def close_lease(body: CloseBody):
    try:
        with transaction() as conn:
            return lease_svc.close_lease(conn, body.lease_no)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/leases/extend")
def extend_lease(body: ExtendBody):
    """延租入口在扫描臂下始终拒绝（409），且不改任何 end_min。"""
    try:
        with transaction() as conn:
            result = lease_svc.extend_lease(conn, body.lease_no, body.minutes)
        if result.get("ok") is False:
            raise HTTPException(409, result.get("error") or "不支持延租")
        return result
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/policy")
def get_policy():
    return {
        "policy": POLICY,
        "expiry": "到点扫描关窗为准",
        "extend_supported": False,
        "explain": (
            "已过 end_min 的 open 租约由到点扫描或人工关窗收口；"
            "扫描与开窗互斥；提供延租入口但一律拒绝且不改 end_min。"
        ),
    }


class DayCloseBody(BaseModel):
    day: str


@router.post("/day-close")
def day_close(body: DayCloseBody):
    """日切入口：同事务强制收口跨日占用，回执可由租约表重放（幂等）。"""
    try:
        with transaction() as conn:
            return day_close_view.run_day_close(conn, body.day)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/day-close/{day}")
def day_close_status(day: str):
    """日切回执 + 当场重放校验：释放数必须能由租约表重放为 0 残留。"""
    with transaction() as conn:
        return day_close_view.verify_day_close(conn, day)
