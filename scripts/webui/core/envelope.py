"""统一响应信封（AC-3.6）。

所有 `/api/v1` 响应都是同一个形状：

    {ok, schema_version, data, warnings, meta, error}

`meta` 用来回传「这份数据是怎么来的」（缓存命中、指纹、数据生成时间）——用户据此判断
该不该去点那个**联网**的按键，而不是靠猜页面卡不卡。
"""

from __future__ import annotations

import json

from .. import SCHEMA_VERSION
from .errors import BadJson, WebUIError


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


def json_body(ctx) -> dict:
    """解析请求体里的 JSON 对象；坏 JSON / 非对象 → `BAD_JSON`（AC-3.6）。

    放在这里而不是各 handler 里：请求体的解码与错误码是**契约**，
    散开写迟早有一个分支会漏成 500。
    """
    raw = getattr(ctx, "body", b"") or b""
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BadJson("请求体不是合法 JSON", hint="Content-Type 用 application/json。") from exc
    if not isinstance(payload, dict):
        raise BadJson("请求体必须是 JSON 对象", hint="形如 {\"command\": \"...\", \"params\": {}}。")
    return payload
