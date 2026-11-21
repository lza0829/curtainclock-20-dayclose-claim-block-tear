# Curtainclock（幕窗钟）

骨架：**批窗定义 → 算力槽/执行器资源依赖就绪 → 开窗租约占用 → 到点扫描关窗释放 → 冲突与水位互证**

- 前端：http://127.0.0.1:6100 （React + Vite）
- API：http://127.0.0.1:11100

## 到点收口口径（全局唯一）

本部署只贯彻 **「以到点扫描关窗为准」** 一种架构（`backend/app/config.py` 中 `POLICY = "sweep"`）：

- 观察钟推进到批窗 `end_min`（含）后，扫描事务把该窗 `open` 租约置 `closed`、
  批窗置 `done`、`open_leases` 水位 -1，同槽下窗随即满足开窗条件。
- 扫描与开窗都在 `BEGIN IMMEDIATE` 写事务里执行，二者**互斥**；
  开窗动作本身也会先做一次到点收口，避免「扫到一半又被新开窗双占」。
- 提供 `POST /api/leases/extend` 延租入口，但在本口径下**一律 409 拒绝且不改
  `end_min`**，并写审计；不会出现「未延租却继续占用」而无解释。
- 人工关窗入口保留；任何 `end_min` 变更都必须留审计（`audit_log`），禁止静默改动。

已过 `end_min` 但仍 `live` 的租约（扫描尚未跑到）不再计入占用判定，
并在总览/批窗/租约页以「已过点待收口」标注，给出可解释状态。

## 日切口径（全局唯一：以租约重放为准）

`POST /api/day-close {"day":"YYYY-MM-DD"}` 对当日（含）之前的跨日占用做日切。
两种候选架构（租约重放 / 释放快照）中，本部署只贯彻 **「以租约重放为准」**，
因为占用判定、预检、水位 reconcile 本就以租约表为唯一事实源：

- 日切在同一个 `BEGIN IMMEDIATE` 写事务里把 `day <= 切日` 的全部 `open` 租约
  强制收口（关租约、批窗置 `done`、水位 -1、逐笔 `day_close_release` 审计），
  与开窗/扫描互斥，不会交错留下半截释放。
- 回执 `released_count` 必须能由租约表重放：回执逐笔列出释放的租约号，
  `GET /api/day-close/{day}` 当场重放校验（所列均已 `closed` 且跨日 `open` 残留为 0）。
- 强制收口后仍有残留则抛错整体回滚，绝不落「回执已释、租约仍 open」的假日切。
- 水位跟释放：收口走唯一的 `close_lease_row`（`status='open'` 守卫 + rowcount 判定），
  结束再以租约表实际占用 reconcile；并发双跑日切幂等，不双计释放、不双减水位。
- 批窗日界：已切日的批窗不得再开窗（开窗 409、预检给出原因），
  次日新窗预检只跟租约表，旧日占用不会挡次日。
- 总览的日切状态每次请求当场重放校验，不用回执缓存覆盖真实水位；
  租约页支持 `?day=` 按日筛选，可与回执逐日互证。

## 观察钟与拨钟

`GET/POST /api/clock`（`{"day":"2026-11-20","now_min":630}`）模拟拨钟；
写入后立即按新时刻扫描一次，便于两条臂行为对照（本部署只启用扫描臂）。
`POST /api/sweep` 可手动触发一次幂等扫描。

## 本地冒烟

```powershell
cd D:\Asolo11\claude-workspace\curtainclock\source
py -V:Astral/CPython3.11.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
$env:PYTHONPATH = "$PWD\backend"
Start-Process .\.venv\Scripts\uvicorn.exe -ArgumentList 'app.main:app','--host','127.0.0.1','--port','11100' -WorkingDirectory "$PWD\backend"
cd frontend
npm install
npm run dev
```

然后运行 `.venv\Scripts\python.exe _smoke_api.py`（服务需已启动）。
日切口径四个夹具（回执已释 vs 租约仍 open、租约闭 vs 次日仍挡、交错半截、
并发双跑）由 `_smoke_day_close.py` 核对（需全新库，先删 `data/curtainclock.db`）。

浏览器打开 http://127.0.0.1:6100 ：开窗占用 → 冲突拒绝 → 拨钟过点 → 扫描收口 → 下窗可开。

## Docker

```powershell
docker compose up -d --build
```
