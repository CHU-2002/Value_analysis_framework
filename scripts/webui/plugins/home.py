"""工作台首页（`REQ-012.1` 的 `AC-1.3` / `AC-2`）。

默认落地页从「采集存档」换成这一页（`NavItem(default=True)`——核心不认识 `home` 这个 id，
谁声明谁生效）。三块内容，全部**服务端可断言**：

| 面板 | kind | 内容 |
|------|------|------|
| `home.universe` | `table` | 每行一家公司：显示名 / 数据期次与拉取时间 / 最近分析 / 状态 / 待办 |
| `home.todo` | `stat` | 待办汇总（没拉数据 / 有缺口 / 分析落后 / 上次失败） |
| `home.actions` | `actions` | 直达动作（与「任务」页同一份动作清单） |

两条刻意的取舍：

- **待办只按本地事实判定**（仓里的期次、产物时间、任务历史），不引入「最新可用期次」
  这种需要外部日历的知识——猜错了会让人白跑一次联网动作；
- **空库给引导而不是空表**：「还没有公司数据 → 拉取数据 / 把既有产物接进来」，
  并且把 `output/` 下已经存在的公司列出来（老用户升级上来时 output/ 里往往已经有东西）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..core.errors import ArtifactMissing, WebUIError
from ..core.models import NavItem, PanelSpec
from .companies import companies_dataset, display_name

#: 最近任务里出现这些状态就算「上次失败」（排队/等待都不算，它们还没结束）。
_FAILED_STATUSES = ("failed", "cancelled")


def _store(ctx):
    """数据层原始仓（只读）。仓还没建立时返回 None——那是**正常空库**，不是错误。"""
    from datalayer.store import DataStore

    try:
        return DataStore(ctx.config.archive_root)
    except Exception:  # noqa: BLE001
        return None


def _universe_entries(store) -> list:
    if store is None:
        return []
    try:
        from datalayer.universe import Universe

        return Universe(store).entries()
    except Exception:  # noqa: BLE001
        return []


def _store_facts(store, ticker: str) -> dict:
    """仓里这家公司的事实：有没有记录、最近一次落盘时间、期次分布。"""
    if store is None or not ticker:
        return {}
    try:
        records = store.records(ticker=ticker, include_rows=False)
    except Exception:  # noqa: BLE001
        return {}
    if not records:
        return {"records": 0}
    fetched = sorted(str(item.get("fetched_at") or "") for item in records)
    return {
        "records": len(records),
        "gaps": sum(1 for item in records if item.get("result") not in ("ok", "empty")),
        "fetched_at": fetched[-1],
    }


def _companies(ctx) -> list:
    try:
        return companies_dataset(ctx)[0]["companies"]
    except ArtifactMissing:
        return []
    except WebUIError:
        return []


def _by_ticker(companies: list) -> dict:
    index = {}
    for item in companies:
        for key in (str(item.get("ticker") or "").strip().upper(), item.get("dir")):
            if key:
                index.setdefault(key, item)
    return index


def _failed_jobs(ctx) -> dict:
    """最近任务里每个公司（动作上下文）最后一次失败的记录。"""
    runner = getattr(ctx.registry, "jobs", None)
    if runner is None:
        return {}
    failed = {}
    for job in runner.list_jobs():
        if job.get("status") not in _FAILED_STATUSES:
            continue
        context = ((job.get("handoff") or {}).get("paths") or {})
        key = str(context.get("ticker") or context.get("company_dir") or "").strip().upper()
        if key and key not in failed:
            failed[key] = job
    return failed


def _runtime_stamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def _mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


def universe_rows(ctx) -> list:
    """工作台的一行：一家公司 + 它的状态与待办（**纯数据**，表格由通用渲染层显示）。"""
    store = _store(ctx)
    companies = _companies(ctx)
    index = _by_ticker(companies)
    failed = _failed_jobs(ctx)
    rows = []
    seen = set()

    def row_for(label, display, ticker, directory, entry) -> dict:
        facts = _store_facts(store, ticker)
        pack = Path(ctx.config.output_root) / directory / "data_pack_market.md"
        analysis = _mtime(Path(ctx.config.output_root) / directory / "latest.json")
        todos = []
        has_raw = bool(facts.get("records"))
        period = (entry or {}).get("primary_period") or "未知"
        if period == "未知":
            todos.append("数据期次未知")
        if not analysis:
            todos.append("尚无可核验的分析记录")
        if not has_raw and not pack.is_file():
            todos.append("还没拉数据")
        if facts.get("gaps"):
            todos.append(f"{facts['gaps']} 个数据缺口")
        if entry and entry.get("downstream_stale"):
            todos.append("下游产物过期")
        job = failed.get(str(ticker or "").upper()) or failed.get(str(directory or "").upper())
        if job:
            todos.append("上次任务失败")
        state = "warn" if todos else "ok"
        if not (has_raw or pack.is_file()):
            state = "error"
        return {
            "company": display,
            "href": f"#charts?company={ticker or directory}",
            "data_period": period,
            "data_pulled": str(facts.get("fetched_at") or "")[:16].replace("T", " "),
            "analysis_at": analysis or "未知",
            "state": {"ok": "正常", "warn": "待更新", "error": "缺数据"}[state],
            "todo": "、".join(todos) or "—",
            "_state": state,
        }

    for entry in companies:
        seen.add(entry["dir"])
        seen.add(str(entry.get("ticker") or "").upper())
        rows.append(row_for(
            entry["dir"], entry["display_name"], entry.get("ticker", ""), entry["dir"], entry,
        ))
    for entry in _universe_entries(store):
        ticker = str(entry.get("ticker") or "").strip()
        if not ticker or ticker.upper() in seen:
            continue
        product = index.get(ticker.upper())
        rows.append(row_for(
            ticker, display_name(ticker, entry.get("display_name") or ""), ticker,
            (product or {}).get("dir", ""), product or {},
        ))
    rows.sort(key=lambda item: item["company"])
    return rows


def _universe_panel(ctx, **_):
    rows = universe_rows(ctx)
    if not rows:
        guide = ("还没有任何公司数据。① 先在本页点击「添加公司」，或「从既有产物导入」，"
                 "② 再执行「拉取全部数据」。")
        return {
            "columns": _UNIVERSE_COLUMNS,
            "rows": [],
            "meta": {"empty": True, "guide": guide},
            "guide": guide,
        }
    return {"columns": _UNIVERSE_COLUMNS, "rows": rows, "meta": {"count": len(rows)}}


_UNIVERSE_COLUMNS = [
    {"key": "company", "title": "公司", "href_key": "href", "search": True, "sort": "text"},
    {"key": "data_period", "title": "数据期次", "sort": "text", "search": True},
    {"key": "data_pulled", "title": "数据拉取时间", "sort": "text"},
    {"key": "analysis_at", "title": "最近分析", "sort": "text"},
    {"key": "state", "title": "状态", "sort": "text"},
    {"key": "todo", "title": "待办", "search": True},
]


def _todo_panel(ctx, **_):
    rows = universe_rows(ctx)
    counts = {
        "no_data": sum(1 for row in rows if "还没拉数据" in row["todo"]),
        "gaps": sum(1 for row in rows if "数据缺口" in row["todo"]),
        "stale": sum(1 for row in rows if "下游产物过期" in row["todo"]),
        "failed": sum(1 for row in rows if "上次任务失败" in row["todo"]),
    }
    items = [
        {"label": "跟踪的公司", "value": str(len(rows)), "state": "ok" if rows else "warn",
         "hint": "来自自选股清单与已经分析过的公司" if rows else "还没有公司：先登记清单，再拉取数据"},
        {"label": "还没拉数据", "value": str(counts["no_data"]),
         "state": "warn" if counts["no_data"] else "ok",
         "hint": "在「数据」页执行「拉取全部数据」"},
        {"label": "数据有缺口", "value": str(counts["gaps"]),
         "state": "warn" if counts["gaps"] else "ok",
         "hint": "执行「只补缺口」比全量拉取省配额"},
        {"label": "分析落后/下游过期", "value": str(counts["stale"]),
         "state": "warn" if counts["stale"] else "ok",
         "hint": "在公司页执行「更新这家公司的分析」"},
        {"label": "上次任务失败", "value": str(counts["failed"]),
         "state": "error" if counts["failed"] else "ok",
         "hint": "去「任务」页看失败原因并重试"},
    ]
    return {"items": items, "generated_at": _runtime_stamp()}


def contribute(registry):
    registry.panel(PanelSpec(
        id="home.universe", kind="table", title="我跟踪的公司与状态",
        provider=_universe_panel, size="full",
        options={"table": {"search": True, "sort": True, "page": 50}},
        description="数据期次、拉取时间、最近分析与待办；点公司名进入它的页面。",
    ))
    registry.panel(PanelSpec(
        id="home.todo", kind="stat", title="待办",
        provider=_todo_panel, size="full",
        description="只按本地事实判定（仓里的期次、产物时间、任务历史），不猜外部日历。",
    ))
    registry.panel(PanelSpec(
        id="home.actions", kind="actions", title="从这里开始", render="client",
        endpoint="/api/v1/actions", provider=_actions_provider, size="full",
        description="拉取数据 / 只补缺口 / 更新这家公司的分析：参数由当前上下文预填。",
    ))
    # 默认落地页（AC-1.3）：核心只认 `default=True` 这个数据，不认 `home` 这个 id。
    registry.nav(NavItem(
        id="home", title="工作台", placement="primary", group="工作台", order=1, panels=(
            "home.todo", "home.universe", "home.actions",
        ),
        default=True,
        description="一眼看到哪家公司该更新了，以及现在可以做什么。",
    ))


def _actions_provider(ctx, **_):
    from .commands import list_actions

    return list_actions(ctx)


__all__ = ["contribute", "universe_rows"]
