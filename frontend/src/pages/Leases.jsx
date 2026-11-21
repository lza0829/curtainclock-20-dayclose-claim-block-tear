import { useEffect, useState } from "react";
import { api } from "../api";

function fmtMin(m) {
  const h = String(Math.floor(m / 60)).padStart(2, "0");
  const mm = String(m % 60).padStart(2, "0");
  return `${h}:${mm}`;
}

export default function Leases() {
  const [rows, setRows] = useState([]);
  const [days, setDays] = useState([]);
  const [policy, setPolicy] = useState(null);
  const [day, setDay] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function load(dayFilter = day) {
    const [ls, all, pol] = await Promise.all([
      api.leases(dayFilter || undefined),
      api.leases(),
      api.policy(),
    ]);
    setRows(ls);
    setDays([...new Set(all.map((r) => r.day))].sort());
    setPolicy(pol);
  }

  useEffect(() => {
    load().catch((e) => setErr(e.message));
  }, []);

  async function filterDay(d) {
    setDay(d);
    try {
      await load(d);
    } catch (e) {
      setErr(e.message);
    }
  }

  async function closeOne(lease_no) {
    setMsg("");
    setErr("");
    try {
      const r = await api.closeLease(lease_no);
      setMsg(`已关窗释放 ${r.lease_no}；在途租约 ${r.open_leases}`);
      await load();
    } catch (e) {
      setErr(e.message);
    }
  }

  async function extendOne(lease_no) {
    setMsg("");
    setErr("");
    try {
      const r = await api.extendLease(lease_no, 30);
      setMsg(`已延租 ${r.lease_no} 至 ${fmtMin(r.end_min)}`);
      await load();
    } catch (e) {
      setErr(`延租被拒：${e.message}`);
    }
  }

  return (
    <section className="panel">
      <h2>
        租约
        <span className="tag policy">
          {policy?.extend_supported ? "延租续窗" : "到点扫描关窗"}
        </span>
      </h2>
      <div className="row-actions" style={{ marginBottom: 8 }}>
        <label>
          按日筛选（与日切回执互证）：
          <select value={day} onChange={(e) => filterDay(e.target.value)}>
            <option value="">全部</option>
            {days.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        {day && (
          <span className="hint">
            若 {day} 已日切，本日租约应全部「已释放」；仍有「占用中」即口径撕裂。
          </span>
        )}
      </div>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="msg err">{err}</div>}
      <table>
        <thead>
          <tr>
            <th>租约号</th>
            <th>批窗</th>
            <th>资源</th>
            <th>时段</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.lease_no} className={r.overdue_live ? "overdue" : ""}>
              <td>{r.lease_no}</td>
              <td>{r.window_code}</td>
              <td>
                {r.slot_code} / {r.executor_code}
              </td>
              <td>
                {r.day} {fmtMin(r.start_min)}–{fmtMin(r.end_min)}
              </td>
              <td>
                {r.status === "open" ? "占用中" : "已释放"}
                {r.overdue_live && <div className="tag warn">已过点待收口</div>}
              </td>
              <td>
                <div className="row-actions">
                  <button
                    className="secondary"
                    disabled={r.status !== "open"}
                    onClick={() => closeOne(r.lease_no)}
                  >
                    关窗释放
                  </button>
                  <button
                    className="secondary"
                    disabled={r.status !== "open"}
                    onClick={() => extendOne(r.lease_no)}
                    title={
                      policy?.extend_supported
                        ? "延租续窗"
                        : "当前口径为到点扫描关窗，延租会被拒绝"
                    }
                  >
                    延租 +30m
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
