"""数据层的错误类型：每条都带稳定 `code` 与给人看的 `hint`。

CLI 退出码映射（`DATA_LAYER_PLAN` §11）：`0` 成功 / `2` 用法或前置错误
（含 `NO_TOKEN`、未确认）/ `4` 仓不可用 / `130` 中断。
"""

from __future__ import annotations


class DatalayerError(Exception):
    """数据层错误的基类。"""

    code = "DATALAYER_ERROR"

    def __init__(self, message: str, hint: str = ""):
        self.message = message
        self.hint = hint
        super().__init__(message)


class NoToken(DatalayerError):
    """没有可用的 Tushare token —— 零请求就退出（`REQ-009.4` 的 `AC-4.1` 语义）。"""

    code = "NO_TOKEN"


class QuotaConfirmRequired(DatalayerError):
    """批量拉取必须先看到调用量预估并显式确认（`AC-2`，沿用 `AC-4.2` 语义）。"""

    code = "QUOTA_CONFIRM_REQUIRED"


class BatchRunning(DatalayerError):
    """同一批次不并发（`AC-6`）。"""

    code = "BATCH_RUNNING"


class StoreUnavailable(DatalayerError):
    """仓根不可写 / SQLite 打不开（退出码 4）。"""

    code = "STORE_UNAVAILABLE"


class UniverseError(DatalayerError, ValueError):
    """清单条目不合法。"""

    code = "UNIVERSE_ERROR"


class UsageError(DatalayerError, ValueError):
    """用法或前置条件错误（退出码 2）。"""

    code = "USAGE_ERROR"
