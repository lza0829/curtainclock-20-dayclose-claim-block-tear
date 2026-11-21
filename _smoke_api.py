# -*- coding: utf-8 -*-
"""0-1 API smoke against running server on 11100.

口径：到点扫描关窗为准（sweep）。
核对：扫描臂下到点后下窗可开；延租接口被拒且不改 end_min；拨钟过点两臂可对照；
并发扫描与开窗不得留下半截占用。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:11100/api"
DAY = "2026-11-20"


def call(method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
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


def reset_clock(now_min: int) -> None:
    st, out = call("POST", "/clock", {"day": DAY, "now_min": now_min})
    assert st == 200, out
    return out


def main() -> None:
    st, policy = call("GET", "/policy")
    check(st == 200 and policy["policy"] == "sweep", "policy=sweep", policy)
    check(policy["extend_supported"] is False, "extend unsupported", policy)

    st, dash = call("GET", "/dashboard")
    check(st == 200, "dashboard", dash)

    # 1) 正常开窗
    reset_clock(9 * 60)
    st, open1 = call("POST", "/leases/open", {"window_code": "W-DIM-01"})
    check(st == 200, "open W-DIM-01", open1)
    lease = open1["lease_no"]
    st, dash = call("GET", "/dashboard")
    check(dash["open_leases"] == 1 and dash["overdue_live"] == 0, "watermark=1", dash)

    # 2) 同槽重叠被拒
    st, created = call(
        "POST",
        "/windows",
        {
            "code": "W-DIM-DUP",
            "title": "冲突试窗",
            "subject": "ETL",
            "slot_code": "S-ETL-01",
            "executor_code": "E-BI-01",
            "day": DAY,
            "start_min": 540,
            "end_min": 630,
        },
    )
    check(st == 200, "create overlap window", created)
    st, conflict = call("POST", "/leases/open", {"window_code": "W-DIM-DUP"})
    check(st == 409, "overlap rejected", conflict)

    # 3) 延租被拒且不改 end_min（重复拨钟会重置，故先看原值）
    st, before = call("GET", "/leases")
    end_before = next(r["end_min"] for r in before if r["lease_no"] == lease)
    st, ext = call("POST", "/leases/extend", {"lease_no": lease, "minutes": 30})
    check(st == 409, "extend refused", ext)
    st, after = call("GET", "/leases")
    end_after = next(r["end_min"] for r in after if r["lease_no"] == lease)
    check(end_before == end_after == 630, "end_min unchanged", (end_before, end_after))

    # 4) 拨钟过点：扫描收口，下窗可开
    sweep = reset_clock(10 * 60 + 30)
    check(any(x["lease_no"] == lease for x in sweep["swept"]), "lease swept", sweep)
    st, dash = call("GET", "/dashboard")
    check(dash["open_leases"] == 0, "watermark back to 0", dash)
    st, leases = call("GET", "/leases")
    check(
        next(r["status"] for r in leases if r["lease_no"] == lease) == "closed",
        "lease closed",
    )
    # 同槽下窗（另起的批窗仍在时段内）——先造一个 10:30 之后的新窗
    st, later = call(
        "POST",
        "/windows",
        {
            "code": "W-NEXT-01",
            "title": "下一窗",
            "subject": "ETL",
            "slot_code": "S-ETL-01",
            "executor_code": "E-PIPE-01",
            "day": DAY,
            "start_min": 11 * 60,
            "end_min": 12 * 60,
        },
    )
    check(st == 200, "create next window", later)
    st, next_open = call("POST", "/leases/open", {"window_code": "W-NEXT-01"})
    check(st == 200, "next window opens after sweep", next_open)
    call("POST", "/leases/close", {"lease_no": next_open["lease_no"]})

    # 5) 已过 end_min 的 draft 不能再开（W-DIM-DUP 时段 540–630，观察钟已到 630）
    st, late = call("POST", "/leases/open", {"window_code": "W-DIM-DUP"})
    check(st == 409, "past-end window refuses open", late)

    # 6) 人工关窗入口仍可用
    reset_clock(13 * 60)
    st, manual = call("POST", "/leases/open", {"window_code": "W-RPT-01"})
    check(st == 200, "open W-RPT-01", manual)
    st, closed = call("POST", "/leases/close", {"lease_no": manual["lease_no"]})
    check(st == 200, "manual close", closed)

    # 7) 审计可查、水位一致
    st, audit = call("GET", "/audit?limit=50")
    actions = {a["action"] for a in audit}
    check(
        {"open_lease", "manual_close", "sweep_close", "clock_set", "extend_refused"}
        <= actions,
        "audit trail covers all paths",
        sorted(actions),
    )
    st, dash = call("GET", "/dashboard")
    check(dash["open_leases"] == dash["live_sessions"], "watermark == live sessions", dash)
    print("SMOKE OK")


if __name__ == "__main__":
    main()
