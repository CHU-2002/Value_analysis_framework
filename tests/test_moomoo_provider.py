"""moomoo's external payload is normalized before the analysis engines see it."""

# 覆盖需求：REQ-011—— AC-1 美股数据包、AC-2 财务口径、AC-3 入口选择、AC-4 来源与缺口、AC-5 失败路径

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from data_providers import configured_provider, create_data_client
from moomoo_collector import MoomooClient, moomoo_code


def report(items, *, currency="USD", period="2025/FY", end="2025-12-31"):
    return {
        "currency_code": currency, "financial_type": "ANNUAL",
        "date_time_str": end, "period_text": period,
        "item_list": [{"field_id": field, "data": value, "yoy": 5.0}
                      for field, value in items.items()],
    }


class FakeOpenD:
    def __init__(self):
        self.calls = []
        self.responses = {
            1: [report({8002: 100e9, 8003: 60e9, 8004: 40e9,
                        8017: 25e9, 8037: 20e9, 8043: 20e9,
                        8047: 5.0, 8048: 4.9})],
            2: [report({8001: 150e9, 8002: 70e9, 8004: 20e9,
                        8048: 70e9, 8049: 40e9, 8085: 80e9})],
            3: [report({8015: 30e9, 8019: 3e9, 8042: -5e9,
                        8056: -10e9, 8061: -2e9, 8072: 25e9})],
            4: [report({14002: 40.0, 14005: 20.0, 14029: 25.0})],
        }

    def get_market_snapshot(self, codes):
        self.calls.append(("snapshot", codes))
        return 0, pd.DataFrame([{
            "code": "US.AAPL", "name": "Apple", "listing_date": "1980-12-12",
            "update_time": "2026-09-25 16:00:00", "last_price": 100.0,
            "open_price": 99.0, "high_price": 101.0, "low_price": 98.0,
            "volume": 1e6, "turnover": 100e6, "total_market_val": 200e9,
            "pe_ttm_ratio": 10.0, "pb_ratio": 2.5,
            "highest52weeks_price": 120.0, "lowest52weeks_price": 80.0,
        }])

    def get_financials_statements(self, code, **kwargs):
        self.calls.append(("financials", code, kwargs))
        return 0, {"report_list": deepcopy(self.responses[kwargs["statement_type"]]),
                   "next_key": "-1"}

    def request_history_kline(self, code, **kwargs):
        self.calls.append(("history", code, kwargs))
        return 0, pd.DataFrame([
            {"time_key": "2026-09-14 00:00:00", "open": 90.0,
             "high": 100.0, "low": 88.0, "close": 95.0,
             "volume": 1e6, "turnover": 95e6},
            {"time_key": "2026-09-21 00:00:00", "open": 95.0,
             "high": 105.0, "low": 94.0, "close": 100.0,
             "volume": 1e6, "turnover": 100e6},
        ]), None

    def get_corporate_actions_dividends(self, code):
        self.calls.append(("dividends", code))
        return 0, {"dividend_list": [
            {"statement": "1股派息0.25USD", "ex_date": "2025/02/01"},
            {"statement": "1股派息0.25USD", "ex_date": "2025/05/01"},
        ]}

    def get_financials_revenue_breakdown(self, code):
        self.calls.append(("segments", code))
        return 0, {"period": "2025/FY", "currency_code": "USD",
                   "breakdown_list": [{"type": "BUSINESS", "item_list": [
                       {"name": "Devices", "main_oper_income": 80e9,
                        "ratio": 80.0}]}]}


def client():
    return MoomooClient(FakeOpenD(), enable_yfinance=False, as_of="2026-09-27")


