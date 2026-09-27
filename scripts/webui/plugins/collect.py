"""Read-only collection archive and completeness views."""

from __future__ import annotations

import json
from pathlib import Path

from ..archive.gaps import completeness
from ..core import envelope
from ..core.errors import NotFound
from ..core.models import NavItem, PanelSpec


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


def _archive_summary(ctx):
    latest = {}
    for item in _manifest(ctx):
        key = (item.get("ticker", ""), item.get("period", ""), item.get("dataset", ""))
        latest[key] = item
    grouped = {}
    for item in latest.values():
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


def _batches(ctx):
    rows = []
    batch_dir = Path(ctx.config.archive_root) / "batches"
    for path in sorted(batch_dir.glob("*.json")) if batch_dir.is_dir() else ():
        try:
            batch = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        progress = batch.get("progress", {})
        usage = batch.get("usage", {})
        rows.append({
            "batch_id": batch.get("batch_id", path.stem),
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
        id="collect.archive", kind="table", title="采集存档完备度",
        provider=_archive_summary, size="full",
        description="仅读取本地原始存档台账；采集由显式 CLI 命令触发。",
    ))
    registry.panel(PanelSpec(
        id="collect.batches", kind="table", title="采集批次进度",
        provider=_batches, size="full",
        description="仅读取本地批次进度与配额消耗；不会触发远程请求。",
    ))
    registry.nav(NavItem(id="collect", title="采集存档", group="数据", order=5,
                         panels=("collect.archive", "collect.batches")))
    registry.route("GET", "/api/v1/collect/batches", lambda ctx, **_: envelope.ok(_batches(ctx)),
                   name="archive batches")
    registry.route("GET", "/api/v1/collect/batches/{batch_id}", _batch_detail, name="archive batch")
    registry.route("GET", "/api/v1/companies/{ticker}/gaps", _gaps, name="archive gaps")
