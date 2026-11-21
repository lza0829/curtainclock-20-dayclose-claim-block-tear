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

浏览器打开 http://127.0.0.1:6100 ：开窗占用 → 冲突拒绝 → 拨钟过点 → 扫描收口 → 下窗可开。

## Docker

```powershell
docker compose up -d --build
```
