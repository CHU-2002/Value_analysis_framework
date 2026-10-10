# 覆盖需求：REQ-012（父需求 AC-6 数据新鲜度可见、AC-7 图表口径正确且可读、AC-8 表格可用、
# AC-10 面向用户的错误）、REQ-012.3（视图质量与口径修正）—— AC-3.1 趋势图不混口径、可切换
# 口径并标明单位与累计/单期、AC-3.2 按容器宽度与 DPR 绘制、标签/图例不截断、缺失值有标注、
# 支持导出、AC-3.3 数据新鲜度可见且「刷新视图」与「重新拉取」语义分离、AC-3.4 列表搜索/排序/
# 分页做在通用渲染层、AC-3.5 错误与降级文案符合 AC-10。
"""控制台视图质量测试（`REQ-012.3`）。

四条刻意的手法：

1. **数据包是唯一输入**：每条断言都从 `tmp_path` 里手写的数据包出发，不读真实 `output/`、
   不联网；口径（年度/半年/单季）就写在表头上，所以「有没有混口径」可以纯数据断言；
2. **口径用独立判据交叉验证**：`series_by_basis` 给的每根序列，其标签必须与 `basis_of`
   算出来的口径一致（两条实现路径互证）——不是把实现里的字面量抄一遍；
3. **前端只能源码级断言**（不引 JS 运行器，与 `REQ-009.3` 的 `AC-7` 同一取舍）：DPR、
   实测宽度抽稀、缺失值断开、导出这些**绘制质量**契约钉在 `kinds/chart_core.js` 的源码上，
   通用表格行为钉在 `kinds/table.js` 上；像素级现象由浏览器走查覆盖；
4. **接线也要断**：服务端契约（`data-table-controls`、`panel.meta`）一律断言**真实接口的
   载荷**，不只断言「渲染函数拿着正确输入能产出正确输出」——「声明了但没接上」正是
   本片踩过的坑。实测到的实现缺陷**先修、再固化成硬断言**（本片交付时抓到三条：`meta` 没提到
   载荷顶层、表格控件标记不可达、`filter_basis` 裁剪后索引越界——三条都已修，所以这个文件里
   **没有** `xfail`）；将来若出现当次修不掉的缺陷，用 `pytest.xfail` 带原因暴露，
   不为了让文件变绿而删断言、放宽断言或 `skip`。
"""

import json
import re
from pathlib import Path

import pytest

from webui.config import Config
from webui.core.context import RequestContext
from webui.core.errors import BadRequest, InvalidParam, NotFound
from webui.core.models import PanelSpec
from webui.core.registry import build_registry
from webui.core.router import find_route
from webui.core.routes import install_core_routes
from webui.datastore import DataStore, parsers
from webui.plugins import actions as actions_plugin
from webui.plugins import charts as charts_plugin
from webui.plugins import collect as collect_plugin
from webui.plugins import commands as commands_plugin
from webui.plugins import companies as companies_plugin
from webui.plugins import data_page as data_page_plugin
from webui.plugins import home as home_plugin
from webui.plugins import run_history as run_history_plugin
from webui.render import panels as panel_render

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC = REPO_ROOT / "scripts" / "webui" / "static"

REPORT_MD = """# 伊利股份 2025 年报分析

## 结论

- 营收微增，利润率稳定。
"""

#: `AC-7` 的判据数据：表头把年度 / 半年 / 单季三种口径写在同一行（`REQ-012.3` 要修的就是它）。
MIXED_PACK = """
## 3. 合并利润表

| 项目 (百万元) | 2026H1 | 2026Q1 | 2025H1 | 2025Q1 | 2025 | 2024 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 营业收入 | 61,776 | 32,938 | 115,636 | 59,000 | 121,000 | 110,000 |
| 归母净利润 | 7,000 | 3,500 | 12,000 | 6,000 | 13,000 | 11,000 |

## 12. 关键财务指标

| 指标 | 2026H1 | 2026Q1 | 2025H1 | 2025Q1 | 2025 | 2024 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ROE (%) | 10.0 | 5.0 | 18.0 | 9.0 | 19.0 | 17.0 |
| 毛利率 (%) | 30.0 | 29.0 | 31.0 | 30.0 | 32.0 | 31.0 |
| 净利率 (%) | 8.0 | 7.0 | 9.0 | 8.0 | 9.5 | 9.0 |
| 资产负债率 (%) | 55.0 | 54.0 | 56.0 | 55.0 | 57.0 | 56.0 |

## 11. 十年周线行情

### 年度行情汇总

| 年度 | 最高 | 最低 | 年末收盘 |
| --- | ---: | ---: | ---: |
| 2025 | 32.00 | 22.00 | 28.00 |
| 2024 | 30.00 | 20.00 | 26.00 |
"""

#: 只有年度期次的数据包（用来验「某个口径在这份数据里不存在」的空状态）。
ANNUAL_ONLY_PACK = """
## 3. 合并利润表

| 项目 (百万元) | 2025 | 2024 |
| --- | ---: | ---: |
| 营业收入 | 121,000 | 110,000 |
| 归母净利润 | 13,000 | 11,000 |
"""

#: 缺小节的数据包：走**带错误码的失败**，而不是「猜一个序列画出来」。
BROKEN_PACK = """
# 只有标题的数据包

正文里没有图表需要的小节。
"""


