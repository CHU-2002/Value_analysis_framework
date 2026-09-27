"""Read-only collection archive and completeness views."""

from __future__ import annotations

import json
from pathlib import Path

from ..archive.gaps import RESULT_KINDS, completeness
from ..core import envelope
from ..core.errors import NotFound
from ..core.models import NavItem, PanelSpec

# 结果枚举 → 人话 + 颜色（分段条的白名单状态，见 render/panels.py 的 BAR_STATES）。
# 这一步刻意留在插件里：渲染层不认识业务枚举。
_RESULT_TEXT = {
    "ok": ("已获取", "ok"),
    "empty": ("确实为空", "empty"),
    "no_permission": ("无权限", "denied"),
    "rate_limited": ("频率超限", "limited"),
    "error": ("其他错误", "error"),
}


def _manifest(ctx):
    path = ctx.config.archive_root / "manifest.jsonl"
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _latest_entries(ctx):
    """每个（标的, 期次, 数据集）只取最近一条台账 —— 完备度与缺口都按它算。"""
    latest = {}
    for item in _manifest(ctx):
        key = (item.get("ticker", ""), item.get("period", ""), item.get("dataset", ""))
        latest[key] = item
    return list(latest.values())


def _grouped(ctx):
    """{(标的, 期次): [最新台账记录, …]} —— 完备度、覆盖图、缺口图共用同一份分组。"""
    grouped = {}
    for item in _latest_entries(ctx):
        key = (item.get("ticker", ""), item.get("period", ""))
        grouped.setdefault(key, []).append(item)
    return grouped


def _health_panel(ctx, **_):
    """一眼看懂的那一块：存档总量 / 覆盖范围 / 完备度 / 缺口（`kind=stat`）。"""
    entries = _latest_entries(ctx)
    report = completeness(entries)
    counts = report["counts"]
    tickers = {item.get("ticker") for item in entries if item.get("ticker")}
    total, complete = counts["total"], counts["complete"]
    percent = round(100 * complete / total) if total else 0
    return {"items": [
        {"label": "原始存档", "value": f"{len(entries)} 条",
         "hint": "manifest.jsonl 里每个（标的, 期次, 接口）的最新一条"},
        {"label": "覆盖范围", "value": f"{len(tickers)} 个标的 · {len(_grouped(ctx))} 组标的×期次",
         "hint": "已完成采集的标的数与（标的, 期次）组合数"},
        {"label": "完备度", "value": f"{complete}/{total} · {percent}%",
         "state": "ok" if total and not report["gaps"] else ("warn" if total else "error"),
         "hint": "「已获取 + 确实为空」算完备；无权限 / 频率超限 / 其他错误算缺口"},
        {"label": "缺口", "value": f"{len(report['gaps'])} 个",
         "state": "ok" if not report["gaps"] else "error",
         "hint": f"无权限 {counts['no_permission']} · 频率超限 {counts['rate_limited']} "
                 f"· 其他错误 {counts['error']}"},
    ], "caption": "只看本地原始存档台账，不发任何网络请求。"}


def _coverage_panel(ctx, **_):
    """覆盖图（`kind=bars`）：一行一个（标的, 期次），条上按结果分段着色。

    缺口多的排在前面——「缺什么一眼看得见」靠的是排序 + 颜色，不是让用户读数字。
    """
    rows = []
    for (ticker, period), items in _grouped(ctx).items():
        counts = completeness(items)["counts"]
        total, complete = counts["total"], counts["complete"]
        percent = round(100 * complete / total) if total else 0
        rows.append({
            "_score": complete / total if total else 0,
            "label": f"{ticker} · {period}",
            "note": f"{complete}/{total} · {percent}%",
            "total": total,
            "parts": [
                {"name": _RESULT_TEXT[kind][0], "value": counts[kind], "state": _RESULT_TEXT[kind][1]}
                for kind in RESULT_KINDS if counts[kind]
            ],
        })
    rows.sort(key=lambda row: (row["_score"], row["label"]))
    for row in rows:
        row.pop("_score", None)
    return {"rows": rows,
            "caption": "覆盖图：每条 = 一个（标的, 期次）；红色/橙色段就是缺口所在，逐条原因见下方缺口清单。"}


