"""统一响应信封（AC-3.6）。

所有 `/api/v1` 响应都是同一个形状：

    {ok, schema_version, data, warnings, meta, error}

`meta` 用来回传「这份数据是怎么来的」（缓存命中、指纹、数据生成时间）——用户据此判断
该不该去点那个**联网**的按键，而不是靠猜页面卡不卡。
"""

from __future__ import annotations

import json

from .. import SCHEMA_VERSION
from .errors import WebUIError


def ok(data, *, warnings=None, meta=None) -> dict:
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "data": data,
        "warnings": list(warnings or []),
        "meta": dict(meta or {}),
        "error": None,
    }


def failure(
    error: WebUIError | None = None,
    *,
    code: str = "INTERNAL",
    message: str = "",
    hint: str = "",
    warnings=None,
) -> dict:
    if error is not None:
        payload = error.to_error()
    else:
        payload = {"code": code, "message": message, "hint": hint}
    return {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "data": None,
        "warnings": list(warnings or []),
        "meta": {},
        "error": payload,
    }


def dumps(payload) -> bytes:
    """统一序列化：中文不转义（便于直接读日志/响应），紧凑分隔符。"""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
