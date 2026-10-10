"""数据页（`REQ-012.4`）：把 `REQ-011` 的能力接成界面上的一件事。

三块，全部**读原始仓**（不是读 Markdown，`AC-4.1`）：

| 面板 | 内容 |
|------|------|
| `data.universe` | 自选股清单：标的、显示名、档位、启用状态、完备度、缺口数 |
| `data.gaps` | 缺口清单：可按标的/期次/数据集下钻，附接口原文摘要（`AC-4.3` 的「看缺口」） |
| `data.store` | 存储概览：仓规模、各结果计数、最近批次、`fetched_at` 分布 |

「拉取」与「重建」是**两个语义不同的动作**（`AC-4.4` / `AC-6`），声明在
`plugins/actions.py` 里，这里只把它们呈现出来并给出预估：

- 拉取（联网、花配额、需确认）——预估调用量来自数据层自己的 `pull.plan`，
  **服务端算**，所以能被 CI 断言；
- 从仓重建（离线、不花钱、不需要确认）。

安全边界不变（`REQ-012` 的约束）：面板通过 `datalayer` 的读接口访问仓，
`REQ-014` 的清单写入由独立动作插件通过 `Universe` 编排，
不接受任何用户提供的路径；仓根本身来自配置（`WEBUI_ARCHIVE_ROOT` / `TURTLE_ARCHIVE_ROOT`），
不是 URL 参数。
"""

from __future__ import annotations

from pathlib import Path

from ..core.errors import WebUIError
from ..core.models import NavItem, PanelSpec, Param
from .companies import display_name, display_name_from_label

_GAP_COLUMNS = [
    {"key": "ticker", "title": "标的", "search": True, "sort": "text"},
    {"key": "period", "title": "期次", "search": True, "sort": "text"},
    {"key": "dataset", "title": "数据集", "search": True, "sort": "text"},
    {"key": "result", "title": "结果", "search": True, "sort": "text"},
    {"key": "reason", "title": "原因（接口原文摘要）", "search": True},
]


def _store(ctx):
    from datalayer.store import DataStore

    try:
        return DataStore(ctx.config.archive_root)
    except Exception:  # noqa: BLE001（仓不可用 = 还没拉过数据，是正常状态）
        return None


def _universe(ctx):
    store = _store(ctx)
    if store is None:
        return []
    try:
        from datalayer.universe import Universe

        return Universe(store).entries()
    except Exception:  # noqa: BLE001
        return []


def _canonical_names(ctx) -> dict:
    """`{TICKER: 规范显示名}`——取自产物索引（`companies.index` 的 `display_name`）。"""
    try:
        from .companies import companies_dataset

        entries = companies_dataset(ctx)[0]["companies"]
    except Exception:  # noqa: BLE001（产物索引不可用不影响清单本身）
        return {}
    names = {}
    for item in entries:
        ticker = str(item.get("ticker") or "").strip().upper()
        if ticker and item.get("display_name"):
            names[ticker] = item["display_name"]
    return names


def _records(ctx, **filters) -> list:
    store = _store(ctx)
    if store is None:
        return []
    try:
        return store.records(include_rows=False, **filters)
    except Exception:  # noqa: BLE001
        return []


def _universe_panel(ctx, **_):
    """自选股清单（`AC-4.1`）：每行一个标的 + 仓内的完备度。

    显示名列用**规范显示名**（`AC-9`）：清单自己只存 `display_name`（用户登记什么就是
    什么），产物里那条记录才是「公司 + 代码」的权威来源；两边都在时以产物为准，
    否则同一家公司在数据页与公司页会显示成两个名字。
    """
    entries = _universe(ctx)
    store = _store(ctx)
    canonical = _canonical_names(ctx)
    rows = []
    for entry in entries:
        ticker = str(entry.get("ticker") or "")
        try:
            counts = store.completeness(ticker=ticker)["counts"] if store is not None else {}
        except Exception:  # noqa: BLE001
            counts = {}
        total = int(counts.get("total", 0) or 0)
        complete = int(counts.get("complete", 0) or 0)
        gaps = total - complete
        rows.append({
            "ticker": ticker,
            # 清单里的显示名是用户登记什么就是什么；产物里有这家公司时以产物为准
            # （`AC-9` 只认一个来源），两者都没有才按目录名约定拼。
            "company": canonical.get(ticker.upper())
            or display_name_from_label(entry.get("display_name"))
            or display_name(ticker, ""),
            "market": entry.get("market", ""),
            "tier": entry.get("tier", ""),
            "enabled": "启用" if entry.get("enabled") else "停用",
            "completeness": f"{complete}/{total}" if total else "还没有数据",
            "gaps": gaps,
            "note": entry.get("note", ""),
        })
    rows.sort(key=lambda item: item["ticker"])
    guide = ""
    if not rows:
        guide = ("自选股清单还是空的。先在本页点击「添加公司」，或「从既有产物导入」登记清单，"
                 "再执行「拉取全部数据」。")
    return {
        "columns": [
            {"key": "company", "title": "公司", "search": True, "sort": "text"},
            {"key": "ticker", "title": "标的", "search": True, "sort": "text"},
            {"key": "market", "title": "市场", "sort": "text"},
            {"key": "enabled", "title": "状态", "search": True, "sort": "text"},
            {"key": "completeness", "title": "已有记录完成数（非计划完备度）", "sort": "text"},
            {"key": "gaps", "title": "缺口", "sort": "number"},
            {"key": "note", "title": "备注", "search": True},
        ],
        "rows": rows,
        "meta": {"count": len(rows), "empty": not rows, "guide": guide},
        "guide": guide,
    }