# --------------------------------------------------------------- 测试辅助


def make_config(tmp_path: Path) -> Config:
    return Config(
        host="127.0.0.1", port=0,
        output_root=tmp_path / "output", cache_dir=tmp_path / "output" / ".webui_cache",
        archive_root=tmp_path / "archive",
    )


def make_app(tmp_path: Path):
    config = make_config(tmp_path)
    registry = build_registry()
    install_core_routes(registry, config)
    registry.config = config
    for module in (collect_plugin, commands_plugin, companies_plugin, charts_plugin,
                   run_history_plugin, home_plugin, data_page_plugin, actions_plugin):
        module.contribute(registry)
    registry.datastore = DataStore(config, spec_lookup=registry.dataset_spec)
    return config, registry


def call_route(registry, method: str, path: str, **query):
    route, params = find_route(registry.routes(), method, path)
    assert route is not None, f"没有匹配的路由：{method} {path}"
    context = RequestContext(
        method=method, path=path, query=dict(query), config=registry.config, registry=registry
    )
    return route.handler(context, **params)


def panel_columns(registry, panel_id: str, **query) -> list:
    """面板 provider 声明的列（不渲染，只看契约）。"""
    from webui.core.context import RequestContext as Ctx
    from webui.core.routes import resolve_params

    spec = registry.panel_spec(panel_id)
    context = Ctx(method="GET", path=f"/api/v1/panels/{panel_id}",
                  query={"company": "600887.SH", **query},
                  config=registry.config, registry=registry)
    return spec.provider(context, **resolve_params(spec, context))["columns"]


def panel_payload(registry, panel_id: str, **query):
    """面板接口的渲染载荷（`kind=table` 带 html，`kind=chart` 带 data）。"""
    return call_route(registry, "GET", f"/api/v1/panels/{panel_id}", **query)["data"]


