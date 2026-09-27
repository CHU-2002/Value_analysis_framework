"""3 类股票图表（REQ-009.2 AC-2.4）：年度行情 / 关键财务指标 / 营收与归母净利。

数据全部来自**本地已落盘的 `data_pack_market.md`**——不联网（AC-3.5）、不重算业务数字，
只把它已经算好的表格转成前端能画的 `{labels, series}`。解析结果经数据层缓存：
同一份数据包第二次打开不再解析（AC-3.4）。

表格结构一旦变化（少一列、改标题），走的是**带错误码的失败**而不是「猜一个序列画出来」：
`ArtifactMissing`（找不到小节）/ `ParseFailed`（列对不上），由 `render/panels.py` 降级成可读卡片。
"""

from __future__ import annotations

from pathlib import Path

from ..core import envelope
from ..core.errors import ArtifactMissing
from ..core.models import DatasetSpec, NavItem, PanelSpec, Param
from ..datastore import parsers
from ..datastore.parsers import markdown_tables
from .companies import company_base

_METRIC_ORDER = ("ROE (%)", "毛利率 (%)", "净利率 (%)", "资产负债率 (%)")
MONTH_SECTION = "年度行情汇总"


def _read_first(sources: list) -> str:
    if not sources:
        raise ArtifactMissing("没有可读的数据包")
    return Path(sources[0]).read_text(encoding="utf-8")


def _records(text: str, section: str, *, numeric: bool = True) -> dict:
    return markdown_tables.table_as_records(
        markdown_tables.parse_rows(markdown_tables.find_section(text, section)), numeric=numeric
    )


def _periods_newest_first(records: dict) -> list:
    """除第一列（标签列）外的所有列，顺序保持数据包的「新 → 旧」。"""
    return [column["key"] for column in records["columns"][1:]]


def parse_annual_price(sources: list, params: dict) -> dict:
    """§11 的「年度行情汇总」子表：最高 / 最低 / 年末收盘（按年份升序）。"""
    records = _records(_read_first(sources), MONTH_SECTION)
    rows = list(reversed(records["rows"]))  # 数据包是「新→旧」，图上按时间从左到右
    return {
        "labels": [str(row.get("年度", "")) for row in rows],
        "series": [
            {"name": "年度最高", "values": [row.get("最高") for row in rows]},
            {"name": "年度最低", "values": [row.get("最低") for row in rows]},
            {"name": "年末收盘", "values": [row.get("年末收盘") for row in rows]},
        ],
        "unit": "元",
    }


def parse_metrics(sources: list, params: dict) -> dict:
    """§12 关键财务指标：ROE / 毛利率 / 净利率 / 资产负债率（按期次升序）。"""
    records = _records(_read_first(sources), "12.")
    periods = list(reversed(_periods_newest_first(records)))
    label_key = records["columns"][0]["key"]
    by_metric = {str(row.get(label_key, "")): row for row in records["rows"]}
    series = []
    for metric in _METRIC_ORDER:
        row = by_metric.get(metric)
        if row is None:
            continue
        series.append({"name": metric, "values": [row.get(period) for period in periods]})
    if not series:
        raise ArtifactMissing(
            "§12 关键财务指标里没有找到 ROE / 毛利率 / 净利率 / 资产负债率",
            hint="确认数据包小节是否改过标题或指标名。",
        )
    return {"labels": periods, "series": series, "unit": "%"}


def parse_revenue_profit(sources: list, params: dict) -> dict:
    """§3 合并利润表：营业收入与归母净利润按期次对比（升序）。"""
    records = _records(_read_first(sources), "3.")
    periods = list(reversed(_periods_newest_first(records)))
    label_key = records["columns"][0]["key"]
    by_item = {str(row.get(label_key, "")): row for row in records["rows"]}
    series = []
    for item, label in (("营业收入", "营业收入"), ("归母净利润", "归母净利润")):
        row = by_item.get(item)
        if row is not None:
            series.append({"name": label, "values": [row.get(period) for period in periods]})
    if not series:
        raise ArtifactMissing(
            "§3 合并利润表里没有找到营业收入 / 归母净利润",
            hint="确认数据包小节的表头是否变化。",
        )
    return {"labels": periods, "series": series, "unit": "百万元"}


def register_parsers() -> None:
    parsers.register_parser("charts.annual_price", parse_annual_price, replace=True)
    parsers.register_parser("charts.metrics", parse_metrics, replace=True)
    parsers.register_parser("charts.revenue_profit", parse_revenue_profit, replace=True)


CHART_DATASETS = ("charts.annual_price", "charts.metrics", "charts.revenue_profit")


def charts_for(ctx, company_dir: str) -> tuple:
    base = company_base(ctx, company_dir)
    charts: dict = {}
    meta: dict = {}
    for name in CHART_DATASETS:
        data, item_meta = ctx.registry.datastore.get(name, base=base, params={})
        charts[name] = data
        meta[name] = item_meta
    return charts, meta


def _chart_panel(dataset: str):
    def provider(ctx, company=None, **_):
        base = company_base(ctx, company)
        data, meta = ctx.registry.datastore.get(dataset, base=base, params={})
        return {**data, "meta": meta}

    return provider


def _charts_route(ctx, dir, **_):
    charts, meta = charts_for(ctx, dir)
    return envelope.ok({"dir": dir, "charts": charts}, meta=meta)


def contribute(registry):
    register_parsers()
    for name in CHART_DATASETS:
        registry.dataset(DatasetSpec(
            name=name, sources=("data_pack_market.md",), parser=name, parser_version=1,
        ))
    registry.panel(PanelSpec(
        id="charts.annual_price", kind="chart", title="年度股价走势", size="half",
        options={"chart": {"type": "line", "x": "labels", "series": "series"}},
        provider=_chart_panel("charts.annual_price"),
        params=(Param("company", type="company", source="selection.company"),),
        description="来自 data_pack_market.md §11 年度行情汇总；断网也能看。",
    ))
    registry.panel(PanelSpec(
        id="charts.metrics", kind="chart", title="关键财务指标趋势", size="half",
        options={"chart": {"type": "line", "x": "labels", "series": "series"}},
        provider=_chart_panel("charts.metrics"),
        params=(Param("company", type="company", source="selection.company"),),
        description="来自 §12：ROE / 毛利率 / 净利率 / 资产负债率。",
    ))
    registry.panel(PanelSpec(
        id="charts.revenue_profit", kind="chart", title="营收与归母净利润", size="full",
        options={"chart": {"type": "bar", "x": "labels", "series": "series"}},
        provider=_chart_panel("charts.revenue_profit"),
        params=(Param("company", type="company", source="selection.company"),),
        description="来自 §3 合并利润表。",
    ))
    registry.nav(NavItem(
        id="charts", title="图表", group="浏览", order=20,
        panels=("charts.annual_price", "charts.metrics", "charts.revenue_profit"),
    ))
    registry.route("GET", "/api/v1/companies/{dir}/charts", _charts_route, name="company charts")


__all__ = [
    "contribute", "parse_annual_price", "parse_metrics", "parse_revenue_profit",
    "CHART_DATASETS", "charts_for",
]
