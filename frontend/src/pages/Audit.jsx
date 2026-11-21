import { useEffect, useState } from "react";
import { api } from "../api";

function fmtMin(m) {
  if (m === null || m === undefined) return "—";
  const h = String(Math.floor(m / 60)).padStart(2, "0");
  const mm = String(m % 60).padStart(2, "0");
  return `${h}:${mm}`;
}

const actionLabel = {
  open_lease: "开窗",
  manual_close: "人工关窗",
  sweep_close: "到点扫描关窗",
  extend_refused: "拒绝延租",
  clock_set: "拨钟",
  window_create: "建窗",
  watermark_reconcile: "水位纠正",
};

export default function Audit() {
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    api
      .audit(200)
      .then(setRows)
      .catch((e) => setErr(e.message));
  }, []);

  if (err) return <div className="msg err">{err}</div>;

  return (
    <section className="panel">
      <h2>审计日志</h2>
      <p className="hint">
        开窗、扫描关窗、人工关窗、拒绝延租、拨钟、水位纠正均留痕；end_min
        变更须经审计，禁止静默改动。
      </p>
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>动作</th>
            <th>批窗</th>
            <th>租约</th>
            <th>end_min</th>
            <th>明细</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={6}>暂无记录</td>
            </tr>
          )}
          {rows.map((r) => (
            <tr key={r.id} className={r.action === "sweep_close" ? "overdue" : ""}>
              <td>{r.at}</td>
              <td>
                <span className="tag">{actionLabel[r.action] || r.action}</span>
              </td>
              <td>{r.window_code || "—"}</td>
              <td>{r.lease_no || "—"}</td>
              <td>
                {r.before_end_min === r.after_end_min
                  ? fmtMin(r.before_end_min)
                  : `${fmtMin(r.before_end_min)} → ${fmtMin(r.after_end_min)}`}
              </td>
              <td>{r.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
