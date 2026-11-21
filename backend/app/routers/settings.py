from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import POLICY
from app.db import connect, transaction
from app.services import clock as clock_svc
from app.services import lease as lease_svc

router = APIRouter(tags=["settings"])


class ModeBody(BaseModel):
    conflict_mode: str


class ClockBody(BaseModel):
    day: str
    now_min: int


@router.get("/settings")
def get_settings():
    conn = connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


@router.post("/settings/conflict-mode")
def set_mode(body: ModeBody):
    if body.conflict_mode not in ("reject", "standby"):
        return {"ok": False, "error": "仅支持 reject / standby"}
    with transaction() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('conflict_mode', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (body.conflict_mode,),
        )
    return {"ok": True, "conflict_mode": body.conflict_mode}


@router.get("/clock")
def get_clock():
    conn = connect()
    try:
        clock = clock_svc.read_clock(conn)
        return {**clock, "policy": POLICY}
    finally:
        conn.close()


@router.post("/clock")
def set_clock(body: ClockBody):
    """拨钟：模拟推进观察钟；写入后立即按新时刻扫描一次，两臂行为可对照。"""
    if not body.day or len(body.day) < 10:
        raise HTTPException(400, "day 需为 YYYY-MM-DD")
    try:
        with transaction() as conn:
            before = clock_svc.read_clock(conn)
            clock_svc.write_clock(conn, body.day, body.now_min)
            clock = clock_svc.read_clock(conn)
            swept = lease_svc.sweep_expired(conn, clock, actor="clock_advance")
            lease_svc.reconcile_open_watermark(conn)
            clock_svc.audit(
                conn,
                "clock_set",
                actor="operator",
                detail=(
                    f"观察钟 {before['day']} {before['label']} → "
                    f"{clock['day']} {clock['label']}，扫描收口 {len(swept)} 笔"
                ),
            )
            return {**clock, "policy": POLICY, "swept": swept}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/sweep")
def force_sweep():
    """手动触发一次到点扫描（幂等）。"""
    with transaction() as conn:
        clock = clock_svc.read_clock(conn)
        swept = lease_svc.sweep_expired(conn, clock, actor="manual_sweep")
        lease_svc.reconcile_open_watermark(conn)
        return {
            "clock": clock,
            "swept": swept,
            "open_leases": lease_svc.actual_open_leases(conn),
        }
