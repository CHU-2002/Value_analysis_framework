"""Read-only collection archive and completeness views."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..archive.gaps import completeness
from ..core import envelope
from ..core.errors import NotFound
from ..core.models import NavItem, PanelSpec
from ..core.security import safe_join


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
    # 必须 jail，不能直接拼路径：`batch_id` 来自 URL 路径段，而 `Route.match()` 是**先按
    # `([^/]+)` 匹配、之后才 `unquote()`**，所以 `..%2f..%2f%2ftmp%2fx` 会把 `/` 与 `..`
    # 解码进参数里。父需求独立验收（报告里的 B1，本条目登记为 V1）就是这样读到存档根以外的
    # 任意 `*.json` 的——同一条链路上 `companies/{dir}/*` 早就走 `company_base()` 的 jail，
    # 只有这里漏了（`AC-6`：文件访问限制在允许的根之下，拒绝 `..`、绝对路径与越界符号链接）。
    path = safe_join(Path(ctx.config.archive_root), "batches", f"{batch_id}.json")
    try:
        return envelope.ok(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise NotFound(f"没有采集批次 {batch_id!r}", hint="检查批次 ID 或先显式运行一次采集。") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"采集批次 {batch_id!r} 记录损坏") from exc


def _gaps(ctx, ticker, **_):
    report = completeness(item for item in _manifest(ctx) if item.get("ticker") == ticker)
    return envelope.ok({"ticker": ticker, **report})


def _rebuild_entries(ctx):
    """「离线重建（不花钱）」面板：只用仓库内已有的产物目录，**不读原始仓**。

    `AC-5` 要求「拉取」与「重建」在 CLI **与界面**上分得清。原始仓在仓库之外的
    `~/turtle_archive`，控制台的路径 jail 只允许 `output/` 子树——要让界面读仓得先
    显式新增允许根，那是 `REQ-012.4` 的事（`DATA_LAYER_PLAN` §4.6）。所以这里只展示
    「产物在哪、重建命令是什么」，不触发任何动作、不联网、不花钱。
    """

    root = Path(ctx.config.output_root)
    rows = []
    for pack in sorted(root.glob("*/data_pack_market.md")) if root.is_dir() else ():
        company = pack.parent.name
        ticker = ""
        record = pack.parent / "record.json"
        if record.is_file():
            try:
                payload = json.loads(record.read_text(encoding="utf-8"))
                ticker = str((payload.get("subject") or {}).get("ticker", "")).strip()
            except (OSError, json.JSONDecodeError):
                ticker = ""
        rows.append({
            "company": company,
            "pack": pack.relative_to(root).as_posix(),
            "updated": datetime.fromtimestamp(pack.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "rebuild": f"make data-rebuild ARGS='--ticker {ticker or company}'",
        })
    return {"columns": [
        {"key": "company", "title": "公司"},
        {"key": "pack", "title": "数据包（由仓离线重建）"},
        {"key": "updated", "title": "产物时间"},
        {"key": "rebuild", "title": "重建命令（不花钱）"},
    ], "rows": rows}


def contribute(registry):
    registry.panel(PanelSpec(
        id="collect.archive", kind="table", title="采集存档完备度",
        provider=_archive_summary, size="full",
        description="仅读取本地原始存档台账；采集由显式 CLI 命令触发。",
    ))
    registry.panel(PanelSpec(
        id="collect.batches", kind="table", title="采集批次进度",
        provider=_batches, size="full",
        options={"table": {"search": True, "sort": True, "page": 50}},
        description="仅读取本地批次进度与配额消耗；不会触发远程请求。",
    ))
    registry.panel(PanelSpec(
        id="collect.rebuild", kind="table", title="离线重建（不花钱，与上面的采集分开）",
        provider=_rebuild_entries, size="full",
        options={"table": {"search": True, "sort": True}},
        description="由原始仓离线重建数据包；本面板只读产物目录，不读仓、不联网、不触发动作。",
    ))
    registry.panel(PanelSpec(
        id="collect.gaps", kind="table", title="缺口清单（逐条原因）",
        provider=_gaps_panel, size="full",
        options={"table": {"search": True, "sort": True, "page": 50}},
        description="列出每个缺口接口的结果分类与错误原文摘要；补齐缺口在「数据」页一键完成。",
    ))
    registry.nav(NavItem(id="collect", title="采集存档", group="数据", order=5,
                         panels=("collect.archive", "collect.batches", "collect.rebuild",
                                 "collect.gaps")))
    registry.route("GET", "/api/v1/collect/batches", lambda ctx, **_: envelope.ok(_batches(ctx)),
                   name="archive batches")
    registry.route("GET", "/api/v1/collect/batches/{batch_id}", _batch_detail, name="archive batch")
    registry.route("GET", "/api/v1/companies/{ticker}/gaps", _gaps, name="archive gaps")