def _gap_reason_panel(ctx, **_):
    """缺口按原因分组的条形图（`kind=bars`）：先看「为什么缺」，再看是哪些接口。"""
    report = completeness(_latest_entries(ctx))
    by_kind = {}
    for gap in report["gaps"]:
        by_kind.setdefault(gap.get("result") or "error", []).append(gap)
    rows = []
    for kind in ("no_permission", "rate_limited", "error"):
        items = by_kind.get(kind) or []
        if not items:
            continue
        datasets = sorted({item.get("dataset") or "?" for item in items})
        shown = "、".join(datasets[:4]) + ("…" if len(datasets) > 4 else "")
        rows.append({
            "label": _RESULT_TEXT[kind][0],
            "note": f"{len(items)} 个 · {shown}",
            "count": len(items),
            "parts": [{"name": _RESULT_TEXT[kind][0], "value": len(items),
                       "state": _RESULT_TEXT[kind][1]}],
        })
    # 各类共用同一把尺子（最大类 = 满格），否则每行都 100% 就比不出多少。
    scale = max((row["count"] for row in rows), default=0)
    for row in rows:
        row["total"] = scale
        row.pop("count", None)
    return {"rows": rows, "legend": [],
            "caption": "缺口按原因分组；完整明细（含接口原文摘要）见下方「缺口清单」。"}


def _batch_progress_panel(ctx, **_):
    """批次进度条（`kind=bars`）：一条 = 一个批次，右侧写清楚新增/命中/无权限/失败。"""
    rows = []
    for batch in _batch_records(ctx):
        progress = batch.get("progress", {})
        usage = batch.get("usage", {})
        completed = int(progress.get("completed", 0) or 0)
        total = int(progress.get("total", len(batch.get("targets", []))) or 0)
        status = batch.get("status", "")
        rows.append({
            "label": f"{batch.get('batch_id', '?')}｜{batch.get('profile', '')}",
            "note": f"{status} · 新增 {usage.get('new_requests', 0)} · 命中 {usage.get('archive_hits', 0)}"
                    f" · 无权限 {usage.get('no_permission', 0)} · 失败 {usage.get('failures', 0)}",
            "total": total,
            "parts": [{"name": "已完成", "value": completed,
                       "state": "ok" if status == "done" else "limited"}],
        })
    return {"rows": rows, "legend": [],
            "caption": "批次进度：条长 = 已完成 / 总数；每个批次都从显式采集命令开始。"}


def _gaps_panel(ctx, **_):
    """逐缺口原因（AC-4.6）：面板直接列出 no_permission / rate_limited / error 的接口原文摘要，
    不再只给计数——这段之前只有路由、没有面板引用（独立验收判为死代码）。"""
    report = completeness(_latest_entries(ctx))
    rows = [
        {
            "ticker": gap.get("ticker", ""),
            "period": gap.get("period", ""),
            "dataset": gap.get("dataset", ""),
            "result": gap.get("result", ""),
            "reason": gap.get("error_excerpt") or "",
        }
        for gap in report["gaps"]
    ]
    counts = report["counts"]
    return {
        "columns": [
            {"key": "ticker", "title": "标的"}, {"key": "period", "title": "期次"},
            {"key": "dataset", "title": "接口"}, {"key": "result", "title": "结果"},
            {"key": "reason", "title": "原因（接口原文摘要）"},
        ],
        "rows": rows,
        "meta": {"complete": counts["complete"], "total": counts["total"],
                 "gaps": len(rows)},
    }


def _archive_summary(ctx):
    grouped = {}
    for item in _latest_entries(ctx):
        key = (item.get("ticker", ""), item.get("period", ""))
        grouped.setdefault(key, []).append(item)
    rows = []
    for (ticker, period), items in sorted(grouped.items()):
        count = completeness(items)["counts"]
        rows.append({"ticker": ticker, "period": period,
                     "completeness": f"{count['complete']}/{count['total']}",
                     "permissions": count["no_permission"], "rate_limited": count["rate_limited"],
                     "errors": count["error"]})
    return {"columns": [
        {"key": "ticker", "title": "标的"}, {"key": "period", "title": "期次"},
        {"key": "completeness", "title": "完备度"}, {"key": "permissions", "title": "无权限"},
        {"key": "rate_limited", "title": "频率受限"}, {"key": "errors", "title": "其他错误"},
    ], "rows": rows}


