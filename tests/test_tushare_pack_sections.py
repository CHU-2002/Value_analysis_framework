# 覆盖需求：REQ-006.2 —— AC-2.1（§12 营收同比字段）、AC-2.2（§16 股数单位与一致性）
"""REQ-006.2 数据包「分部与关键指标 / 单位与量级」的回归测试。
覆盖需求：REQ-006.2 —— AC-2.1（§12 营收同比字段；§9 分部表见后续切片）、
AC-2.2（§16 总股本/质押股数单位 + 与 §1 市值反推的一致性）。

夹具与常量取自真实数据，测试本身**不做任何网络调用**：

- ``pledge_stat.json``：2026-09-25 AC-1.9 实跑（run ``20260925T042255414048Z``，
  统计日 ``20260918``）的 ``pledge_stat`` 真实响应。
- ``fina_indicator.json``：字段名按 ``fina_indicator`` 真实 schema（``or_yoy``）。
  同一轮实跑实测：显式请求的 ``or_yoy`` / ``tr_yoy`` 回 4.1345 / 4.1285，
  而此前请求的 ``revenue_yoy`` 不是该接口字段，被 Tushare 静默丢弃 → 整行永远是「—」。
- §1 的「总市值 (万元) = 17,015,220.01」「当前价格 = 26.90」取自同一轮实跑落盘的
  ``data_pack_market.md``（用于 AC-2.2 的股数反推一致性校验）。
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pandas as pd

from results.context import build_module_context
from results.evidence import build_evidence_index
from tushare_collector import TushareClient

MOCK_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "mock_tushare_responses")

#: 2026-09-25 AC-1.9 实跑落盘的 §1 市值（万元）与现价（元），用于反推总股本。
REAL_MARKET_CAP_WAN_YUAN = 17015220.01
REAL_PRICE_YUAN = 26.90


def _load_mock(filename: str) -> pd.DataFrame:
    """Load a mock fixture as DataFrame（与 test_tushare_client.py 同口径）。"""
    with open(os.path.join(MOCK_DIR, filename)) as f:
        data = json.load(f)
    return pd.DataFrame(data if isinstance(data, list) else [data])


def _make_client() -> TushareClient:
    with patch("tushare_collector.ts") as mock_ts:
        mock_ts.pro_api.return_value = MagicMock()
        client = TushareClient("test_token")
    client._cache_dir = tempfile.mkdtemp(prefix="pack_sections_test_cache_")
    return client


def _two_column_rows(section_markdown: str) -> dict:
    """把 `项目 | 数值` 两列 markdown 表解析成 {项目: 数值}。"""
    rows = {}
    for line in section_markdown.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 2 or cells[0] in {"项目", ""} or set(cells[0]) <= {"-", ":"}:
            continue
        rows[cells[0]] = cells[1]
    return rows


def _render_pledge(df: pd.DataFrame) -> str:
    client = _make_client()
    with patch("tushare_collector.time.sleep"):
        client._safe_call = MagicMock(return_value=df)
        return client.get_pledge_stat("600887.SH")


def _pledge_fixture_row() -> dict:
    with open(os.path.join(MOCK_DIR, "pledge_stat.json")) as f:
        return json.load(f)[0]


def _revenue_yoy_row(section_markdown: str) -> list:
    for line in section_markdown.splitlines():
        if line.startswith("| 营收同比增长率"):
            return [cell.strip() for cell in line.strip().strip("|").split("|")][1:]
    raise AssertionError("§12 没有「营收同比增长率」行")


# --- AC-2.2：§16 的单位与量级 -------------------------------------------------


class TestPledgeStatUnits:
    def test_share_counts_are_not_divided_twice(self):
        """AC-2.2：pledge_stat 的三个股数字段本来就是万股，不得再除 1e4。"""
        result = _render_pledge(_load_mock("pledge_stat.json"))
        rows = _two_column_rows(result)

        assert rows["总股本 (万股)"] == "632,536.07"
        assert rows["无限售质押 (万股)"] == "39,775.10"
        assert rows["有限售质押 (万股)"] == "0.00"
        # 回归（2026-09-25 实跑实测）：双除 1e4 会输出 63.25 / 3.98，差 10,001 倍。
        assert "63.25" not in result
        assert "3.98" not in result

    def test_total_share_consistent_with_market_cap_over_price(self):
        """AC-2.2 的一致性校验：§1 总市值 ÷ 现价 反推的股数 ≈ §16 总股本（相对误差 <1%）。"""
        row = _pledge_fixture_row()
        implied_wan_shares = REAL_MARKET_CAP_WAN_YUAN / REAL_PRICE_YUAN

        relative_error = abs(implied_wan_shares - row["total_share"]) / row["total_share"]

        assert relative_error < 0.01, (
            f"§16 总股本 {row['total_share']} 万股与 §1 反推的 {implied_wan_shares:.2f} 万股"
            f"相对误差 {relative_error:.2%}，超出 1%"
        )

    def test_pledge_ratio_consistent_with_pledged_and_total_share(self):
        """AC-2.2：接口自带的质押比例必须等于质押股数 / 总股本。"""
        row = _pledge_fixture_row()
        expected_ratio = (
            (row["unrest_pledge"] + row["rest_pledge"]) / row["total_share"] * 100
        )

        assert abs(expected_ratio - row["pledge_ratio"]) < 0.01

    def test_latest_end_date_wins(self):
        """§16 只展示最近一期（排序行为不因夹具换成真实单期响应而失去覆盖）。"""
        df = pd.DataFrame(
            [
                {"end_date": "20250131", "pledge_count": 1, "unrest_pledge": 1.0,
                 "rest_pledge": 0.0, "total_share": 100.0, "pledge_ratio": 1.0},
                {"end_date": "20260918", "pledge_count": 60, "unrest_pledge": 39775.1,
                 "rest_pledge": 0.0, "total_share": 632536.07, "pledge_ratio": 6.29},
            ]
        )
        rows = _two_column_rows(_render_pledge(df))

        assert rows["统计日期"] == "20260918"
        assert rows["质押笔数"] == "60"


# --- AC-2.1：§12 的营收同比增长率 --------------------------------------------


class TestFinaIndicatorRevenueYoy:
    def test_revenue_yoy_row_renders_values_from_or_yoy(self):
        """AC-2.1：请求的是 fina_indicator 真实字段 ``or_yoy``，该行必须有值。"""
        client = _make_client()
        seen: dict = {}

        def fake_call(api_name, **kwargs):
            seen[api_name] = kwargs
            return _load_mock("fina_indicator.json")

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(side_effect=fake_call)
            result = client.get_fina_indicators("600887.SH")

        fields = seen["fina_indicator"]["fields"]
        assert "or_yoy" in fields
        assert "revenue_yoy" not in fields, "revenue_yoy 不是 fina_indicator 的字段"

        values = _revenue_yoy_row(result)
        assert values, "§12 营收同比行没有数据列"
        assert all(value != "—" for value in values), f"营收同比整行仍为空：{values}"

    def test_revenue_yoy_falls_back_to_tr_yoy(self):
        """AC-2.1：只有 ``tr_yoy``（营业总收入同比）的主体也要显示营收同比。"""
        client = _make_client()
        df = _load_mock("fina_indicator.json").copy()
        df["or_yoy"] = None
        df["tr_yoy"] = [1.11, 2.22, 3.33]

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(return_value=df)
            result = client.get_fina_indicators("600887.SH")

        values = _revenue_yoy_row(result)
        assert values[0] == "1.11", values
        assert all(value != "—" for value in values), values


# --- AC-2.3：Agent 专属占位段落的填充策略（owner 2026-09-25：增量流程不填） ----

#: 与真实数据包同构的最小片段：§8/§10 只有占位符，§13 有真实的 13.1 内容 + 13.2 占位符。
PLACEHOLDER_PACK = """# 数据包 — 600887.SH

