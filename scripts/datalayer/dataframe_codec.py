"""DataFrame ⇄ 仓内记录体的往返契约（`DATA_LAYER_PLAN` §6.3）。

仓里存的是**规范 JSON 行**（可读、可 grep、可 diff），读出来要变回既有 `get_*` 与
`compute_derived_metrics` 能用的 DataFrame。dtype 漂移会让 Markdown 表格与派生指标
**静默**变化，所以往返必须是**契约**：写入时逐列记 `dtype`，读出时按它还原。

空结果同样入仓（`rows_json == "[]"`）：**空也是信息**——
「这个接口确实返回空」与「还没拉过」必须能分开（`REQ-009.4` 的 `AC-4.5`）。
"""

from __future__ import annotations

import hashlib
import json

import pandas as pd


def column_contract(frame: pd.DataFrame) -> list[dict]:
    """逐列记下 ``{"name", "dtype"}``，顺序即列顺序。"""

    return [{"name": str(name), "dtype": str(frame[name].dtype)} for name in frame.columns]


def encode_frame(frame) -> dict:
    """DataFrame → ``columns_json`` / ``rows_json`` / ``content_sha256`` / ``bytes``。"""

    if frame is None:
        frame = pd.DataFrame()
    if not isinstance(frame, pd.DataFrame):
        frame = pd.DataFrame(frame)

    columns = column_contract(frame)
    if frame.empty:
        rows: list = []
    else:
        # 用 pandas 自己的序列化，避免自己写一套「标量长什么样」的规则。
        rows = json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso"))
    rows_json = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    payload = rows_json.encode("utf-8")
    return {
        "columns_json": json.dumps(columns, ensure_ascii=False),
        "rows_json": rows_json,
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def decode_frame(columns_json: str, rows_json: str) -> pd.DataFrame:
    """``columns_json`` + ``rows_json`` → DataFrame，按契约还原 dtype。"""

    columns = json.loads(columns_json) if columns_json else []
    rows = json.loads(rows_json) if rows_json else []
    names = [column["name"] for column in columns]
    frame = pd.DataFrame.from_records(rows, columns=names)
    for column in columns:
        dtype = column.get("dtype")
        if not dtype:
            continue
        try:
            frame[column["name"]] = frame[column["name"]].astype(dtype)
        except (TypeError, ValueError):
            # 例如「整列缺失的 int64」在 JSON 里全是 null，还原不回 int64。
            # 这里保留能还原出来的形状，不假装成功——往返断言会把这类接口暴露出来。
            continue
    return frame