def _batch_records(ctx):
    """按 created_at 升序读出本地批次记录；坏文件跳过（一行坏数据不该让整页消失）。"""
    records = []
    batch_dir = Path(ctx.config.archive_root) / "batches"
    for path in sorted(batch_dir.glob("*.json")) if batch_dir.is_dir() else ():
        try:
            batch = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        batch.setdefault("batch_id", path.stem)
        records.append(batch)
    records.sort(key=lambda batch: (batch.get("created_at") or "", batch.get("batch_id") or ""))
    return records


def _batches(ctx):
    rows = []
    for batch in _batch_records(ctx):
        progress = batch.get("progress", {})
        usage = batch.get("usage", {})
        rows.append({
            "batch_id": batch.get("batch_id", ""),
            "profile": batch.get("profile", ""),
            "status": batch.get("status", ""),
            "completed": progress.get("completed", 0),
            "total": progress.get("total", len(batch.get("targets", []))),
            "new_requests": usage.get("new_requests", 0),
            "archive_hits": usage.get("archive_hits", 0),
            "no_permission": usage.get("no_permission", 0),
        })
    return {"columns": [
        {"key": "batch_id", "title": "批次"}, {"key": "profile", "title": "档案"},
        {"key": "status", "title": "状态"}, {"key": "completed", "title": "已完成"},
        {"key": "total", "title": "总数"}, {"key": "new_requests", "title": "新增请求"},
        {"key": "archive_hits", "title": "命中存档"}, {"key": "no_permission", "title": "无权限"},
    ], "rows": rows}


def _batch_detail(ctx, batch_id, **_):
    path = Path(ctx.config.archive_root) / "batches" / f"{batch_id}.json"
    try:
        return envelope.ok(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise NotFound(f"没有采集批次 {batch_id!r}", hint="检查批次 ID 或先显式运行一次采集。") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"采集批次 {batch_id!r} 记录损坏") from exc


def _gaps(ctx, ticker, **_):
    report = completeness(item for item in _manifest(ctx) if item.get("ticker") == ticker)
    return envelope.ok({"ticker": ticker, **report})


def contribute(registry):
    registry.panel(PanelSpec(
        id="collect.health", kind="stat", title="存档概览",
        provider=_health_panel, size="full",
        description="存档总量、覆盖范围、完备度与缺口——先看这一块。",
    ))
    registry.panel(PanelSpec(
        id="collect.coverage", kind="bars", title="覆盖图（按标的 · 期次）",
        provider=_coverage_panel, size="full",
        description="每条一个（标的, 期次）：绿色=已获取、灰色=确实为空、红/橙=缺口；缺口多的排在前面。",
    ))
    registry.panel(PanelSpec(
        id="collect.gap_reasons", kind="bars", title="缺口原因分布",
        provider=_gap_reason_panel, size="full",
        description="缺口按「无权限 / 频率超限 / 其他错误」分组，并列出涉及的接口。",
    ))
    registry.panel(PanelSpec(
        id="collect.batch_progress", kind="bars", title="批次进度",
        provider=_batch_progress_panel, size="full",
        description="每个采集批次的完成度与配额消耗（新增请求 / 命中存档 / 无权限 / 失败）。",
    ))
    registry.panel(PanelSpec(
        id="collect.archive", kind="table", title="采集存档完备度（明细）",
        provider=_archive_summary, size="full",
        description="覆盖图的数字版；仅读取本地原始存档台账，采集由显式 CLI 命令触发。",
    ))
    registry.panel(PanelSpec(
        id="collect.gaps", kind="table", title="缺口清单（逐条原因）",
        provider=_gaps_panel, size="full",
        description="列出每个缺口接口的结果分类与错误原文摘要；补齐请用 --only-gaps 收敛目标。",
    ))
    registry.panel(PanelSpec(
        id="collect.batches", kind="table", title="采集批次（明细）",
        provider=_batches, size="full",
        description="批次进度的数字版；仅读取本地批次记录，不会触发远程请求。",
    ))
    registry.nav(NavItem(id="collect", title="采集存档", group="数据", order=5,
                         panels=("collect.health", "collect.coverage", "collect.gap_reasons",
                                 "collect.batch_progress", "collect.archive", "collect.gaps",
                                 "collect.batches")))
    registry.route("GET", "/api/v1/collect/batches", lambda ctx, **_: envelope.ok(_batches(ctx)),
                   name="archive batches")
    registry.route("GET", "/api/v1/collect/batches/{batch_id}", _batch_detail, name="archive batch")
    registry.route("GET", "/api/v1/companies/{ticker}/gaps", _gaps, name="archive gaps")
