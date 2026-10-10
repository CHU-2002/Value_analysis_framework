"""3 类股票图表（`REQ-009.2` AC-2.4；口径修正见 `REQ-012.3` 的 `AC-7`）。

数据全部来自**本地已落盘的 `data_pack_market.md`**——不联网（AC-3.5）、不重算业务数字，
只把它已经算好的表格转成前端能画的 `{labels, series}`。解析结果经数据层缓存：
同一份数据包第二次打开不再解析（AC-3.4）。

## 口径（`REQ-012.3` 的 `AC-7`：这是本片修掉的那个会误导人的缺陷）

数据包的表头把「年度 / 半年 / 单季」三种口径**写在同一行**：

    | 项目 (百万元) | 2026H1 | 2026Q1 | 2025H1 | 2025Q1 | 2025 | 2024 | 2023 … |

旧实现只做 `reversed()` 就画，于是营收曲线出现「115,636 → 32,938 → 61,776」的锯齿——
那不是经营波动，是**口径混用**（年度累计值、半年累计值、单季值被画在同一根轴上）。

修法是让口径成为**一等数据**：

- 从列标签解析（`2025` → 年度、`2025H1` → 半年、`2025Q1` → 单季）——表头本来就无歧义；
- 按口径**分组**输出（`series[].basis` / `series[].cumulative`），并给出 `bases` 元数据；
- 同一口径内部累计与单期仍然不同轴（`cumulative` 为假时另起一组），
  所以「半年累计」与「单季」不会互相冒充；
- 默认只画年度，其余口径由界面上的切换器显式选择（`options.chart.toolbar.basis`）。

表格结构一旦变化（少一列、改标题），走的是**带错误码的失败**而不是「猜一个序列画出来」：
`ArtifactMissing`（找不到小节）/ `ParseFailed`（列对不上），由 `render/panels.py` 降级成可读卡片。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core import envelope
from ..core.errors import ArtifactMissing, NotFound
from ..core.models import DatasetSpec, NavItem, PanelSpec, Param
from ..datastore import parsers
from ..datastore.parsers import markdown_tables
from .companies import company_base, context_resolvable
from .research_timeline import cached_financial_income, load_timeline, register_research_datasets

_METRIC_ORDER = ("ROE (%)", "毛利率 (%)", "净利率 (%)", "资产负债率 (%)")
MONTH_SECTION = "年度行情汇总"

#: 口径枚举（`AC-7` 的「必须能按口径查看」）。
BASES = ("annual", "half", "quarter")
BASIS_LABELS = {"annual": "年度", "half": "半年", "quarter": "单季"}
#: 默认口径：年度。混着看是旧缺陷，所以默认**只画一种**。
DEFAULT_BASIS = "annual"
#: 单期口径的说明（累计 vs 单期，`AC-7` 要求在图上标明）。
BASIS_KIND = {
    "annual": "累计（全年）",
    "half": "累计（半年）",
    "quarter": "单期（单季）",
}

_PERIOD_RE = re.compile(r"^(\d{4})\s*(?:[-/]?\s*(H1|H2|Q[1-4]|FY))?$", re.IGNORECASE)


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


# --------------------------------------------------------------- 口径


def basis_of(period: str) -> str:
    """期次标签 → 口径。解析不出来时按**年度**处理（裸年份是数据包里最常见的形态）。"""
    match = _PERIOD_RE.match(str(period or "").strip())
    if not match:
        return "annual"
    suffix = (match.group(2) or "").upper()
    if suffix in ("H1", "H2"):
        return "half"
    if suffix.startswith("Q"):
        return "quarter"
    return "annual"


def cumulative_of(basis: str) -> bool:
    """年度与半年是**累计**口径，单季是**单期**口径。"""
    return basis in ("annual", "half")


def _series_entry(name: str, basis: str, values: list) -> dict:
    return {
        "name": name,
        "values": values,
        "basis": basis,
        "cumulative": cumulative_of(basis),
    }


def _group_by_basis(periods: list) -> dict:
    """按口径分组：`{口径: [期次…]}`，组内按时间升序（数据包原本是「新→旧」）。

    口径本身就是「累计 vs 单期」的划分：年度与半年是累计、单季是单期
    （见 `cumulative_of`），所以按口径分组即可，不需要再分一层。
    """
    grouped: dict = {}
    for period in periods:            # 数据包是「新→旧」，这里翻成「旧→新」
        grouped.setdefault(basis_of(period), []).append(period)
    for items in grouped.values():
        items.sort(key=lambda text: str(text))
    return grouped


def _basic_payload(section_records: dict, item_names, periods_newest_first, unit: str) -> dict:
    """公共装配：一行一个指标 → 每组口径一根序列；默认口径只有一组。"""
    periods = list(reversed(periods_newest_first))
    label_key = section_records["columns"][0]["key"]
    by_item = {str(row.get(label_key, "")): row for row in section_records["rows"]}
    grouped = _group_by_basis(periods)
    bases = [basis for basis in BASES if basis in grouped]
    series = []
    for basis in bases:
        labels = grouped[basis]
        for item, display in item_names:
            row = by_item.get(item)
            if row is None:
                continue
            # 同一口径里累计与单期不混：`cumulative` 已经是该口径的固有属性，
            # 所以这里按口径分组即可（年度/半年=累计，单季=单期）。
            series.append(_series_entry(
                f"{display}（{BASIS_LABELS[basis]}）" if len(bases) > 1 else display,
                basis, [row.get(period) for period in labels],
            ))
    if not series:
        raise ArtifactMissing(
            "这些小节里没有找到要画的指标",
            hint="确认数据包小节的表头与指标名是否改过。",
        )
    default_labels = grouped.get(DEFAULT_BASIS) or grouped[bases[0]]
    return {
        "labels": default_labels,
        "series": series,
        "unit": unit,
        "basis": DEFAULT_BASIS if DEFAULT_BASIS in grouped else bases[0],
        "bases": bases,
        "basis_labels": {basis: BASIS_LABELS[basis] for basis in bases},
        "basis_kind": {basis: BASIS_KIND[basis] for basis in bases},
        "labels_by_basis": {basis: grouped[basis] for basis in bases},
        "series_by_basis": {
            basis: [index for index, s in enumerate(series) if s["basis"] == basis]
            for basis in bases
        },
    }


# --------------------------------------------------------------- 数据集


def parse_annual_price(sources: list, params: dict) -> dict:
    """§11 的「年度行情汇总」子表：最高 / 最低 / 年末收盘（按年份升序）。

    这张表本身就是**年度**口径，所以只有一组；
    仍然带上 `basis` 元数据，前端不必对每个图特判。
    """
    records = _records(_read_first(sources), MONTH_SECTION)
    rows = list(reversed(records["rows"]))  # 数据包是「新→旧」，图上按时间从左到右
    labels = [str(row.get("年度", "")) for row in rows]
    return {
        "labels": labels,
        "series": [
            _series_entry("年度最高", "annual", [row.get("最高") for row in rows]),
            _series_entry("年度最低", "annual", [row.get("最低") for row in rows]),
            _series_entry("年末收盘", "annual", [row.get("年末收盘") for row in rows]),
        ],
        "unit": "元",
        "basis": "annual",
        "bases": ["annual"],
        "basis_labels": {"annual": BASIS_LABELS["annual"]},
        "basis_kind": {"annual": BASIS_KIND["annual"]},
        "labels_by_basis": {"annual": labels},
        "series_by_basis": {"annual": [0, 1, 2]},
    }


def parse_metrics(sources: list, params: dict) -> dict:
    """§12 关键财务指标：ROE / 毛利率 / 净利率 / 资产负债率（按期次升序、按口径分组）。"""
    records = _records(_read_first(sources), "12.")
    payload = _basic_payload(
        records,
        tuple((metric, metric) for metric in _METRIC_ORDER),
        _periods_newest_first(records),
        "%",
    )
    payload["missing_reasons"] = {}
    for item in payload["series"]:
        if item["basis"] == "quarter":
            for index, label in enumerate(payload["labels_by_basis"]["quarter"]):
                if not str(label).upper().endswith("Q1"):
                    item["values"][index] = None
                    payload["missing_reasons"][str(label)] = "累计财务比率不能直接相减换成单季；缺少单季计算依据"
    payload["source_note"] = "财报比率按报告期口径；Q1为首季，其他累计比率缺少单季计算依据时不画值。"
    return payload


def parse_revenue_profit(sources: list, params: dict) -> dict:
    """§3 合并利润表：营业收入与归母净利润（口径分组，默认年度）。

    旧实现的锯齿（`115,636 → 32,938 → 61,776`）就出在这里：年度累计、半年累计与单季
    被画在同一条序列上。现在它们分属不同 `basis`，`AC-7` 的判据（序列数 ≥2 或带显式
    口径切换状态）由 `bases` / `series_by_basis` 直接可断言。
    """
    records = _records(_read_first(sources), "3.")
    payload = _basic_payload(
        records,
        (("营业收入", "营业收入"), ("归母净利润", "归母净利润")),
        _periods_newest_first(records),
        "百万元",
    )
    label_key = records["columns"][0]["key"]
    by_name = {str(row.get(label_key, "")): row for row in records["rows"]}
    payload["missing_reasons"] = {}
    for item in payload["series"]:
        if item["basis"] != "quarter":
            continue
        original = by_name.get(item["name"].split("（")[0], {})
        for index, period in enumerate(payload["labels_by_basis"]["quarter"]):
            match = _PERIOD_RE.match(str(period))
            suffix = match.group(2).upper() if match else ""
            if suffix == "Q1":
                continue
            year = match.group(1)
            prior = {"Q2": [year + "Q1"], "Q3": [year + "H1", year + "Q2"], "Q4": [year + "Q3"]}.get(suffix, [])
            preceding = next((original[key] for key in prior if original.get(key) is not None), None)
            value = original.get(period)
            item["values"][index] = value - preceding if isinstance(value, (float, int)) and isinstance(preceding, (float, int)) else None
            if item["values"][index] is None:
                payload["missing_reasons"][str(period)] = "缺少上一季度累计值，不能换算单季"
    payload["source_note"] = "利润表累计值按相邻季度差额换算单季；Q1无需换算。缺少依据留缺。"
    return payload


def register_parsers() -> None:
    parsers.register_parser("charts.annual_price", parse_annual_price, replace=True)
    parsers.register_parser("charts.metrics", parse_metrics, replace=True)
    parsers.register_parser("charts.revenue_profit", parse_revenue_profit, replace=True)


CHART_DATASETS = ("charts.annual_price", "charts.metrics", "charts.revenue_profit")


def charts_for(ctx, company_dir: str, *, basis: str = "") -> tuple:
    base = company_base(ctx, company_dir)
    charts: dict = {}
    meta: dict = {}
    for name in CHART_DATASETS:
        data, item_meta = ctx.registry.datastore.get(name, base=base, params={})
        if basis:
            data = filter_basis(data, basis)
        charts[name] = data
        meta[name] = item_meta
    return charts, meta


def filter_basis(data: dict, basis: str) -> dict:
    """按口径裁剪：只留该口径的序列与标签（`AC-7` 的「必须能按口径查看」）。

    服务端做这件事而不是前端：裁剪后的数据仍是**可断言的纯数据**，
    前端只画它拿到的序列（前端不做业务判断，见「反模式」一节）。

    口径元数据由**数据集自己**给全（`parse_annual_price` 天生只有年度，它也带一套单口径
    元数据）；这里只负责裁剪，所以**未知口径原样返回**，不编造元数据。
    """
    if basis not in BASES or not data.get("series_by_basis"):
        return data
    if basis not in data["series_by_basis"]:
        return {**data, "basis": basis, "series": [], "labels": [],
                "empty_hint": f"{BASIS_LABELS[basis]}口径在这份数据包里没有期次。"}
    indices = data["series_by_basis"][basis]
    series = [data["series"][index] for index in indices]
    return {
        **data,
        "basis": basis,
        "labels": data["labels_by_basis"][basis],
        "series": series,
        # **裁剪后必须重算索引**：留着指向未裁剪列表的旧下标，任何按
        # `series[series_by_basis[basis][0]]` 取数的消费者都会 IndexError
        # （实测：面板参数默认就是 annual，所以真实接口返回的载荷本来就已经裁剪过）。
        "series_by_basis": {basis: list(range(len(series)))},
        "filtered": True,
    }


def _chart_panel(dataset: str):
    def provider(ctx, company=None, basis=None, **_):
        if dataset == "charts.revenue_profit" and company:
            income, income_meta = cached_financial_income(ctx, company)
            if income["available"]:
                return {**filter_basis(income, basis or DEFAULT_BASIS), "meta": income_meta, "company": company}
        try:
            base = company_base(ctx, company)
            data, meta = ctx.registry.datastore.get(dataset, base=base, params={})
        except (ArtifactMissing, NotFound):
            # A missing pack is a normal empty state; a present but malformed pack
            # must retain its per-panel diagnostic rather than hide a regression.
            if "base" in locals() and (base / "data_pack_market.md").is_file():
                raise
            return {"labels": [], "series": [], "unit": "%" if dataset == "charts.metrics" else "百万元",
                    "empty_hint": "尚无财务数据包；请在数据页更新数据并离线重建。", "company": company}
        if basis:
            data = filter_basis(data, basis)
        return {**data, "meta": meta, "company": company}

    return provider


def _charts_route(ctx, ticker=None, **_):
    charts, meta = charts_for(ctx, ticker or ctx.query.get("company"), basis=ctx.query.get("basis", ""))
    return envelope.ok({"company": ticker or ctx.query.get("company"), "charts": charts}, meta=meta)


def _timeline_panel(ctx, company=None, **_):
    settings = {key: ctx.query.get(key, default) for key, default in
                (("cycle", "day"), ("window", "5"), ("adjustment", "none"),
                 ("range", "1"), ("start", ""), ("end", ""))}
    return load_timeline(ctx, company or ctx.query.get("company"), **settings)


def _timeline_route(ctx, **_):
    return envelope.ok(_timeline_panel(ctx, company=ctx.query.get("company")))


def _chart_context(ctx, value):
    if context_resolvable(ctx, value):
        return True
    # Universe companies without derived output still have a normal empty research chart.
    from .companies import companies_dataset
    return any(item.get("ticker") == value for item in companies_dataset(ctx)[0]["companies"])


def contribute(registry):
    register_research_datasets(registry)
    register_parsers()
    for name in CHART_DATASETS:
        # `parser_version` 3：REQ-015.1 修正累计季度的单季换算。
        # 数据层的缓存键含 parser_version，所以老缓存会自动失效——不升版本的话
        # 界面会继续拿到「没有口径元数据」的旧结果（实测：接口返回 basis=None）。
        registry.dataset(DatasetSpec(
            name=name, sources=("data_pack_market.md",), parser=name, parser_version=3,
        ))
    registry.panel(PanelSpec(
        id="charts.timeline", kind="chart", title="行情与估值时间轴",
        provider=_timeline_panel,
        params=(Param("company", type="company", source="selection.company"),),
        description="真实 OHLC / 每日 PE 与 PB；离线查看，不自动补历史。",
    ))
    registry.route("GET", "/api/v1/research/charts", _timeline_route, name="research timeline")
    chart_options = {
        "chart": {
            "type": "line",
            "x": "labels",
            "series": "series",
            # 口径切换器 + 单位/口径标注（AC-7）。
            "toolbar": {"basis": True, "unit": True, "export": True},
        }
    }
    registry.panel(PanelSpec(
        id="charts.annual_price", kind="chart", title="旧数据包年度行情汇总（非 K 线）",
        provider=_chart_panel("charts.annual_price"),
        params=(Param("company", type="company", source="selection.company"),),
        description="来自 data_pack_market.md §11 年度行情汇总；断网也能看。",
    ))
    registry.panel(PanelSpec(
        id="charts.metrics", kind="chart", title="关键财务指标趋势",
        provider=_chart_panel("charts.metrics"),
        params=(
            Param("company", type="company", source="selection.company"),
            Param("basis", type="enum", choices=BASES, default=DEFAULT_BASIS),
        ),
        options=chart_options,
        description="来自 §12：ROE / 毛利率 / 净利率 / 资产负债率；默认只看年度口径。",
    ))
    registry.panel(PanelSpec(
        id="charts.revenue_profit", kind="chart", title="营收与归母净利润",
        provider=_chart_panel("charts.revenue_profit"),
        params=(
            Param("company", type="company", source="selection.company"),
            Param("basis", type="enum", choices=BASES, default=DEFAULT_BASIS),
        ),
        options=chart_options,
        description="来自 §3 合并利润表。年度累计 / 半年累计 / 单季分开画，不混在一根轴上。",
    ))
    registry.nav(NavItem(
        id="charts", title="行情与估值", placement="context",
        actions=({"title":"更新数据", "page":"data"}, {"title":"生成报告", "page":"agent"}), group="公司", order=20,
        panels=("charts.timeline", "charts.metrics", "charts.revenue_profit", "charts.annual_price"),
        requires=("selection.company",),
        context_resolver=_chart_context,
        description="看这家公司的趋势图；口径可切换，缺数据的地方断开而不是画成 0。",
    ))
    registry.route("GET", "/api/v1/companies/{ticker}/charts", _charts_route, name="company charts")


__all__ = [
    "contribute", "parse_annual_price", "parse_metrics", "parse_revenue_profit",
    "CHART_DATASETS", "charts_for", "basis_of", "cumulative_of", "filter_basis",
    "BASES", "DEFAULT_BASIS",
]
