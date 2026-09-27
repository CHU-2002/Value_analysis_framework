"""Classification and completeness summaries for archive targets."""

from __future__ import annotations

RESULT_KINDS = ("ok", "empty", "no_permission", "rate_limited", "error")


def classify_result(data=None, error=None):
    if error is None:
        empty = data is None or (hasattr(data, "empty") and data.empty)
        if isinstance(data, (list, tuple, dict, str, bytes)):
            empty = empty or len(data) == 0
        if empty:
            return "empty", None
        return "ok", None
    message = str(error)
    lowered = message.lower()
    if any(term in lowered for term in ("no permission", "没有权限", "无权限", "积分不足", "权限")):
        kind = "no_permission"
    elif any(term in lowered for term in ("rate limit", "频率", "调用频率", "too many request", "429")):
        kind = "rate_limited"
    else:
        kind = "error"
    return kind, message[:500]


def completeness(results):
    counts = {kind: 0 for kind in RESULT_KINDS}
    gaps = []
    for item in results:
        kind = item.get("result", "error")
        if kind not in counts:
            kind = "error"
        counts[kind] += 1
        if kind not in ("ok", "empty"):
            gaps.append({key: item.get(key) for key in
                         ("dataset", "ticker", "period", "result", "error_excerpt")})
    counts["total"] = sum(counts.values())
    counts["complete"] = counts["ok"] + counts["empty"]
    return {"counts": counts, "gaps": gaps}