def _gaps_panel(ctx, ticker=None, period=None, dataset=None, **_):
    """缺口下钻（`AC-4.1` / `AC-4.3`）：三个可选过滤参数，全部来自下拉而不是手敲路径。"""
    filters = {}
    if ticker:
        filters["ticker"] = ticker
    if period:
        filters["period"] = period
    if dataset:
        filters["dataset"] = dataset
    records = _records(ctx, **filters)
    gaps = [
        {
            "ticker": item.get("ticker", ""),
            "period": item.get("period", ""),
            "dataset": item.get("dataset", ""),
            "result": item.get("result", ""),
            "reason": item.get("error_excerpt") or "",
        }
        for item in records
        if item.get("result") not in ("ok", "empty")
    ]
    gaps.sort(key=lambda item: (item["ticker"], item["period"], item["dataset"]))
    filters_state = {"ticker": ticker or "", "period": period or "", "dataset": dataset or ""}
    return {
        "columns": _GAP_COLUMNS,
        "rows": gaps,
        "meta": {
            "count": len(gaps),
            "total": len(records),
            "filters": filters_state,
            "empty_hint": ("尚未采集：请检查目标计划，未尝试目标仍属缺口。" if not records
                           else "已有记录没有缺口；分析完备度请核对上方完整目标计划。") if not gaps else "",
        },
    }


def _store_panel(ctx, **_):
    """存储概览：仓的规模与新鲜度（`AC-4.1` 的「存储概览」）。"""
    store = _store(ctx)
    if store is None:
        return {
            "columns": [{"key": "item", "title": "项"}, {"key": "value", "title": "值"}],
            "rows": [],
            "meta": {"empty": True,
                     "guide": "还没有原始仓。执行一次「拉取全部数据」后这里会显示仓的规模。"},
        }
    try:
        stats = store.stats()
    except Exception as exc:  # noqa: BLE001
        return {
            "columns": [{"key": "item", "title": "项"}, {"key": "value", "title": "值"}],
            "rows": [{"item": "原始仓不可用", "value": f"{type(exc).__name__}"}],
            "meta": {"empty": True},
        }
    if not stats.get("records") and not stats.get("universe"):
        return {
            "columns": [{"key": "item", "title": "项"}, {"key": "value", "title": "值"}],
            "rows": [],
            "meta": {"empty": True, "store": stats.get("store", ""),
                     "guide": "原始仓还是空的（0 条记录）。先执行「拉取全部数据」，"
                              "或从既有存档导入之后，这里会显示仓的规模与新鲜度。"},
        }
    rows = [
        # 仓的位置对用户没有用（那是内部目录名），而且会暴露绝对路径；
        # 给「仓的标识」（目录名）与是否就绪就够了——要判断新鲜度看下面的时间与计数。
        {"item": "原始仓", "value": Path(str(stats.get("store", ""))).parent.name or "（未建立）"},
        {"item": "记录数", "value": stats.get("records", 0)},
        {"item": "占用字节", "value": stats.get("bytes", 0)},
        {"item": "写入格式版本", "value": stats.get("schema_version", "")},
        {"item": "清单标的数", "value": stats.get("universe", 0)},
        {"item": "批次数", "value": stats.get("batches", 0)},
    ]
    for result, count in sorted((stats.get("by_result") or {}).items()):
        rows.append({"item": f"结果 · {result}", "value": count})
    for period_type, count in sorted((stats.get("by_period_type") or {}).items()):
        rows.append({"item": f"期次类型 · {period_type}", "value": count})
    batches = []
    try:
        batches = store.list_batches(limit=5)
    except Exception:  # noqa: BLE001
        batches = []
    for batch in batches:
        rows.append({
            "item": f"最近批次 · {batch.get('batch_id', '')}",
            "value": f"{batch.get('status', '')}（{batch.get('profile', '')}）",
        })
    return {
        "columns": [{"key": "item", "title": "项", "search": True},
                    {"key": "value", "title": "值", "search": True}],
        "rows": rows,
        "meta": {"count": len(rows)},
    }