def test_code_conversion_and_provider_selection(monkeypatch):
    assert moomoo_code("AAPL.US") == "US.AAPL"
    with pytest.raises(ValueError, match="只支持美股"):
        moomoo_code("600887.SH")
    with pytest.raises(ValueError, match="只支持美股"):
        create_data_client("moomoo", "600887.SH")
    monkeypatch.setenv("DATA_PROVIDER", "moomoo")
    assert configured_provider() == "moomoo"
    assert configured_provider("tushare") == "tushare"
    monkeypatch.setenv("DATA_PROVIDER", "unknown")
    with pytest.raises(ValueError, match="只支持"):
        configured_provider()


def test_financials_map_stable_ids_and_raw_usd_units():
    source = client()
    income = source._safe_call("us_income", ts_code="AAPL")
    balance = source._safe_call("us_balancesheet", ts_code="AAPL")
    cash = source._safe_call("us_cashflow", ts_code="AAPL")
    metrics = source._safe_call("us_fina_indicator", ts_code="AAPL")
    assert income.loc[income.ind_name == "营业收入", "ind_value"].iloc[0] == 100e9
    assert balance.loc[balance.ind_name == "股东权益", "ind_value"].iloc[0] == 80e9
    assert cash.loc[cash.ind_name == "资本支出", "ind_value"].iloc[0] == 5e9
    assert metrics.iloc[0]["roe_avg"] == 25.0
    assert metrics.iloc[0]["debt_asset_ratio"] == pytest.approx(70 / 150 * 100)
    assert metrics.iloc[0]["operate_income_yoy"] == 5.0
    assert all(call[2]["currency_code"] == "USD" for call in source.quote_context.calls
               if call[0] == "financials")


