import { useEffect, useState } from "react";
import { api } from "../api";

function fmtMin(m) {
  const h = String(Math.floor(m / 60)).padStart(2, "0");
  const mm = String(m % 60).padStart(2, "0");
  return `${h}:${mm}`;
}

export default function Home() {
  const [d, setD] = useState(null);
  const [clock, setClock] = useState(null);
  const [policy, setPolicy] = useState(null);
  const [dow, setDow] = useState("");
  const [nowMin, setNowMin] = useState(540);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function load() {
    setErr("");
    try {
      const [dash, ck, pol] = await Promise.all([
        api.dashboard(),
        api.clock(),
        api.policy(),
      ]);
      setD(dash);
      setClock(ck);
      setPolicy(pol);
      setDow(ck.day);
      setNowMin(ck.now_min);
    } catch (e) {
      setErr(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function applyClock(e) {
    e.preventDefault();
    setMsg("");
    setErr("");
    try {
      const r = await api.setClock(dow, Number(nowMin));
      setMsg(
        `观察钟 → ${r.day} ${r.label}；扫描收口 ${r.swept.length} 笔` +
          (r.swept.length ? `（${r.swept.map((s) => s.lease_no).join("、")}）` : "")
      );
      await load();
    } catch (e2) {
      setErr(e2.message);
    }
  }

  async function doSweep() {
    setMsg("");
    setErr("");
    try {
      const r = await api.sweep();
      setMsg(`扫描完成：收口 ${r.swept.length} 笔；在途租约 ${r.open_leases}`);
      await load();
    } catch (e2) {
      setErr(e2.message);
    }
  }

  if (err && !d) return <div className="msg err">{err}</div>;
  if (!d) return <div className="panel">加载中…</div>;

  return (
    <>
      <section className="panel">
        <h2>
          今日水位 · {d.fixture_day}
          <span className="tag policy">
            口径：{policy?.policy === "sweep" ? "到点扫描关窗" : policy?.policy}
          </span>
        </h2>
        <div className="stats">
          <div className="stat">
            在途租约
            <b>{d.open_leases}</b>
          </div>
          <div className="stat">
            冲突计数
            <b>{d.conflict_count}</b>
          </div>
          <div className="stat">
            占用中
            <b>{d.live_sessions}</b>
          </div>
          <div className="stat">
            待开窗
            <b>{d.draft_sessions}</b>
          </div>
        </div>
        <p className="hint">
          已过 end_min 仍占用的租约：
          <b className={d.overdue_live ? "warn" : ""}>{d.overdue_live}</b>
          。到点由扫描或人工关窗收口，不会静默续窗。
        </p>
      </section>

      <section className="panel">
        <h2>观察钟 · 拨钟对照</h2>
        <form className="row-actions" onSubmit={applyClock}>
          <input
            type="date"
            value={dow}
            onChange={(e) => setDow(e.target.value)}
          />
          <input
            type="time"
            value={fmtMin(nowMin)}
            onChange={(e) => {
              const [h, m] = e.target.value.split(":").map(Number);
              setNowMin(h * 60 + m);
            }}
          />
          <button type="submit">拨钟并扫描</button>
          <button type="button" className="secondary" onClick={doSweep}>
            立即扫描
          </button>
        </form>
        <p className="hint">
          当前观察钟 {clock?.day} {clock?.label}；拨过某窗 end_min 后，该窗租约由扫描收口，
          同槽下窗随即可开。
        </p>
        {msg && <div className="msg">{msg}</div>}
        {err && <div className="msg err">{err}</div>}
      </section>
    </>
  );
}