def pull_estimate(ctx, *, only_gaps: bool = False) -> dict:
    """「拉取全部数据」的调用量预估（`AC-4.2`：先给预估与确认，再显示进度）。

    预估在**服务端**算——直接调数据层自己的 `pull.plan` / `pull.estimate`，
    **不重新实现一份**（第二套实现必然漂移），所以 CI 能断言，前端不用猜。

    返回数据层 `estimate()` 的报告（`total` / `by_dataset` / `by_ticker` / …），
    外加 `requests` 作为「这一次会发出多少次请求」的直白字段（给确认文案用）。
    """
    store = _store(ctx)
    if store is None:
        return {}
    try:
        from datalayer.config import default_periods
        from datalayer.pull import plan

        # 期次口径与 CLI 一致（同一个 `default_periods`），否则「预估 40 次」与实际
        # 发出的请求数会对不上——预估的价值就在于它是**同一份计划**算出来的。
        planned = plan(_universe_adapter(ctx, store), periods=default_periods(),
                       only_gaps=only_gaps, store=store)
        report = dict(planned.get("estimate") or {})
        report["requests"] = int(report.get("total", 0))
        report["targets"] = int(report.get("total", 0))
        return report
    except Exception:  # noqa: BLE001（预估失败不该挡住动作本身；确认层会给通用文案）
        return {}


def _universe_adapter(ctx, store):
    from datalayer.universe import Universe

    return Universe(store)


def contribute(registry):
    from .watchlist import contribute as contribute_watchlist

    contribute_watchlist(registry)
    from .research_data import contribute as contribute_research

    contribute_research(registry)
    registry.panel(PanelSpec(
        id="data.universe", kind="table", title="自选股清单",
        provider=_universe_panel, size="full",
        options={"table": {"search": True, "sort": True, "page": 50}, "include_rows": True},
        description="清单是唯一事实来源；完备度按原始仓的实际情况算。",
    ))
    registry.panel(PanelSpec(
        id="data.gaps", kind="table", title="数据缺口",
        provider=_gaps_panel, size="full",
        options={"table": {"search": True, "sort": True, "page": 50}},
        params=(
            Param("ticker", type="string", source="selection.company"),
            Param("period", type="string"),
            Param("dataset", type="string"),
        ),
        description="按标的 / 期次 / 数据集下钻；附接口原文摘要，便于判断是权限、限频还是错误。",
    ))
    registry.panel(PanelSpec(
        id="data.store", kind="table", title="存储概览",
        provider=_store_panel, size="full",
        # 这是一张**两列的键值表**（指标 / 数值），排序没有意义：原先声明了 `sort: True`
        # 却没给任何列 `sort` 键，渲染出来 0 个可排序表头——正是第三轮那条缺陷的形状，
        # 门② 第八轮抓到的 F2。**去掉面板级声明**（而不是给键值表编造排序键）。
        options={"table": {"page": 50}},
        description="仓规模、各结果计数与最近批次；原始仓可整体拷走。",
    ))
    registry.panel(PanelSpec(
        id="data.actions", kind="actions", title="数据动作（联网 vs 离线）", render="client",
        endpoint="/api/v1/actions", provider=_actions_provider, size="full",
        description="「拉取」会联网并消耗配额、需要确认；「从原始仓重建」完全离线、不花钱。",
    ))
    registry.nav(NavItem(
        id="data", title="数据获取", group="数据", order=35, placement="context",
        actions=({"title":"采集批次与原始存档", "page":"collect"},),
        panels=("data.research", "data.actions", "data.universe", "data.gaps", "data.store"),
        description="清单 / 缺口 / 仓规模，以及「拉取」与「离线重建」两个动作。",
    ))


def _actions_provider(ctx, **_):
    from .commands import list_actions

    return list_actions(ctx)


__all__ = ["contribute", "pull_estimate"]
