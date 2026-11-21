import { useEffect, useState } from "react";
import { api } from "../api";

function fmtMin(m) {
  const h = String(Math.floor(m / 60)).padStart(2, "0");
  const mm = String(m % 60).padStart(2, "0");
  return `${h}:${mm}`;
}

const statusLabel = { draft: "待开窗", live: "占用中", done: "已结束" };

export default function Sessions() {
  const [rows, setRows] = useState([]);
  const [clock, setClock] = useState(null);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function load() {
    setErr("");
    try {
      const [ws, ck] = await Promise.all([api.windows(), api.clock()]);
      setRows(ws);
      setClock(ck);
    } catch (e) {
      setErr(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function openLease(code) {
    setMsg("");
    setErr("");
    try {
      const r = await api.openLease(code);
      const swept = (r.swept || []).length;
      setMsg(
        `已开窗，租约 ${r.lease_no}；在途租约 ${r.open_leases}` +
          (swept ? `；开窗前扫描收口 ${swept} 笔` : "")
      );
      await load();
    } catch (e) {
      setErr(e.message);
      await load();
    }
  }

  return (
    <section className="panel">
      <h2>
        批窗
        <span className="tag policy">观察钟 {clock?.label || "—"}</span>
      </h2>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="msg err">{err}</div>}
      <table>
        <thead>
          <tr>
            <th>批窗</th>
            <th>时段</th>
            <th>算力槽 / 执行器</th>
            <th>就绪</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.code} className={r.overdue_live ? "overdue" : ""}>
              <td>
                <div>{r.title}</div>
                <small>{r.code}</small>
              </td>
              <td>
                {r.day}
                <br />
                {fmtMin(r.start_min)}–{fmtMin(r.end_min)}
              </td>
              <td>
                {r.slot_code}
                <br />
                {r.executor_code}
              </td>
              <td>
                {r.readiness?.ready ? (
                  <span className="tag live">就绪</span>
                ) : (
                  <span className="tag bad">
                    {(r.readiness?.reasons || []).join("；") || "未就绪"}
                  </span>
                )}
              </td>
              <td>
                <span className={`tag ${r.status}`}>
                  {statusLabel[r.status] || r.status}
                </span>
                {r.overdue_live && (
                  <div className="tag warn">已过点待收口</div>
                )}
              </td>
              <td>
                <button
                  disabled={r.status !== "draft" || r.readiness?.past_end}
                  onClick={() => openLease(r.code)}
                  title={
                    r.readiness?.past_end ? "已过结束时刻，不能开窗" : undefined
                  }
                >
                  开窗占用
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
