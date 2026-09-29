"""统一原始数据仓（REQ-011）。

「买回来的」远程原始响应落在**一个**结构化仓里（SQLite 单文件 + append-only 的
`manifest.jsonl`），面向人的产物（`data_pack_market.md`）由仓**离线**重建，
不再一边当报表一边当数据源。

- 需求（要什么、做到什么程度）：`docs/requirements/REQ-011-unified-data-acquisition.md`
- 设计（怎么做、取舍）：`docs/DATA_LAYER_PLAN.md`
- 与 `scripts/webui/datastore/` 的边界：那边是**算出来的**派生数据（可失效），
  这边是**买回来的**资产（不过期、不主动删、可整体拷走），两者不得互相接线。

零新增依赖：只用标准库与仓库既有依赖。
"""

from __future__ import annotations

import sys
from pathlib import Path

__version__ = "0.1.0"

# 仓的 schema 版本。写入 `meta` 表，`--check` 打印；将来加字段要写迁移。
SCHEMA_VERSION = "1.0"

# 结果枚举（AC-3）。与 `webui/archive/gaps.py` 的既有分类保持同一套词汇。
RESULT_KINDS = ("ok", "empty", "no_permission", "rate_limited", "error")

# 记录形状（AC-4 的结构化口径）。
SHAPES = ("period_report", "timeseries", "snapshot")
PERIOD_TYPES = ("annual", "half", "quarter", "point", "series")

# 兼容期需要双写的旧路径布局（`REQ-009.4` 的存档根，见 DATA_LAYER_PLAN §10.2）。
DEFAULT_ARCHIVE_ROOT_NAME = "turtle_archive"

# 按仓库既有习惯做「扁平导入」：`scripts/` 下的模块（`periods` / `tushare_collector` /
# `format_utils` …）依赖「直接执行脚本时 `scripts/` 在 sys.path 上」。
# `webui/archive/adapters/tushare.py` 用的是「临时插入、用完移除」，但数据层几乎每个模块
# 都要用 `scripts/periods.py`（期次口径的唯一权威），逐个进出反而更容易漏。
# 所以这里在包导入时插一次并**保留**：效果与「测试/脚本运行时的既有约定」一致。
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[1])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
