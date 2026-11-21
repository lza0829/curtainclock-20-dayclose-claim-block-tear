from __future__ import annotations

import os
from pathlib import Path

PROJECT_NAME = "幕窗钟"

# 「到点后占用如何收口」的全局口径：本部署只贯彻一种架构。
# sweep = 到点扫描关窗为准（延长 end_min 的延租接口一律拒绝）。
POLICY = "sweep"

_DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "curtainclock.db"
DB_PATH = Path(os.environ.get("CURTAINCLOCK_DB", str(_DEFAULT_DB)))

# 后台到点扫描间隔（秒）；<= 0 表示关闭后台扫描，仅由接口懒触发。
SWEEP_INTERVAL_SECONDS = float(os.environ.get("CURTAINCLOCK_SWEEP_SECONDS", "5"))
