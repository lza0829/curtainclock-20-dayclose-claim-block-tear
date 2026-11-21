from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import transaction
from app.services import clock as clock_svc
from app.services import lease as lease_svc
from app.services.ready import readiness

router = APIRouter(tags=["windows"])


class SessionIn(BaseModel):
    code: str = Field(min_length=2)
    title: str
    subject: str
    slot_code: str
    executor_code: str
    day: str
    start_min: int
    end_min: int


def _annotate(conn, row, clock, expired_leases: set[str]) -> dict:
    item = dict(row)
    item["readiness"] = readiness(conn, row, clock=clock)
    # 明确解释「已过 end_min 仍 live」：不是新占用，等扫描/人工关窗收口。
    item["overdue_live"] = row["code"] in expired_leases
    return item


@router.get("/windows")
def list_windows():
    with transaction() as conn:
        clock = clock_svc.read_clock(conn)
        lease_svc.sweep_expired(conn, clock, actor="read:sweep")
        rows = conn.execute(
            "SELECT * FROM windows ORDER BY day, start_min, code"
        ).fetchall()
        expired = {
            r["window_code"]
            for r in conn.execute(
                "SELECT * FROM leases WHERE status='open' AND "
                "(day < ? OR (day = ? AND end_min <= ?))",
                (clock["day"], clock["day"], clock["now_min"]),
            ).fetchall()
        }
        return [_annotate(conn, r, clock, expired) for r in rows]


@router.post("/windows")
def create_session(body: SessionIn):
    if body.end_min <= body.start_min:
        raise HTTPException(400, "结束时刻必须晚于开始时刻")
    try:
        with transaction() as conn:
            conn.execute(
                "INSERT INTO windows(code, title, subject, slot_code, executor_code, day, start_min, end_min, status) "
                "VALUES(?,?,?,?,?,?,?,?, 'draft')",
                (
                    body.code,
                    body.title,
                    body.subject,
                    body.slot_code,
                    body.executor_code,
                    body.day,
                    body.start_min,
                    body.end_min,
                ),
            )
            clock = clock_svc.read_clock(conn)
            row = conn.execute(
                "SELECT * FROM windows WHERE code=?", (body.code,)
            ).fetchone()
            clock_svc.audit(
                conn,
                "window_create",
                actor="operator",
                window_code=body.code,
                detail=f"批窗 {body.day} "
                f"{clock_svc.fmt_min(body.start_min)}–{clock_svc.fmt_min(body.end_min)}",
            )
            return {**dict(row), "readiness": readiness(conn, row, clock=clock)}
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@router.get("/windows/{code}/ready")
def session_ready(code: str):
    with transaction() as conn:
        clock = clock_svc.read_clock(conn)
        lease_svc.sweep_expired(conn, clock, actor="read:sweep")
        row = conn.execute(
            "SELECT * FROM windows WHERE code=?", (code,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "批窗不存在")
        return readiness(conn, row, clock=clock)