def test_full_pack_uses_moomoo_without_tushare_token(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    source = client()
    pack = source.assemble_data_pack("AAPL.US")
    assert "*数据来源: moomoo OpenAPI*" in pack
    assert "主营业务构成" in pack and "Devices" in pack
    assert "美股暂不支持" in pack  # buybacks are unavailable
    assert source._store["income"].iloc[0]["revenue"] == 100e9
    assert source._store["cashflow"].iloc[0]["c_pay_acq_const_fiolta"] == 5e9
    assert source._store["dividends"].iloc[0]["cash_div_tax"] == 0.5
    assert len(source._store["weekly_prices"]) == 2
    history = [call for call in source.quote_context.calls if call[0] == "history"]
    assert history[0][2]["autype"] == "None"


def test_non_usd_report_is_rejected():
    source = client()
    source.quote_context.responses[1][0]["currency_code"] = "HKD"
    with pytest.raises(RuntimeError, match="币种不是 USD"):
        source._safe_call("us_income", ts_code="AAPL")


def test_missing_revenue_or_capex_blocks_core_data():
    source = client()
    source.quote_context.responses[1][0]["item_list"] = [
        x for x in source.quote_context.responses[1][0]["item_list"]
        if x["field_id"] != 8002
    ]
    with pytest.raises(RuntimeError, match="缺少核心字段.*revenue"):
        source._safe_call("us_income", ts_code="AAPL")

    source = client()
    source.quote_context.responses[3][0]["item_list"] = [
        x for x in source.quote_context.responses[3][0]["item_list"]
        if x["field_id"] != 8072
    ]
    with pytest.raises(RuntimeError, match="c_pay_acq_const_fiolta"):
        source._safe_call("us_cashflow", ts_code="AAPL")

    source = client()
    source.quote_context.responses[1][0]["item_list"][0]["data"] = float("nan")
    with pytest.raises(RuntimeError, match="缺少核心字段.*revenue"):
        source._safe_call("us_income", ts_code="AAPL")


def test_api_error_and_unparseable_dividend_are_explicit():
    source = client()
    source.quote_context.get_market_snapshot = lambda codes: (1, "permission denied")
    with pytest.raises(RuntimeError, match="permission denied"):
        source._safe_call("us_daily", ts_code="AAPL")

    source = client()
    source.quote_context.get_corporate_actions_dividends = lambda code: (
        0, {"dividend_list": [
            {"statement": "1股派息0.25USD", "ex_date": "2025/02/01"},
            {"statement": "unknown", "ex_date": "2025/05/01"},
        ]})
    with pytest.raises(RuntimeError, match="无法解析"):
        source._get_dividends_us("AAPL.US")


def test_current_pe_is_not_copied_into_old_financial_periods():
    source = client()
    source.quote_context.responses[4].append(report(
        {14002: 35.0, 14005: 18.0, 14029: 20.0},
        period="2024/FY", end="2024-12-31"))
    source.assemble_data_pack("AAPL.US")
    metrics = source._store["fina_indicators"].set_index("end_date")
    assert metrics.loc["20251231", "pe_ttm"] == 10.0
    assert pd.isna(metrics.loc["20241231", "pe_ttm"])


def test_partial_current_year_dividend_is_not_used_as_full_year():
    source = client()
    source.quote_context.get_corporate_actions_dividends = lambda code: (
        0, {"dividend_list": [
            {"statement": "1股派息0.50USD", "ex_date": "2025/06/01"},
            {"statement": "1股派息0.25USD", "ex_date": "2026/06/01"},
        ]})
    section = source._get_dividends_us("AAPL.US")
    assert "2026 年内累计" in section
    assert source._store["dividends"]["end_date"].tolist() == ["20251231"]
    assert source._store["dividends"]["cash_div_tax"].tolist() == [0.5]


def test_all_us_cli_entries_select_moomoo_without_tushare_token(monkeypatch, tmp_path):
    import sys
    import buy_sell_inputs
    import buy_sell_plan
    import tushare_collector
    import valuation_engine
    import value_analysis_engine

    selected = []
    source = client()

    def create(provider, code, **kwargs):
        selected.append((provider, code))
        return source

    def forbidden_token():
        raise AssertionError("moomoo path must not read TUSHARE_TOKEN")

    monkeypatch.setattr("data_providers.create_data_client", create)
    for module in (tushare_collector, valuation_engine,
                   value_analysis_engine, buy_sell_plan):
        monkeypatch.setattr(module, "get_token", forbidden_token)

    output = tmp_path / "pack.md"
    monkeypatch.setattr(sys, "argv", ["tushare_collector.py", "--code", "AAPL",
                                         "--provider", "moomoo", "--output", str(output)])
    tushare_collector.main()
    assert "moomoo OpenAPI" in output.read_text(encoding="utf-8")

    monkeypatch.setattr(valuation_engine, "ValuationEngine",
                        lambda *args: SimpleNamespace(run=lambda: "valuation"))
    monkeypatch.setattr(sys, "argv", ["valuation_engine.py", "--code", "AAPL",
                                         "--provider", "moomoo", "--output-dir", str(tmp_path)])
    valuation_engine.main()
    assert (tmp_path / "valuation_computed.md").read_text(encoding="utf-8") == "valuation"

    monkeypatch.setattr(value_analysis_engine, "ValueAnalysisEngine",
                        lambda *args: SimpleNamespace(generate_output=lambda: "value"))
    monkeypatch.setattr(buy_sell_inputs, "export_value", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["value_analysis_engine.py", "--code", "AAPL",
                                         "--provider", "moomoo", "--output-dir", str(tmp_path)])
    value_analysis_engine.main()
    assert (tmp_path / "value_computed.md").read_text(encoding="utf-8") == "value"

    monkeypatch.setattr(buy_sell_plan, "export_market", lambda *args: None)
    monkeypatch.setattr(buy_sell_plan, "run_directory",
                        lambda *args, **kwargs: {"execution": {"action": "HOLD"}})
    monkeypatch.setattr(sys, "argv", ["buy_sell_plan.py", "--code", "AAPL",
                                         "--provider", "moomoo", "--output-dir", str(tmp_path)])
    assert buy_sell_plan.main() == 0
    assert selected == [("moomoo", "AAPL.US")] * 4
