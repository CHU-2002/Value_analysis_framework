"""结果分类、完备度与缺口（`REQ-011` 的 `AC-7`）。

**这是分类的唯一实现**：`classify_result` / `completeness` / `gap_targets` 原先写在
`scripts/webui/archive/gaps.py`（`REQ-009.4` 的 `AC-4.5` / `AC-4.6` 的判据），
按 `DATA_LAYER_PLAN` §12「webui/archive 逐步改为转调 datalayer」搬到数据层，
GUI 侧改为**转调**——行为逐字保持不变，只是不再是两份实现。

补缺口只做两件事：① 按仓里上次的结果把目标收敛到缺口；② 补齐后重新算完备度。
「无权限」永远不算完成（`no_permission` 的完善度不计入 `complete`）。
"""

from __future__ import annotations

RESULT_KINDS = ("ok", "empty", "no_permission", "rate_limited", "error")

# 「已完成」的结果枚举：`empty` 也算完成（拉到了确实为空，与「还没拉过」不同）。
DONE_KINDS = ("ok", "empty")


def classify_result(data=None, error=None):
    """把一次远程调用的结局归入五类之一，并给出脱敏后的原文摘要。"""

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
    """计数 + 缺口清单。`complete` 只数 `ok` 与 `empty`，无权限不计入成功。"""

    counts = {kind: 0 for kind in RESULT_KINDS}
    gaps = []
    for item in results:
        kind = item.get("result", "error")
        if kind not in counts:
            kind = "error"
        counts[kind] += 1
        if kind not in DONE_KINDS:
            gaps.append({key: item.get(key) for key in
                         ("dataset", "ticker", "period", "result", "error_excerpt")})
    counts["total"] = sum(counts.values())
    counts["complete"] = counts["ok"] + counts["empty"]
    return {"counts": counts, "gaps": gaps}


def gap_targets(targets, result_of):
    """从目标清单里**只留缺口**：没有记录、或上次结果不是 `ok`/`empty` 的目标。

    这是「按缺口清单只补缺口目标」的入口：先把目标集合收敛到缺口，
    调用量预估与批次进度就都只反映缺口，补齐后完备度直接收敛。
    """

    return [target for target in targets if result_of(target) not in DONE_KINDS]


def completeness_by_targets(targets, result_of):
    """按目标清单算完备度：缺口 = 目标集合 − 仓里已完成的目标。

    与 :func:`completeness`（按仓内记录算）互补——前者回答「我打算拉的拉全了没有」，
    后者回答「仓里买回来的东西是什么状态」。
    """

    counts = {kind: 0 for kind in RESULT_KINDS}
    gaps = []
    for target in targets:
        kind = result_of(target) or "error"
        if kind not in counts:
            kind = "error"
        counts[kind] += 1
        if kind not in DONE_KINDS:
            gaps.append({**{key: target.get(key) for key in ("ticker", "dataset", "period")},
                         "result": kind})
    counts["total"] = sum(counts.values())
    counts["complete"] = counts["ok"] + counts["empty"]
    return {"counts": counts, "gaps": gaps}