def company_dir(tmp_path: Path, pack: str = MIXED_PACK, name: str = "600887_伊利",
                ticker: str = "600887.SH", company: str = "伊利股份") -> Path:
    """一份最小但完整的数据包目录：数据包 / 公司记录 / run 指针 / 可读报告。"""
    directory = tmp_path / "output" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "data_pack_market.md").write_text(pack, encoding="utf-8")
    (directory / "qualitative_report.md").write_text(REPORT_MD, encoding="utf-8")
    (directory / "record.json").write_text(
        json.dumps({"subject": {"ticker": ticker, "company": company},
                    "downstream": {"stale": False}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (directory / "latest.json").write_text(
        json.dumps({"run_id": "20260101T000000000000Z", "primary_period": "2025FY"}),
        encoding="utf-8",
    )
    return directory


def static_source(relative: str) -> str:
    return (STATIC / relative).read_text(encoding="utf-8")


def js_function_body(source: str, signature: str) -> str:
    """取一个**顶层** JS 函数的函数体（按顶格 `}` 切），只服务于源码级断言。

    与 `tests/test_webui_framework.py::_js_function_body` 同一手法：前端没有 JS 运行器，
    而「按实测宽度抽稀」「缺失值断开」这类契约恰恰只能钉在源码上。函数的收尾 `}` 顶格，
    所以用 `\\n}\\n` 切割足够稳；格式若变，断言会响。
    """
    start = source.index(signature)
    return source[start: source.index("\n}\n", start) + 3]


@pytest.fixture(autouse=True)
def _isolated_parsers():
    parsers.reset_parsers()
    parsers.register_builtin_parsers()
    yield
    parsers.reset_parsers()
    parsers.register_builtin_parsers()


# --------------------------------------------------------------- AC-7 / AC-3.1 口径不混


def test_revenue_profit_keeps_annual_and_single_quarter_series_apart(tmp_path):
    """AC-7 / AC-3.1：混排表头必须按口径分组——旧缺陷「115,636 → 32,938 → 61,776」的判据。

    三层判据缺一不可：① 序列数 ≥2 且每根都带 `basis`/`cumulative`；② 每根序列里的期次
    口径与它声明的 `basis` **互相印证**（拿 `basis_of` 当独立判据，年度组里出现 `2026Q1`
    就会响）；③ 数值来自数据包对应期次，口径一旦回退到「混着画」这里立刻对不上。
    """
    source = company_dir(tmp_path) / "data_pack_market.md"
    data = charts_plugin.parse_revenue_profit([source], {})

    assert data["bases"] == ["annual", "half", "quarter"]
    assert len(data["bases"]) >= 2 and len(data["series"]) >= 2
    assert data["basis"] == charts_plugin.DEFAULT_BASIS == "annual"   # 默认只画年度
    for series in data["series"]:
        assert series["basis"] in data["bases"]
        assert series["cumulative"] is charts_plugin.cumulative_of(series["basis"])

    # ① 每根序列只拿自己口径的标签（`series_by_basis` 与 `labels_by_basis` 自洽）。
    for basis, indices in data["series_by_basis"].items():
        labels = data["labels_by_basis"][basis]
        assert labels, f"{basis} 组不该是空的"
        assert all(charts_plugin.basis_of(label) == basis for label in labels)
        for index in indices:
            series = data["series"][index]
            assert series["basis"] == basis
            assert len(series["values"]) == len(labels)
    # ② 分组是一个**划分**：每根序列恰好属于一个口径，没有序列被漏掉或重复。
    partition = [index for indices in data["series_by_basis"].values() for index in indices]
    assert sorted(partition) == list(range(len(data["series"])))
    assert set(data["series_by_basis"]) == set(data["bases"]) == set(data["labels_by_basis"])

    # ③ 值必须落在数据包写明的期次上（不是「看起来像」）。
    by_name = {series["name"]: series for series in data["series"]}
    assert data["labels_by_basis"]["annual"] == ["2024", "2025"]
    assert by_name["营业收入（年度）"]["values"] == [110000, 121000]
    assert data["labels_by_basis"]["half"] == ["2025H1", "2026H1"]
    assert by_name["营业收入（半年）"]["values"] == [115636, 61776]
    assert data["labels_by_basis"]["quarter"] == ["2025Q1", "2026Q1"]
    assert by_name["营业收入（单季）"]["values"] == [59000, 32938]
    # 顶层 labels 跟默认口径一致（前端不传 basis 时画的就是它）。
    assert data["labels"] == data["labels_by_basis"]["annual"]


def test_basis_of_and_cumulative_of_cover_period_labels_and_fall_back_to_annual():
    """AC-3.1：期次标签 → 口径的边界；解析不出来时退化成年度（裸年份是最常见形态）。"""
    assert charts_plugin.basis_of("2025") == "annual"
    assert charts_plugin.basis_of("2025H1") == "half"
    assert charts_plugin.basis_of("2025H2") == "half"
    assert charts_plugin.basis_of("2025Q1") == "quarter"
    assert charts_plugin.basis_of("2025Q3") == "quarter"
    assert charts_plugin.basis_of("2025FY") == "annual"
    assert charts_plugin.basis_of("  2024  ") == "annual"
    for weird in ("", None, "2025年", "2026-06-30", "FY2025", "乱七八糟"):
        assert charts_plugin.basis_of(weird) == "annual", f"{weird!r} 应退化成年度口径"
    # 累计/单期是口径的固有属性：年度与半年是累计，单季是单期。
    assert [charts_plugin.cumulative_of(basis) for basis in charts_plugin.BASES] == [True, True, False]
    assert charts_plugin.cumulative_of("不存在的口径") is False


def test_filter_basis_prunes_labels_and_series_and_never_raises(tmp_path):
    """AC-3.1 / AC-3.3：按口径裁剪时 `labels`/`series` 必须同步；缺该口径给可读空状态。"""
    source = company_dir(tmp_path) / "data_pack_market.md"
    data = charts_plugin.parse_revenue_profit([source], {})

    annual = charts_plugin.filter_basis(data, "annual")
    assert annual["basis"] == "annual"
    assert annual["labels"] == data["labels_by_basis"]["annual"]
    assert [series["basis"] for series in annual["series"]] == ["annual"] * 2
    assert all(len(series["values"]) == len(annual["labels"]) for series in annual["series"])

    quarter = charts_plugin.filter_basis(data, "quarter")
    assert quarter["basis"] == "quarter"
    assert quarter["labels"] == ["2025Q1", "2026Q1"]
    assert [series["name"] for series in quarter["series"]] == ["营业收入（单季）", "归母净利润（单季）"]
    # 裁剪不破坏元数据：可选项仍在（切换器据此列出全部口径）。
    assert quarter["bases"] == data["bases"]
    assert quarter["labels_by_basis"] == data["labels_by_basis"]

    # 数据里没有这个口径：空状态 + 人话提示，不抛错、也不悄悄换成别的口径。
    annual_only = charts_plugin.parse_revenue_profit(
        [company_dir(tmp_path, ANNUAL_ONLY_PACK, name="000001_平安",
                     ticker="000001.SZ", company="平安银行") / "data_pack_market.md"], {}
    )
    missing = charts_plugin.filter_basis(annual_only, "quarter")
    assert missing["series"] == [] and missing["labels"] == []
    assert missing["basis"] == "quarter"
    assert "单季" in missing["empty_hint"]
    # 完全未知的口径：原样返回（不裁剪成空图），仍不抛错。
    assert charts_plugin.filter_basis(data, "monthly") == data
    assert charts_plugin.filter_basis({}, "annual") == {}

    # 裁剪后 `series_by_basis` 必须**跟着重算**：它给的下标是裁剪结果的合法下标。
    # （曾经漏过：只重写 `labels`/`series`，旧下标越过裁剪后的列表，消费者 IndexError。
    # 面板参数默认就是 annual，所以真实接口返回的载荷本来就已裁剪，这个不一致直接可见。）
    assert quarter["series_by_basis"] == {"quarter": [0, 1]}
    assert quarter["series"][quarter["series_by_basis"]["quarter"][0]]["basis"] == "quarter"


def test_every_chart_dataset_carries_basis_metadata(tmp_path):
    """AC-7：三个数据集都返回同一套口径元数据（前端不必对某个图特判）。"""
    source = company_dir(tmp_path) / "data_pack_market.md"
    price = charts_plugin.parse_annual_price([source], {})
    assert price["basis"] == "annual" and price["bases"] == ["annual"]
    assert price["unit"] == "元"
    assert price["labels"] == ["2024", "2025"], "数据包是新→旧，图上是旧→新"
    assert price["series_by_basis"] == {"annual": [0, 1, 2]}
    assert [series["name"] for series in price["series"]] == ["年度最高", "年度最低", "年末收盘"]
    assert all(series["basis"] == "annual" and series["cumulative"] is True
               for series in price["series"])
    assert price["basis_kind"]["annual"] == "累计（全年）"
    assert price["labels_by_basis"]["annual"] == price["labels"]

    metrics = charts_plugin.parse_metrics([source], {})
    assert metrics["unit"] == "%"
    assert metrics["bases"] == ["annual", "half", "quarter"]
    assert metrics["basis_kind"]["quarter"] == "单期（单季）"
    assert len(metrics["series"]) == 4 * len(metrics["bases"]), "4 个指标 × 3 种口径"
    assert {series["basis"] for series in metrics["series"]} == set(metrics["bases"])
    names = [series["name"] for series in metrics["series"]]
    assert len(names) == len(set(names)), "多口径时序列名必须带口径，图例里不能出现同名线"
    assert "ROE (%)（年度）" in names and "ROE (%)（单季）" in names
    by_name = {series["name"]: series for series in metrics["series"]}
    assert by_name["ROE (%)（年度）"]["values"] == [17.0, 19.0]
    assert by_name["ROE (%)（单季）"]["values"] == [9.0, 5.0]
    for basis, indices in metrics["series_by_basis"].items():
        labels = metrics["labels_by_basis"][basis]
        assert all(charts_plugin.basis_of(label) == basis for label in labels)
        for index in indices:
            assert len(metrics["series"][index]["values"]) == len(labels)


# --------------------------------------------------------------- AC-7 / AC-3.1 图表契约


def test_chart_panel_declares_the_toolbar_unit_and_basis_contract(tmp_path):
    """AC-7：面板声明口径切换器/单位/导出三个工具，数据里带单位与口径说明。"""
    _, registry = make_app(tmp_path)
    company_dir(tmp_path)
    payload = panel_payload(registry, "charts.revenue_profit", company="600887.SH")

    assert payload["kind"] == "chart" and payload["render"] == "client"
    assert payload["options"]["chart"]["toolbar"] == {"basis": True, "unit": True, "export": True}
    assert payload["options"]["chart"]["type"] == "line"

    data = payload["data"]
    assert data["unit"] == "百万元"                     # 图上要标单位
    assert data["basis"] == "annual"                    # 默认年度（不混口径）
    assert set(data["bases"]) == {"annual", "half", "quarter"}
    assert data["basis_labels"] == {"annual": "年度", "half": "半年", "quarter": "单季"}
    assert data["basis_kind"]["annual"] == "累计（全年）"
    assert data["basis_kind"]["half"] == "累计（半年）"
    assert data["basis_kind"]["quarter"] == "单期（单季）"
    assert {series["basis"] for series in data["series"]} == {"annual"}

    params = {param["name"]: param for param in payload["params"]}
    assert params["basis"]["type"] == "enum"
    assert params["basis"]["choices"] == ["annual", "half", "quarter"]
    assert params["basis"]["default"] == "annual"
    assert params["company"]["source"] == "selection.company"


def test_chart_panel_route_switches_basis_and_gives_a_readable_empty_state(tmp_path):
    """AC-7 / AC-3.1：切换口径走真实面板接口；缺该口径给空状态，非法口径在参数层被拒。"""
    _, registry = make_app(tmp_path)
    company_dir(tmp_path)

    annual = panel_payload(registry, "charts.revenue_profit", company="600887.SH", basis="annual")["data"]
    quarter = panel_payload(registry, "charts.revenue_profit", company="600887.SH", basis="quarter")["data"]
    assert [series["name"] for series in annual["series"]] == ["营业收入（年度）", "归母净利润（年度）"]
    assert [series["name"] for series in quarter["series"]] == ["营业收入（单季）", "归母净利润（单季）"]
    assert quarter["labels"] == ["2025Q1", "2026Q1"] == quarter["labels_by_basis"]["quarter"]
    assert all(charts_plugin.basis_of(label) == "quarter" for label in quarter["labels"])
    assert all(len(series["values"]) == len(quarter["labels"]) for series in quarter["series"])

    # 这份数据包只有年度：要的是可读空状态，不是异常、也不是画成 0。
    company_dir(tmp_path, ANNUAL_ONLY_PACK, name="000001_平安",
                ticker="000001.SZ", company="平安银行")
    empty = panel_payload(registry, "charts.revenue_profit", company="000001.SZ", basis="quarter")["data"]
    assert empty["series"] == [] and empty["labels"] == []
    assert empty["bases"] == ["annual"]
    assert "单季" in empty["empty_hint"] and "没有期次" in empty["empty_hint"]

    # 枚举之外的口径：参数校验挡下（422 口径的错误码），不是 500，也不会画出别的东西。
    with pytest.raises(InvalidParam) as excinfo:
        panel_payload(registry, "charts.revenue_profit", company="600887.SH", basis="monthly")
    assert excinfo.value.code == "INVALID_PARAM"
    assert "annual" in excinfo.value.message and "monthly" in excinfo.value.message


def test_chart_core_owns_the_drawing_quality_contract():
    """AC-3.2 / AC-7（源码级）：DPR 适配、容器宽度、实测宽度抽稀、缺失值断开、导出。

    前端没有 JS 运行器（不引新依赖）：像素现象由 `scripts/gui_walkthrough.py` 的浏览器走查
    覆盖，这里钉的是「这些能力**存在且落在公共层**」——删掉任何一条，MT-6/MT-7 就会回退。
    """
    source = static_source("kinds/chart_core.js")

    resize = js_function_body(source, "export function resizeCanvas(")
    assert "devicePixelRatio" in resize
    assert "ctx.setTransform(" in resize, "按 DPR 缩放画布，否则高分屏上发糊"
    assert "canvas.width = Math.floor(cssWidth * ratio)" in resize
    assert "clientWidth" in resize, "宽度取自容器，不是固定像素"
    assert "ResizeObserver" in source and "observer.observe(container)" in source

    thinning = js_function_body(source, "export function thinLabels(")
    assert "measureText" in thinning and "minGap" in thinning, "按实测宽度抽稀，不是隔一项画一个"
    render = js_function_body(source, "export function renderChart(")
    assert "thinLabels(ctx, labels," in render
    assert "单位：" in render and "口径：" in render, "图上必须标明单位与口径（累计/单期）"
    assert "这一口径下没有可画的数据" in render

    legend = js_function_body(source, "export function layoutLegend(")
    assert "measureLegend(" in legend and "rows.push(" in legend, "图例按容器宽度换行，不固定步进"
    assert "measureText" in js_function_body(source, "export function measureLegend(")

    scale = js_function_body(source, "export function niceScale(")
    assert "if (min > 0) min = 0;" in scale, "正值量级从 0 起，避免视觉夸大波动"

    drawn = js_function_body(source, "export function drawSeries(")
    assert "started = false" in drawn and "缺失值" in drawn, "缺失值断开折线，不连假线"
    assert "ctx.arc(" in drawn, "缺失值位置标空心点，让断开口看起来是有意的"
    assert "if (!isNumber(value)) return;" in drawn, "柱状图里缺失值不画柱子（也不画成 0）"

    tooltip = js_function_body(source, "export function drawTooltip(")
    assert "—（缺失）" in tooltip, "悬停读数里缺失值有可见标注"

    export_png = js_function_body(source, "export function downloadPng(")
    assert "toDataURL(" in export_png and "link.download" in export_png
    export_tsv = js_function_body(source, "export function seriesToTsv(")
    assert "期次" in export_tsv and "\\t" in export_tsv and "isMissing(value)" in export_tsv


def test_chart_kind_only_dispatches_and_exposes_the_basis_switch_and_export():
    """AC-3.1 / AC-3.2（源码级）：`chart.js` 只做分发 + 工具栏，绘制质量全在公共层。"""
    source = static_source("kinds/chart.js")
    assert 'from "/kinds/chart_core.js"' in source
    for forbidden in ("getContext(", "setTransform", "fillText(", "toDataURL("):
        assert forbidden not in source, f"绘制实现不该复制到 kind 层（发现 {forbidden}）"

    bar = js_function_body(source, "function toolbar(")
    assert "panel.options.chart.toolbar" in bar, "工具由面板声明的 options 决定，kind 不自作主张"
    assert "select.dataset.chartBasis" in bar, "口径切换器"
    assert "basis_labels" in bar and "basis_kind" in bar, "选项上标出口径与累计/单期"
    assert 'data.basis || "annual"' in bar
    assert "png.dataset.chartExport" in bar and "tsv.dataset.chartExport" in bar, "导出（图片 + 数据）"
    assert "downloadPng(chart.canvas" in bar and "seriesToTsv(" in bar
    assert "缺失值断开折线并标空心点，不画成 0" in bar, "缺失值语义要在图上说明"

    render = js_function_body(source, "export async function render(")
    assert "unit: data.unit" in render
    assert "basisLabel:" in render and "basisKind:" in render
    assert "window.location.hash" in render and "basis" in render, "切换口径写回 URL（可前进后退）"
    assert "暂无数据" in render


# --------------------------------------------------------------- AC-6 / AC-3.3 数据新鲜度


def test_chart_panel_meta_carries_cache_hit_and_fingerprint(tmp_path):
    """AC-6 / AC-3.3：面板拿到的 `meta` 要能回答「这份数据从哪来、是不是刚算的」。"""
    _, registry = make_app(tmp_path)
    company_dir(tmp_path)

    first = panel_payload(registry, "charts.metrics", company="600887.SH")["data"]["meta"]
    assert first["cached"] is False and first["from_session"] is False
    assert first["fingerprint"].startswith("sha256:")
    assert first["generated_at"], "数据生成时间（界面上要显示它）"
    assert first["source_digests"]["data_pack_market.md"].startswith("sha256:")
    # `parser_version` 升过版（口径元数据是 REQ-012.3 加的）：断言它**存在且是整数**，
    # 而不是钉死某个数字——钉死会让每次解析器演进都要改测试，而这里要验的是
    # 「缓存键含解析器版本」这件事本身。
    assert first["dataset"] == "charts.metrics"
    assert isinstance(first["parser_version"], int) and first["parser_version"] >= 1

    second = panel_payload(registry, "charts.metrics", company="600887.SH")["data"]["meta"]
    assert second["cached"] is True, "同一份数据包第二次打开应命中派生缓存"
    assert second["fingerprint"] == first["fingerprint"]
    assert second["generated_at"] == first["generated_at"]


def test_freshness_badge_is_driven_by_panel_meta_from_mount_panel(tmp_path):
    """AC-6 / AC-3.3：`app.js` 渲染 `panel.meta`，且**载荷顶层真的有 meta**。

    两边都要断：只测前端源码会漏掉「provider 写 `data.meta`、前端读 `panel.meta`」这种
    两处永远对不上的接线错误（实测就是这样：契约写在注释里、徽标永远不显示）。
    """
    app_js = static_source("app.js")
    note = js_function_body(app_js, "export function freshnessNote(")
    assert "命中缓存（没有重新解析）" in note
    assert "本次重新解析" in note
    assert "数据生成时间" in note and "generated_at" in note
    mount = js_function_body(app_js, "async function mountPanel(")
    assert "freshnessNote(panel.meta)" in mount, "徽标必须挂在 mountPanel 上，否则没人调用它"

    _, registry = make_app(tmp_path)
    company_dir(tmp_path)
    payload = panel_payload(registry, "charts.revenue_profit", company="600887.SH")
    assert payload["data"]["meta"]["fingerprint"], "provider 下发的 meta 还在（面板自己的那份）"
    assert payload["meta"]["fingerprint"], "内核把 meta 提到载荷顶层，前端读的就是它"
    assert payload["meta"]["cached"] in (True, False)


# --------------------------------------------------------------- AC-6 / AC-3.3 两个动作


def test_offline_rebuild_and_online_pull_are_two_distinct_actions(tmp_path):
    """AC-6 / AC-3.3：离线「刷新视图」与联网「重新拉取」在动作层是两个不同的东西。

    判据用**三件事**分开：网络副作用、是否需要显式确认、面向用户的文案。
    本测试不看 `enabled`/`blockers`（依赖机器上有没有 token，与语义无关）。
    """
    _, registry = make_app(tmp_path)
    company_dir(tmp_path)
    entries = {item["id"]: item for item in
               call_route(registry, "GET", "/api/v1/actions", company="600887.SH")["data"]["actions"]}
    rebuild, pull = entries["data.rebuild"], entries["data.pull_all"]

    assert rebuild["id"] != pull["id"]
    assert rebuild["effects"]["network"] is False and rebuild["effects"]["quota"] is False
    assert pull["effects"]["network"] is True and pull["effects"]["quota"] is True
    assert pull["danger"] is True and pull["confirm"], "联网拉取必须显式确认一次"
    assert pull["confirm"]["confirm_label"] and pull["confirm"]["title"]
    assert rebuild["danger"] is False and rebuild["confirm"] == {}, "离线重算不花钱，不必二次确认"

    assert "联网" in pull["description"] and "配额" in pull["description"]
    assert "离线" in rebuild["description"]
    assert "不联网" in rebuild["description"] and "不花配额" in rebuild["description"]

    # 两个动作都只编排既有按键（业务实现不搬进界面），且步骤是链式的。
    for entry in (rebuild, pull):
        assert entry["steps"] and all(step["kind"] == "command" for step in entry["steps"])
    spec = registry.job_type_spec("data.rebuild")
    assert spec.effects["network"] is False and spec.danger is False and spec.confirm == {}


# --------------------------------------------------------------- AC-8 / AC-3.4 表格能力


def test_table_panels_declare_and_emit_the_search_sort_page_contract(tmp_path):
    """AC-8 / AC-3.4：通用表格控件是**服务端产出的契约**，客户端按这些属性接管行为。

    列级 `sort` 落到 `data-sort-key` / `data-sort-type`，面板级 `options={"table": …}`
    落到 `data-table-controls` / `data-page-size`——两边都要断，因为「声明了但接线没接上」
    正是这里踩过的坑（分支不可达 = 死代码）。
    """
    _, registry = make_app(tmp_path)
    company_dir(tmp_path)

    spec = registry.panel_spec("companies.list")
    assert spec.options["table"] == {"search": True, "sort": True, "page": 50}
    assert registry.panel_spec("home.universe").options["table"]["search"] is True
    assert registry.panel_spec("data.gaps").options["table"]["page"] == 50
    assert registry.panel_spec("collect.batches").options["table"]["sort"] is True

    html = panel_payload(registry, "companies.list")["html"]
    assert 'data-sort-key="name"' in html and 'data-sort-type="text"' in html
    artifacts = panel_payload(registry, "companies.artifacts", company="600887.SH")["html"]
    assert 'data-sort-type="number"' in artifacts, "数值列声明 number，客户端才不会按字符串排"
    assert 'data-sort-key="rel"' not in artifacts, "没声明 sort 的列不该长出排序契约"

    # 渲染层的分支本身是对的（拿着 table 选项渲染就有控件标记）。
    rendered = panel_render.render_table({
        "columns": [{"key": "name", "title": "公司", "sort": "text"}],
        "rows": [{"name": "600887 伊利股份"}],
        "table": {"search": True, "sort": True, "page": 25},
    })
    assert 'data-table-controls="search,sort,page"' in rendered
    assert 'data-page-size="25"' in rendered

    # 真实面板的 HTML 里也要有这两个标记（否则渲染层那段就是不可达的死代码）。
    assert 'data-table-controls="search,sort,page"' in html
    assert 'data-page-size="50"' in html
    # 空表走的是**引导空状态**（那时不需要搜索/分页控件）：有空行时才要求控件标记。
    gaps_html = panel_payload(registry, "data.gaps")["html"]
    if "panel-table-empty" in gaps_html:
        assert "panel-empty-state-inline" in gaps_html, "空表必须给引导，不能是一张空表"
    else:
        assert "data-table-controls" in gaps_html

    # 面板声明了 `sort` 就**必须**每列都有 `data-sort-key`：门② 第三轮在真实浏览器里
    # 点表头发现批次列表「声明了 sort 却一个可排序表头都没有」——只断言面板级声明的
    # 测试全绿，所以这里把声明与列级契约**绑在一起**判。
    # **扫所有注册过的面板**，不手写清单：门② 第八轮指出手写清单漏了 `data.store`
    # （它声明了 `sort: True` 却 0 个 `data-sort-key`，正是第三轮那条缺陷的形状）。
    company_dir(tmp_path)          # 公司级面板需要一份产物才能渲染
    checked, context_dependent = [], []
    for panel_id in registry.panel_ids():
        spec = registry.panel_spec(panel_id)
        if spec.kind != "table":
            continue
        options = spec.options.get("table") or {}
        columns = panel_columns(registry, panel_id)
        declares_sort = bool(options.get("sort"))
        columns_sort = any(col.get("sort") for col in columns)
        # 两个方向都要判：**声明了就必须落地**（第三轮），**落地了就必须声明**（第八轮：
        # `collect.archive` 的列有 `sort` 而面板没声明，渲染层不下发控件，键是死代码）。
        assert declares_sort == columns_sort, (
            f"{panel_id} 的面板级 `sort` 声明（{declares_sort}）与列级 `sort`"
            f"（{columns_sort}）脱节——两边必须配对"
        )
        if not declares_sort:
            continue
        try:
            html = panel_payload(registry, panel_id, company="600887.SH")["html"]
        except (BadRequest, NotFound):
            # 需要别的上下文（例如某个 run / 批次）的面板：这一层跳过，
            # 但**记下来**，让「到底跳过了哪些」可见（不然又会变成手写清单那种盲区）。
            context_dependent.append(panel_id)
            continue
        checked.append(panel_id)
        if "panel-table-empty" in html or "<table" not in html:
            continue
        heads = re.findall(r"<th[ >][^>]*>", html)   # 注意别把 `<thead>` 算进来
        sortable = [head for head in heads if "data-sort-key=" in head]
        # 判据是「**声明了 sort 的列**都有可排序表头」——不是「所有表头都可排序」：
        # 长文本列（如缺口的原因原文）刻意不声明 sort，它不该长排序键。
        from webui.core.context import RequestContext as Ctx
        from webui.core.routes import resolve_params

        declared = [col for col in columns if col.get("sort")]
        assert len(sortable) == len(declared), (
            f"{panel_id} 有 {len(declared)} 列声明了 sort，但只有 {len(sortable)} 个表头"
            f"带 data-sort-key（声明与列级契约脱节——门② 第三轮点表头点出来的）"
        )
        for column in declared:
            assert f'data-sort-key="{column["key"]}"' in html, (
                f"{panel_id} 的 {column['key']} 列声明了 sort，却没有 data-sort-key"
            )
    # 覆盖范围要**可见且足够宽**：门② 第三轮漏了 collect.*、第八轮漏了 data.store，
    # 都是「手写清单」的后果。这里正面钉住必须被扫到的那些面板。
    # `data.store` 是两列键值表：它**不该**声明排序（第八轮 F2 就是它声明了却没落地）。
    store_options = registry.panel_spec("data.store").options.get("table") or {}
    assert not store_options.get("sort"), "键值表不该声明排序"
    for required in ("data.gaps", "data.universe", "collect.batches",
                     "collect.gaps", "collect.rebuild", "collect.archive",
                     "companies.list", "home.universe"):
        assert required in checked, (
            f"{required} 没有被这条契约扫到（checked={checked} "
            f"context_dependent={context_dependent}）"
        )


def test_table_behaviour_lives_in_the_shared_renderer():
    """AC-8 / AC-3.4（源码级）：搜索/排序/分页只在通用渲染层实现一次。"""
    table_js = static_source("kinds/table.js")

    search = js_function_body(table_js, "function buildControls(")
    assert 'search.dataset.tableSearch = "1"' in search, "搜索框标记（data-table-search）"
    assert 'search.placeholder = "搜索…"' in search and "aria-label" in search
    pager = js_function_body(table_js, "function buildPager(")
    assert 'prev.dataset.tablePage = "prev"' in pager and 'next.dataset.tablePage = "next"' in pager
    assert "上一页" in pager and "下一页" in pager

    render = js_function_body(table_js, "export async function render(")
    assert "第 ${state.page + 1} / ${pages} 页" in render, "页码与上一页/下一页的禁用状态"
    assert "pager.next.disabled = state.page >= pages - 1" in render
    assert "th.dataset.sortKey" in render and "th.dataset.sortType" in render
    assert 'options.page || DEFAULT_PAGE_SIZE' in render, "分页大小来自服务端声明"
    assert "data-original-index" in render or "dataset.originalIndex" in render, "保留默认顺序"
    assert "options.search !== false" in render
    assert 'container.querySelector("table")' in render and "if (!table) return;" in render, (
        "空状态/降级卡片里没有表格：行为层不能因此报错"
    )

    compare = js_function_body(table_js, "function compare(")
    assert "缺失值排在最后，不冒充 0" in compare
    assert 'localeCompare(b, "zh-Hans-CN")' in compare, "中文列按中文排序"
    assert "state.direction" in compare

    # 「不是每个面板各写一遍」：这几个标记在整个前端里只出现在通用渲染层。
    owners = sorted(
        path.relative_to(STATIC).as_posix()
        for path in STATIC.rglob("*.js")
        if "dataset.tableSearch" in path.read_text(encoding="utf-8")
    )
    assert owners == ["kinds/table.js"]
    app_js = static_source("app.js")
    assert 'registerPanelKind("table", renderTable)' in app_js
    assert 'from "/kinds/table.js"' in app_js
    assert "DEFAULT_PAGE_SIZE = 50" in table_js, "默认每页 50 行：100 行数据不用肉眼翻"


# --------------------------------------------------------------- AC-10 / AC-3.5 错误文案


def test_panel_errors_are_user_facing_with_the_code_collapsed(tmp_path):
    """AC-10 / AC-3.5：降级卡片先说人话 + 「下一步」，错误码只出现在折叠的技术细节里。"""
    spec = PanelSpec(id="charts.metrics", kind="chart", title="关键财务指标趋势", provider=None)
    html = panel_render.render_panel_error(
        spec, "PARSE_FAILED", "这份数据的表头和预期不一致。", hint="按示例更新解析器后再试。"
    )
    assert "这块内容暂时看不到" in html
    assert html.index("这块内容暂时看不到") < html.index("PARSE_FAILED"), "错误码不能占据主视觉"
    assert "下一步：按示例更新解析器后再试。" in html
    assert "<details" in html and "<summary>技术细节</summary>" in html
    assert "PARSE_FAILED" not in html.split("<details")[0], "折叠区之前不许出现错误码"
    assert "panel-error-title" in html and "charts.metrics" in html

    # 没有 hint 的失败路径：仍然给标题 + 折叠的技术细节，不出现空的「下一步：」。
    no_hint = panel_render.render_panel_error(spec, "INTERNAL", "面板内部错误")
    assert "这块内容暂时看不到" in no_hint and "<details" in no_hint
    assert "下一步：" not in no_hint

    # 真实链路：数据包缺小节 → 页面上的三张图各自降级成同一套文案（页面不整体失败）。
    _, registry = make_app(tmp_path)
    company_dir(tmp_path, BROKEN_PACK)
    page = call_route(registry, "GET", "/api/v1/pages/charts", company="600887.SH")["data"]
    degraded = [panel for panel in page["panels"] if panel.get("fallback")]
    assert len(degraded) == 3, "三张图都缺小节，应当各自降级而不是整页失败"
    for panel in degraded:
        panel_html = panel["html"]
        assert "这块内容暂时看不到" in panel_html
        assert "下一步：" in panel_html
        assert panel_html.index("这块内容暂时看不到") < panel_html.index("ARTIFACT_MISSING")
        assert (panel.get("meta") or {}).get("error", {}).get("code") == "ARTIFACT_MISSING"
        assert panel["meta"]["error"]["unexpected"] is False, "可预期的缺件不算未预期异常"

    # 前端自己兜底的错误卡（取数失败路径）用同一套文案。
    card = js_function_body(static_source("app.js"), "function problemCard(")
    assert "这块内容暂时看不到" in card
    assert "下一步：" in card and "error.hint" in card
    assert "技术细节" in card and "error.code" in card


def test_markdown_panel_names_the_company_and_empty_tables_give_guidance(tmp_path):
    """AC-10 的「可读」另一面：报告面板标出当前公司；空表给引导而不是空白网格。"""
    _, registry = make_app(tmp_path)
    company_dir(tmp_path)

    report = panel_payload(registry, "report.view", company="600887.SH")
    assert report["kind"] == "report" and report["render"] == "client"
    assert report["data"]["company"]["display_name"] == "600887 伊利股份"
    # UI 从安全数据契约渲染；无指针时提供可选历史，不能猜默认报告。
    assert report["data"]["artifact"] is None
    assert any(item["name"] == "qualitative_report.md" for item in report["data"]["versions"])
    # 同一家公司：报告接口与渲染出来的 HTML 用同一个显示名。
    assert call_route(registry, "GET", "/api/v1/companies/600887.SH/report")["data"][
        "company"]["display_name"] == "600887 伊利股份"

    # 没有公司信息时不硬塞一行占位。
    assert "panel-company" not in panel_render.render_markdown({"html": "<p>正文</p>"})

    # 空表：有 guide 就是一块可读的空状态（不是 0 行网格），没有 guide 也至少不白屏。
    empty = panel_render.render_table({
        "columns": [{"key": "company", "title": "公司"}], "rows": [],
        "guide": "还没有公司数据。下一步：① 加自选股；② 拉取数据。",
    })
    assert "panel-table-empty" in empty and "还没有公司数据" in empty and "下一步" in empty
    blank = panel_render.render_table({"columns": [{"key": "company", "title": "公司"}], "rows": []})
    assert "<table>" in blank and "0 行" in blank

    # 表格内容照常转义（改渲染层不能回退 `REQ-009` 的 XSS 边界）。
    escaped = panel_render.render_table({
        "columns": [{"key": "company", "title": "公司"}],
        "rows": [{"company": "<script>alert(1)</script>"}],
    })
    assert "<script>" not in escaped and "&lt;script&gt;" in escaped
