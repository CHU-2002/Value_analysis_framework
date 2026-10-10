"""数据层的配置：仓放哪儿、期次范围默认值、框架版本。

**不复用 `scripts/webui/config.py`**（数据层不该依赖 GUI，`DATA_LAYER_PLAN` §3.3）：
GUI 侧要读仓时由它把 `archive_root` 传进来，而不是反过来。

仓根仍是**仓库之外**的 `~/turtle_archive/`（`REQ-009.4` 的资产语义），
布局见 `DATA_LAYER_PLAN` §4.6。
"""

from __future__ import annotations

import os
from pathlib import Path

# 环境变量优先级：数据层自己的变量 → GUI 的变量（兼容 `make gui-collect`）→ 默认。
ENV_KEYS = ("TURTLE_ARCHIVE_ROOT", "WEBUI_ARCHIVE_ROOT")
DEFAULT_ARCHIVE_ROOT = "~/turtle_archive"

# 默认期次范围：按「最近 N 个年报期」推导（`DATA_LAYER_PLAN` §18.1 的「全局默认 + 清单可覆盖」）。
DEFAULT_PERIOD_YEARS = 5
DEFAULT_REPORT_TYPE = "年报"


def archive_root(explicit=None, env=None) -> Path:
    """解析仓根：显式参数 > 环境变量 > `~/turtle_archive`。"""

    if explicit:
        return Path(explicit).expanduser()
    source = os.environ if env is None else env
    for key in ENV_KEYS:
        value = str(source.get(key, "") or "").strip()
        if value:
            return Path(value).expanduser()
    return Path(DEFAULT_ARCHIVE_ROOT).expanduser()


def store_path(root) -> Path:
    """仓的单文件载体。"""

    return Path(root).expanduser() / "store.db"


def default_periods(today=None, years: int = DEFAULT_PERIOD_YEARS) -> list[str]:
    """默认期次范围：最近 ``years`` 个年报期次（接口期次，如 ``20251231``）。

    只做「最近 N 年」这一条推导；要拉更多期次时由 `--periods` 显式给出，
    或写进自选股清单（AC-1 的所需数据档位/期次覆盖）。
    """

    from datetime import date

    from periods import make_period, period_to_end_date

    end = today or date.today()
    # 年报期末属于上一年，通常次年 4 月底前披露；窗口未结束时默认取再前一年。
    # 当前年份的年报尚未结束，不能因已到 5 月就把未来 12/31 填进计划。
    latest_year = end.year - 1 if end.month >= 5 else end.year - 2
    # 期末日期不写字面量：走 periods.py 的「项目期次 → 接口期次」换算（期次口径只有一处权威）。
    return [period_to_end_date(make_period(year, "年报"))
            for year in range(latest_year, latest_year - max(1, years), -1)]
