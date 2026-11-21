from __future__ import annotations
from app.services import day_close_view

from fastapi import APIRouter

from app.config import POLICY
from app.db import transaction
from app.services import clock as clock_svc
from app.services import lease as lease_svc

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard():
    with transaction() as conn:
        clock = clock_svc.read_clock(conn)
        swept = lease_svc.sweep_expired(conn, clock, actor="read:sweep")
        lease_svc.reconcile_open_watermark(conn)

        live_n = conn.execute(
            "SELECT COUNT(*) c FROM windows WHERE status='live'"
        ).fetchone()["c"]
        draft_n = conn.execute(
            "SELECT COUNT(*) c FROM windows WHERE status='draft'"
        ).fetchone()["c"]
        done_n = conn.execute(
            "SELECT COUNT(*) c FROM windows WHERE status='done'"
        ).fetchone()["c"]
        actual_open = lease_svc.actual_open_leases(conn)
        conflicts = conn.execute(
            "SELECT value FROM watermarks WHERE key='conflict_count'"
        ).fetchone()
        overdue = conn.execute(
            "SELECT COUNT(*) c FROM leases WHERE status='open' AND "
            "(day < ? OR (day = ? AND end_min <= ?))",
            (clock["day"], clock["day"], clock["now_min"]),
        ).fetchone()["c"]
        base = {
            "policy": POLICY,
            "fixture_day": clock["day"],
            "clock_day": clock["day"],
            "clock_min": clock["now_min"],
            "clock_label": clock["label"],
            "open_leases": actual_open,
            "overdue_live": overdue,
            "conflict_count": int(conflicts["value"]) if conflicts else 0,
            "live_sessions": live_n,
            "draft_sessions": draft_n,
            "done_sessions": done_n,
            "swept": swept,
        }
        # 日切状态当场由租约表重放校验，绝不用回执缓存覆盖真实水位。
        base["day_close"] = day_close_view.latest_day_close(conn)
        return base

