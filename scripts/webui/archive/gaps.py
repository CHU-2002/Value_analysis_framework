"""`scripts/webui/archive/gaps.py`：结果分类与完备度的**转调**入口。

`REQ-011` 把 `classify_result` / `completeness` / `gap_targets` 搬到数据层
（`datalayer/gaps.py`）——分类是「买回来的数据」的语义，属于数据层；
GUI 只是展示它。这里保留同名导出，`REQ-009.4` 的既有判据与测试一行不改。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 与 `webui/archive/adapters/tushare.py` 同一套「扁平导入」做法：临时把 `scripts/`
# 放到 `sys.path` 上，让 `import datalayer...` 在不安装包的情况下可用。
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2])
_added = _SCRIPTS_DIR not in sys.path
if _added:
    sys.path.insert(0, _SCRIPTS_DIR)
try:
    from datalayer.gaps import (  # noqa: F401  （转调，保持既有导出名）
        DONE_KINDS,
        RESULT_KINDS,
        classify_result,
        completeness,
        gap_targets,
    )
finally:
    if _added:
        sys.path.remove(_SCRIPTS_DIR)

__all__ = ["DONE_KINDS", "RESULT_KINDS", "classify_result", "completeness", "gap_targets"]
