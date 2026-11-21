# -*- coding: utf-8 -*-
"""日切口径冒烟（需服务已启动且为全新库）。

口径：以租约重放为准——日切回执释放数必须能由租约表重放；
当日结束后无跨日 open 租约；水位跟释放；次日预检跟租约；并发双跑幂等。

夹具：
1) 回执已释 vs 租约仍 open —— 日切后租约必须全部 closed，回执可重放；
2) 租约闭 vs 次日仍挡 —— 次日新窗预检就绪、可开，旧日租约不得复活；
3) 日切与开窗交错 —— 任意交错终态一致，无半截释放；
4) 并发双跑日切 —— 释放数不双计、水位不双减、回执幂等。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:11100/api"
DAY = "2026-11-20"
DAY2 = "2026-11-21"
DAY3 = "2026-11-22"


def call(method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"detail": raw}
        return e.code, payload


def check(cond: bool, label: str, payload=None) -> None:
    assert cond, f"{label} -> {payload}"
    print("ok", label, "" if payload is None else payload)


def set_clock(day: str, now_min: int) -> dict:
    st, out = call("POST", "/clock", {"day": day, "now_min": now_min})
    assert st == 200, out
    return out


def make_window(code: str, day: str, slot: str, exe: str, start: int, end: int):
    st, out = call(
        "POST",
        "/windows",
        {
            "code": code,
            "title": code,
            "subject": "ETL",
            "slot_code": slot,
            "executor_code": exe,
            "day": day,
            "start_min": start,
            "end_min": end,
        },
    )
    assert st == 200, out
    return out


def open_leases_now() -> list[dict]:
    st, rows = call("GET", "/leases")
    assert st == 200, rows
    return [r for r in rows if r["status"] == "open"]


def main() -> None:
    # ---------- 夹具 1：回执已释 vs 租约仍 open ----------
    set_clock(DAY, 9 * 60)
    st, o1 = call("POST", "/leases/open", {"window_code": "W-DIM-01"})
    check(st == 200, "f1 open W-DIM-01", o1)
    st, o2 = call("POST", "/leases/open", {"window_code": "W-FACT-01"})
    check(st == 200, "f1 open W-FACT-01", o2)
    st, dash = call("GET", "/dashboard")
    check(dash["open_leases"] == 2, "f1 watermark=2 before close", dash)

    st, dc = call("POST", "/day-close", {"day": DAY})
    check(st == 200 and dc["ok"], "f1 day-close accepted", dc)
    check(dc["released_now"] == 2 and dc["released_count"] == 2, "f1 released=2", dc)
    check(dc["verify"]["ok"] is True, "f1 receipt replays clean", dc["verify"])

    st, leases = call("GET", f"/leases?day={DAY}")
    statuses = {r["lease_no"]: r["status"] for r in leases}
    check(
        statuses.get(o1["lease_no"]) == "closed"
        and statuses.get(o2["lease_no"]) == "closed",
        "f1 leases actually closed (no 回执已释租约仍open)",
        statuses,
    )
    st, dash = call("GET", "/dashboard")
    check(dash["open_leases"] == 0, "f1 watermark follows release", dash)
    check(
        dash["day_close"]["ok"] is True and dash["day_close"]["residual_open"] == 0,
        "f1 dashboard day_close replays clean",
        dash["day_close"],
    )
    st, v = call("GET", f"/day-close/{DAY}")
    check(st == 200 and v["ok"] and v["residual_open"] == 0, "f1 replay endpoint", v)

    # 幂等重跑：本次释放 0，累计不变，水位不动
    st, dc2 = call("POST", "/day-close", {"day": DAY})
    check(
        st == 200 and dc2["released_now"] == 0 and dc2["released_count"] == 2
        and dc2["runs"] == 2 and dc2["open_leases"] == 0,
        "f1 idempotent re-run",
        dc2,
    )

    # 批窗日界：已切日的 draft 不得再开
    st, late = call("POST", "/leases/open", {"window_code": "W-RPT-01"})
    check(st == 409 and "日切" in str(late), "f1 closed-day window refused", late)
    st, ws = call("GET", "/windows")
    rpt = next(w for w in ws if w["code"] == "W-RPT-01")
    check(
        rpt["readiness"]["day_closed"] is True
        and any("日切" in r for r in rpt["readiness"]["reasons"]),
        "f1 readiness shows day boundary",
        rpt["readiness"],
    )

    # ---------- 夹具 2：租约闭 vs 次日仍挡 ----------
    set_clock(DAY2, 9 * 60)
    make_window("W-NXT-A", DAY2, "S-ETL-01", "E-PIPE-01", 9 * 60, 10 * 60)
    st, ready = call("GET", "/windows/W-NXT-A/ready")
    check(st == 200 and ready["ready"] is True, "f2 next-day precheck ready", ready)
    st, o3 = call("POST", "/leases/open", {"window_code": "W-NXT-A"})
    check(st == 200, "f2 next-day window opens", o3)
    st, dash = call("GET", "/dashboard")
    check(dash["open_leases"] == 1, "f2 watermark counts only new lease", dash)
    st, leases = call("GET", f"/leases?day={DAY}")
    check(
        all(r["status"] == "closed" for r in leases),
        "f2 old-day leases stay closed",
        leases,
    )
    call("POST", "/leases/close", {"lease_no": o3["lease_no"]})

    # ---------- 夹具 3：日切与开窗交错，不得半截 ----------
    set_clock(DAY2, 9 * 60 + 30)
    make_window("W-X1", DAY2, "S-ETL-01", "E-PIPE-01", 10 * 60, 11 * 60)
    make_window("W-X2", DAY2, "S-ETL-02", "E-PIPE-02", 10 * 60, 11 * 60)
    st, o4 = call("POST", "/leases/open", {"window_code": "W-X1"})
    check(st == 200, "f3 open W-X1", o4)

    results: dict = {}

    def t_close():
        results["close"] = call("POST", "/day-close", {"day": DAY2})

    def t_open():
        results["open"] = call("POST", "/leases/open", {"window_code": "W-X2"})

    threads = [threading.Thread(target=t_close), threading.Thread(target=t_open)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    st_close, close_out = results["close"]
    st_open, open_out = results["open"]
    check(st_close == 200, "f3 day-close ok", close_out)
    check(
        st_open == 200 or (st_open == 409 and "日切" in str(open_out)),
        "f3 interleaved open either landed-then-closed or refused by boundary",
        open_out,
    )
    # 终态互证：无 DAY2 及之前的 open 残留；每笔 closed 租约的批窗均 done；水位=实际
    leftovers = [r for r in open_leases_now() if r["day"] <= DAY2]
    check(not leftovers, "f3 no half-released cross-day leases", leftovers)
    st, leases = call("GET", "/leases")
    st, ws = call("GET", "/windows")
    wstatus = {w["code"]: w["status"] for w in ws}
    bad = [
        r["lease_no"]
        for r in leases
        if r["status"] == "closed" and wstatus.get(r["window_code"]) != "done"
    ]
    check(not bad, "f3 closed leases have done windows (no half state)", bad)
    st, dash = call("GET", "/dashboard")
    check(
        dash["open_leases"] == len(open_leases_now()) == 0,
        "f3 watermark == actual == 0",
        dash,
    )
    st, v = call("GET", f"/day-close/{DAY2}")
    check(v["ok"] and v["released_count"] >= 1, "f3 day-close receipt replays", v)

    # ---------- 夹具 4：并发双跑日切，不得双计释放/双减水位 ----------
    set_clock(DAY3, 9 * 60)
    make_window("W-Y1", DAY3, "S-ETL-01", "E-PIPE-01", 9 * 60, 10 * 60)
    make_window("W-Y2", DAY3, "S-BI-01", "E-BI-01", 9 * 60, 10 * 60)
    st, y1 = call("POST", "/leases/open", {"window_code": "W-Y1"})
    check(st == 200, "f4 open W-Y1", y1)
    st, y2 = call("POST", "/leases/open", {"window_code": "W-Y2"})
    check(st == 200, "f4 open W-Y2", y2)
    st, dash = call("GET", "/dashboard")
    check(dash["open_leases"] == 2, "f4 watermark=2 before close", dash)

    double: list = []

    def t_dc():
        double.append(call("POST", "/day-close", {"day": DAY3}))

    threads = [threading.Thread(target=t_dc), threading.Thread(target=t_dc)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check(all(st == 200 for st, _ in double), "f4 both runs accepted", double)
    released_now_sum = sum(out["released_now"] for _, out in double)
    check(
        released_now_sum == 2
        and all(out["released_count"] == 2 for _, out in double)
        and max(out["runs"] for _, out in double) == 2,
        "f4 release counted once across double run",
        double,
    )
    st, dash = call("GET", "/dashboard")
    check(
        dash["open_leases"] == 0 and len(open_leases_now()) == 0,
        "f4 watermark not double-decremented",
        dash,
    )
    st, v = call("GET", f"/day-close/{DAY3}")
    check(
        v["ok"] and v["released_count"] == 2 and v["listed"] == 2
        and v["residual_open"] == 0,
        "f4 receipt replays to exactly the two leases",
        v,
    )
    st, audit = call("GET", "/audit?limit=200")
    n_rel = sum(
        1
        for a in audit
        if a["action"] == "day_close_release" and f"日切 {DAY3}" in a["detail"]
    )
    check(n_rel == 2, "f4 exactly two day_close_release audits", n_rel)

    print("DAY-CLOSE SMOKE OK")


if __name__ == "__main__":
    main()
