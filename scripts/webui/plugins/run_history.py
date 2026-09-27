"""迭代台账时间线（REQ-009.2 AC-2.5；父需求 AC-5）。

只读 REQ-003 落盘的 `history.jsonl`（追加式台账）与 `latest.json`（当前生效指针）：
按时间**倒序**列出每次 run，标出当前生效的那一次，变化型 run 给出 `supersedes` 与
`conclusions_changed`。`history.jsonl` 缺失时给**空时间线**而不是错误页——
「这家公司还没做过增量更新」是正常状态，不是故障。

损坏的 JSONL 行跳过并在 `warnings` 里留痕：一行坏数据不该让整条时间线消失。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core import envelope
from ..core.errors import ArtifactMissing
from ..core.models import DatasetSpec, NavItem, PanelSpec, Param
from ..datastore import parsers
from .companies import company_base, company_caption, resolve_company


def _read_jsonl(path: Path) -> tuple:
    entries, warnings = [], []
    if not path.is_file():
        return entries, warnings
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            warnings.append(f"history.jsonl 第 {number} 行损坏，已跳过")
            continue
        if isinstance(payload, dict):
            entries.append(payload)
        else:
            warnings.append(f"history.jsonl 第 {number} 行不是对象，已跳过")
    return entries, warnings


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _changes(entry: dict) -> list:
    changes = entry.get("conclusions_changed") or []
    result = []
    for change in changes:
        if isinstance(change, dict):
            label = change.get("conclusion") or change.get("id") or change.get("module") or ""
            detail = change.get("change") or change.get("summary") or change.get("to") or ""
            result.append(f"{label}：{detail}".strip("：") or json.dumps(change, ensure_ascii=False))
        else:
            result.append(str(change))
    return result


def parse_runs(sources: list, params: dict) -> dict:
    """`history.jsonl` + `latest.json` → 时间线（倒序）+ 当前生效 run。"""
    paths = {Path(path).name: Path(path) for path in sources}
    entries, warnings = [], []
    history = paths.get("history.jsonl")
    if history is not None:
        entries, warnings = _read_jsonl(history)
    elif not paths.get("latest.json"):
        raise ArtifactMissing(
            "这家公司还没有迭代台账（history.jsonl / latest.json）",
            hint="第一次 /business-analysis 之后才会有 run 记录。",
        )
    latest = _read_json(paths["latest.json"]) if paths.get("latest.json") else {}
    current = latest.get("run_id", "")

    runs = []
    for entry in entries:
        run_id = str(entry.get("run_id", ""))
        framework = entry.get("framework") or {}
        subject = entry.get("subject") or {}
        runs.append(
            {
                "run_id": run_id,
                "kind": entry.get("kind", ""),
                "at": entry.get("created_at") or entry.get("recorded_at") or "",
                "supersedes": entry.get("supersedes", "") or "",
                "primary_period": entry.get("primary_period", "") or "",
                "report_periods": list(entry.get("report_periods") or []),
                "status": entry.get("status", ""),
                "ticker": subject.get("ticker", ""),
                "company": subject.get("company", ""),
                "framework_commit": str((framework.get("git_commit") or ""))[:12],
                "changes": _changes(entry),
                "is_current": bool(current) and run_id == current,
            }
        )
    runs.sort(key=lambda item: item["at"], reverse=True)
    return {
        "count": len(runs),
        "latest_run": current,
        "primary_period": latest.get("primary_period", "") or "",
        "runs": runs,
        "warnings": warnings,
    }


def register_parsers() -> None:
    parsers.register_parser("runs.timeline", parse_runs, replace=True)


def runs_dataset(ctx, base: Path) -> tuple:
    return ctx.registry.datastore.get(
        "runs.timeline", base=base, params={}
    )


_EMPTY = {"count": 0, "latest_run": "", "primary_period": "", "runs": [], "warnings": []}


def _runs(ctx, company=None) -> tuple:
    base = company_base(ctx, company)
    try:
        data, meta = runs_dataset(ctx, base)
        return data, meta
    except ArtifactMissing:
        return dict(_EMPTY), {"empty": True}


def _timeline_panel(ctx, company=None, **_):
    company = resolve_company(ctx, company)
    data, meta = _runs(ctx, company)
    items = []
    for run in data["runs"]:
        badges = [run["kind"] or "run"]
        if run["is_current"]:
            badges.append("当前生效")
        if run["supersedes"]:
            badges.append(f"取代 {run['supersedes'][:12]}")
        detail_bits = [part for part in (run["primary_period"], run["framework_commit"]) if part]
        repeated = "、".join(run["report_periods"])
        if repeated:
            detail_bits.append(f"期次：{repeated}")
        items.append(
            {
                "at": run["at"],
                "title": f"{run['run_id']} · {run['kind'] or 'run'}",
                "badges": badges,
                "detail": " · ".join(detail_bits),
                "changes": run["changes"],
                "link": f"#report?company={company}&run={run['run_id']}",
            }
        )
    return {"items": items, "meta": meta, "warnings": data.get("warnings", []),
            "caption": company_caption(company)}


def _status_panel(ctx, company=None, **_):
    company = resolve_company(ctx, company)
    data, _ = _runs(ctx, company)
    items = [
        {"label": "run 数", "value": data["count"]},
        {"label": "当前 run", "value": data["latest_run"] or "—",
         "state": "ok" if data["latest_run"] else "warn"},
        {"label": "当前期次", "value": data["primary_period"] or "—"},
    ]
    return {"items": items, "caption": company_caption(company)}


def _runs_route(ctx, dir, **_):
    data, meta = _runs(ctx, dir)
    return envelope.ok(data, meta=meta, warnings=data.get("warnings", []))


def contribute(registry):
    register_parsers()
    registry.dataset(DatasetSpec(
        name="runs.timeline", sources=("history.jsonl", "latest.json"),
        parser="runs.timeline", parser_version=1,
    ))
    registry.panel(PanelSpec(
        id="runs.status", kind="stat", title="台账概览", size="full",
        provider=_status_panel,
        params=(Param("company", type="company", source="selection.company"),),
    ))
    registry.panel(PanelSpec(
        id="runs.timeline", kind="timeline", title="迭代记录（倒序）", size="full",
        provider=_timeline_panel,
        params=(Param("company", type="company", source="selection.company"),),
        description="每次 run 的时间、类型、期次、取代关系与结论变化；当前生效的那次有标记。",
    ))
    registry.nav(NavItem(id="runs", title="迭代记录", group="浏览", order=40,
                         panels=("runs.status", "runs.timeline")))
    registry.route("GET", "/api/v1/companies/{dir}/runs", _runs_route, name="company runs")


__all__ = ["contribute", "parse_runs", "runs_dataset"]
