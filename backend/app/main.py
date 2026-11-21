from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import PROJECT_NAME, SWEEP_INTERVAL_SECONDS
from app.db import transaction
from app.routers import dashboard, leases, resources, settings, windows
from app.seed import seed_if_empty
from app.services import clock as clock_svc
from app.services import lease as lease_svc

log = logging.getLogger("curtainclock.sweep")

stop_event = threading.Event()


def sweep_loop() -> None:
    """后台到点扫描：定时把已过 end_min 的 open 租约收口。

    与开窗共用 BEGIN IMMEDIATE，扫描与开窗互斥；扫描中断不会留下半截占用。
    """
    while not stop_event.wait(SWEEP_INTERVAL_SECONDS):
        try:
            with transaction() as conn:
                clock = clock_svc.read_clock(conn)
                closed = lease_svc.sweep_expired(conn, clock, actor="background_sweep")
            for item in closed:
                log.info("swept lease %s (%s)", item["lease_no"], item["reason"])
        except Exception:  # 扫描失败不应杀掉后台线程
            log.exception("background sweep failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    sweeper: threading.Thread | None = None
    if SWEEP_INTERVAL_SECONDS > 0:
        sweeper = threading.Thread(target=sweep_loop, name="lease-sweeper", daemon=True)
        sweeper.start()
    try:
        yield
    finally:
        stop_event.set()
        if sweeper is not None:
            sweeper.join(timeout=2)


seed_if_empty()

app = FastAPI(title=PROJECT_NAME, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(dashboard.router, prefix="/api")
app.include_router(resources.router, prefix="/api")
app.include_router(windows.router, prefix="/api")
app.include_router(leases.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