## 3. 合并利润表

| 项目 (百万元) | 2026H1 |
| --- | ---: |
| 营业收入 | 64,330.94 |

## 8. 行业与竞争

*[§8 待Agent WebSearch补充]*

## 10. 管理层讨论与分析 (MD&A)

*[§10 待Agent WebSearch补充]*

## 13. 风险警示

### 13.1 脚本自动检测

**高风险:**
- [YOY_ANOMALY|高] 合并利润表/assets_impair_loss: 2025H1→2026H1 同比变化 628% 超过 300% 阈值

### 13.2 Agent WebSearch 补充

*[§13.2 待Agent WebSearch补充]*
"""


class TestAgentOnlyPlaceholderPolicy:
    """AC-2.3：占位符不得作为模块证据槽位；策略是「增量流程不填」。"""

    def _index(self, tmp_path):
        pack = tmp_path / "data_pack_market.md"
        pack.write_text(PLACEHOLDER_PACK, encoding="utf-8")
        index = build_evidence_index([{"source_id": "market_data", "path": str(pack)}])
        return pack, index

    def test_placeholder_sections_are_not_indexed_as_evidence(self, tmp_path):
        _, index = self._index(tmp_path)
        sections = {entry["section"] for entry in index["entries"]}

        assert "8" not in sections
        assert "10" not in sections
        assert "13" in sections, "§13 有真实 13.1 内容，仍应进索引"
        for entry in index["entries"]:
            assert "待Agent WebSearch补充" not in entry["quote"]

        unfilled = {(item["source_id"], item["section"]) for item in index["unfilled_sections"]}
        assert ("market_data", "8") in unfilled
        assert ("market_data", "10") in unfilled

    def test_module_bundle_marks_them_unavailable_with_a_reason(self, tmp_path):
        pack, index = self._index(tmp_path)
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

        bundle = build_module_context(
            "environment",
            data_pack_path=str(pack),
            evidence_index_path=str(index_path),
        )

        coverage = bundle["selection"]["evidence_coverage"]
        assert coverage["market_data:8"] == "unavailable"
        assert coverage["market_data:10"] == "unavailable"
        reasons = {(item["source_id"], item["section"]): item["reason"]
                   for item in bundle["unavailable_inputs"]}
        assert ("market_data", "8") in reasons
        assert ("market_data", "10") in reasons
        assert all(reason for reason in reasons.values()), "unavailable 必须带原因"
        assert "待Agent WebSearch补充" not in json.dumps(bundle, ensure_ascii=False)


# --- AC-2.3：§13.1 的异常检测期次必须包含本期 -------------------------------


def _income_df() -> pd.DataFrame:
    """最小 income 历史：含 2007/2008 的远古噪声、最新年报、以及本期 2026H1。"""
    return pd.DataFrame(
        [
            # end_date, revenue, n_income_attr_p, assets_impair_loss
            ("20260630", 64330.94, 6172.79, 2455.88),
            ("20250630", 61776.75, 7720.36, 337.45),
            ("20251231", 115636.23, 10400.00, 1000.00),
            ("20241231", 115393.31, 8450.00, 100.00),
            ("20081231", 216586.00, 82.00, 0.00),
            ("20071231", 193597.00, 1.00, 0.00),
        ],
        columns=["end_date", "revenue", "n_income_attr_p", "assets_impair_loss"],
    )


class TestSection131PeriodSelection:
    """AC-2.3：§13.1 只比较「最新一期 vs 上年同期」与「最新年报 vs 上一年报」。"""

    def test_pairs_include_the_latest_period(self):
        client = _make_client()
        pairs = client._yoy_period_pairs(_income_df())

        assert pairs[0] == ("20260630", "20250630"), "最新财报期必须在检测窗口里"
        assert ("20251231", "20241231") in pairs, "最新年报口径也要看"
        assert all("2008" not in pair[0] and "2007" not in pair[1] for pair in pairs)

    def test_section_131_reports_the_current_period_not_ancient_noise(self):
        client = _make_client()
        income = _income_df()

        def safe_call(api_name, **kwargs):
            return {"income": income}.get(api_name, pd.DataFrame())

        with patch("tushare_collector.time.sleep"):
            client._safe_call = MagicMock(side_effect=safe_call)
            result = client.assemble_data_pack("600887.SH")

        warnings = result.split("### 13.1 脚本自动检测", 1)[1].split("### 13.2", 1)[0]
        assert "2026H1" in warnings, f"§13.1 必须包含本期：{warnings}"
        assert "2008" not in warnings, f"§13.1 不应再落在 18 年前的数据上：{warnings}"
