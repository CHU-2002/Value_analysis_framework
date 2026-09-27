"""Read-only collection archive and completeness views."""

from __future__ import annotations

import json

from ..archive.gaps import completeness
from ..core import envelope
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


def _gaps(ctx, ticker, **_):
    report = completeness(item for item in _manifest(ctx) if item.get("ticker") == ticker)
    return envelope.ok({"ticker": ticker, **report})


def contribute(registry):
    registry.panel(PanelSpec(
        id="collect.archive", kind="table", title="采集存档完备度",
        provider=_archive_summary, size="full",
        description="仅读取本地原始存档台账；采集由显式 CLI 命令触发。",
    ))
    registry.nav(NavItem(id="collect", title="采集存档", group="数据", order=5,
                         panels=("collect.archive",)))
    registry.route("GET", "/api/v1/companies/{ticker}/gaps", _gaps, name="archive gaps")
