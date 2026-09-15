"""Offline numerical, execution-state, artifact and workflow contract tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from buy_sell_engine import (
    EXIT_CODES, build_plan, main, make_basis, method_cv, render_markdown,
    run_directory, safety_margin, select_basis, write_json,
)
from buy_sell_inputs import export_inputs, financial_period, market_snapshot, value_snapshot
from market_sessions import MARKET_ZONES, daily_close_complete
from value_analysis_engine import ValueAnalysisEngine


ROOT = Path(__file__).resolve().parents[1]
SUBJECT = {"ticker": "600887.SH", "currency": "CNY"}
TODAY = "2026-09-14"


@pytest.fixture
def snapshot():
    return {"schema": "investment.value_snapshot", "schema_version": "1.0",
            "subject": dict(SUBJECT), "as_of": "2026-09-01", "financial_period": "2026-06-30",
            "cycle": None, "values": {"V_bear": 80, "V_base": 100, "V_bull": 140},
            "value_sources": {"V_base": "scenarios[base].per_share"},
            "methods": [{"method": "CashFlow", "intrinsic": 100}, {"method": "DDM", "intrinsic": 100}],
            "risk": {"valuation_mode": "Normal"}}


def market(close=80, **kwargs):
    return {"subject": dict(SUBJECT), "quote_date": TODAY, "close": close,
            "price_basis": "unadjusted", **kwargs}


def state(committed=0, spent=0, session=TODAY, **kwargs):
    return {"subject": dict(SUBJECT), "committed_pct": committed,
            "session_spent_pct": spent, "session_date": session, "exited": False, **kwargs}


def plan(snapshot, close=80, **kwargs):
    return build_plan(make_basis(snapshot), market(close), as_of=TODAY, **kwargs)


def bar(day, close, **kwargs):
    return {"date": day, "close": close, "complete": True, "price_basis": "unadjusted", **kwargs}


@pytest.mark.parametrize("cv,expected", [(0, 20), (15, 20), (15.00001, 30), (30, 30), (30.00001, 40), (100, 40), (None, 50)])
def test_safety_margin_thresholds(cv, expected):
    assert safety_margin(cv, {})["pct"] == expected


def test_sample_cv_matches_existing_engine():
    from valuation_engine import ValuationEngine
    methods = [{"method": "DCF", "intrinsic": 80}, {"method": "DDM", "intrinsic": 120}]
    cv, valid, excluded = method_cv(methods)
    existing = ValuationEngine.cross_validate(None, methods, {"DCF": 50, "DDM": 50})
    assert cv == pytest.approx(28.284271247)
    assert cv == pytest.approx(existing["cv"])
    assert len(valid) == 2 and not excluded


def test_cv_excludes_invalid_and_does_not_report_single_method_as_zero():
    cv, valid, excluded = method_cv([{"method": str(i), "intrinsic": v}
                                    for i, v in enumerate([100, None, 0, -1, float("nan"), float("inf"), True])])
    assert cv is None and len(valid) == 1 and len(excluded) == 6
    assert method_cv([]) == (None, [], [])


def test_duplicate_methods_are_not_independent():
    with pytest.raises(ValueError, match="unique"):
        method_cv([{"method": "DCF", "intrinsic": 100}] * 2)


def test_risk_adjustment_trace_and_clamp():
    margin = safety_margin(20, {"valuation_mode": "Defensive", "missing_methods": ["DDM"],
                                "bank_health_missing": True, "report_notes_missing": True})
    assert margin["pct"] == 50 and margin["unclamped_pct"] == 55
    assert len(margin["adjustments"]) == 4
    assert safety_margin(0, {"valuation_mode": "Conservative"})["pct"] == 25


def test_four_prices_allocation_and_trace(snapshot):
    basis = make_basis(snapshot)
    assert basis["cv_pct"] == 0 and basis["margin"]["pct"] == 20
    assert [t["price"] for t in basis["tiers"]] == [80, 70, 60, 50]
    assert [t["allocation_pct"] for t in basis["tiers"]] == [20, 30, 30, 20]
    assert [t["cumulative_pct"] for t in basis["tiers"]] == [20, 50, 80, 100]
    assert basis["exit_price"] == 200 and basis["exit_components_raw"] == ["200.0", "168.00"]
    assert basis["value_sources"] == snapshot["value_sources"]
    assert len(basis["source_digest"]) == len(basis["basis_id"]) == 64
    assert basis["source_snapshot"] == snapshot


def test_bull_dominates_exit_and_prices_round_conservatively(snapshot):
    snapshot["values"].update(V_base=100.019, V_bull=200.019)
    basis = make_basis(snapshot)
    assert basis["tiers"][0]["price"] == 80.01
    assert basis["exit_price"] == 240.03


@pytest.mark.parametrize("key,value", [("V_bear", None), ("V_base", 0), ("V_base", -1),
                                      ("V_bull", -10), ("V_base", "100"), ("V_base", True)])
def test_invalid_valuation_never_fabricates_prices(snapshot, key, value):
    snapshot["values"][key] = value
    result = plan(snapshot)
    assert result["execution"]["action"] == "BLOCKED"
    assert result["basis"]["tiers"] == [] and result["basis"]["exit_price"] is None
    assert result["execution"]["next_price"] is None


@pytest.mark.parametrize("values", [{"V_bear": 110, "V_base": 100, "V_bull": 140},
                                   {"V_bear": 80, "V_base": 100, "V_bull": 90},
                                   {"V_bear": .001, "V_base": .002, "V_bull": .003},
                                   {"V_bear": 1e307, "V_base": 1e308, "V_bull": 1.7e308}])
def test_unordered_tiny_and_overflowing_prices_block(snapshot, values):
    snapshot["values"] = values
    result = plan(snapshot)
    assert result["execution"]["action"] == "BLOCKED"
    assert result["basis"]["exit_price"] is None


@pytest.mark.parametrize("close,tier,buy,target,next_price", [
    (80.01, 0, 0, 0, 80), (80, 1, 20, 20, 70), (70.01, 1, 20, 20, 70),
    (70, 2, 30, 50, 70), (60, 3, 30, 80, 70), (50, 4, 30, 100, 70), (1, 4, 30, 100, 70),
])
def test_current_tier_boundaries_and_gap_cap(snapshot, close, tier, buy, target, next_price):
    execution = plan(snapshot, close)["execution"]
    assert execution["current_tier"] == tier
    assert execution["buy_now_pct"] == buy
    assert execution["target_cumulative_pct"] == target
    assert execution["next_price"] == next_price
    assert execution["cumulative_after_fill_pct"] == buy
    assert execution["execute"] is (buy > 0)
    assert execution["buy_limit_price"] == ([80, 70, 60, 50][tier - 1] if buy else None)


def test_gap_fill_over_multiple_days_and_no_duplicate_daily_spend(snapshot):
    first = plan(snapshot, 50)["execution"]
    assert first["buy_now_pct"] == 30 and first["pending_eligible_pct"] == 70
    same_day = plan(snapshot, 50, state=state(30, 30))["execution"]
    assert same_day["action"] == "HOLD" and same_day["buy_now_pct"] == 0
    for committed, expected in [(30, 30), (60, 30), (90, 10), (100, 0)]:
        later = plan(snapshot, 50, state=state(committed, min(30, committed), "2026-09-11"))["execution"]
        assert later["buy_now_pct"] == expected
    assert plan(snapshot, 50, state=state(100))["execution"]["next_price"] is None


def test_rebound_does_not_fill_ineligible_tiers_or_sell(snapshot):
    assert plan(snapshot, 80, state=state(30))["execution"]["buy_now_pct"] == 0
    assert plan(snapshot, 190, state=state(100))["execution"]["action"] == "HOLD"
    execution = plan(snapshot, 65, state=state(20, 20))["execution"]
    assert execution["buy_now_pct"] == 10
    assert execution["cumulative_after_fill_pct"] == 30


@pytest.mark.parametrize("changes", [{"committed_pct": -1}, {"committed_pct": 101},
                                    {"session_spent_pct": 31}, {"committed_pct": True},
                                    {"committed_pct": 10, "session_spent_pct": 20},
                                    {"session_date": "2027-01-01"}, {"session_date": "bad"},
                                    {"exited": "false"}, {"committed_pct": 20, "session_spent_pct": 20, "session_date": None}])
def test_invalid_execution_state_is_rejected(snapshot, changes):
    payload = state()
    payload.update(changes)
    with pytest.raises(ValueError, match="execution state"):
        plan(snapshot, state=payload)


@pytest.mark.parametrize("changes", [{"close": None}, {"close": -1}, {"close": 0},
                                    {"quote_date": None}, {"quote_date": "2026-09-01"},
                                    {"quote_date": "2026-09-15"}, {"price_basis": "adjusted"}])
def test_missing_stale_or_incompatible_market_blocks(snapshot, changes):
    result = build_plan(make_basis(snapshot), market(**changes), as_of=TODAY)
    assert result["execution"]["action"] == "BLOCKED"
    assert result["execution"]["buy_now_pct"] == 0
    assert result["blockers"]


def test_two_day_exit_with_weekend_and_threshold_equality(snapshot):
    quotes = market(200, daily_closes=[bar("2026-09-11", 205),
                                     bar(TODAY, 200, previous_session="2026-09-11")])
    result = build_plan(make_basis(snapshot), quotes, as_of=TODAY, state=state(60))
    assert result["execution"]["action"] == "SELL_ALL"
    assert result["execution"]["sell_position_pct"] == 100
    assert result["exit_confirmation"]["frequency"] == "daily"
    assert result["execution"]["next_price"] is None
    assert result["execution"]["execute"] is True
    assert result["execution"]["buy_limit_price"] is None
    assert result["execution"]["cumulative_after_fill_pct"] == 0


@pytest.mark.parametrize("change", ["one_day", "low_previous", "incomplete", "adjusted", "duplicate", "not_consecutive", "future", "stale"])
def test_unconfirmed_daily_high_price_does_not_sell(snapshot, change):
    rows = [bar("2026-09-11", 200), bar(TODAY, 200, previous_session="2026-09-11")]
    if change == "one_day":
        rows.pop(0)
    elif change == "low_previous":
        rows[0]["close"] = 199.99
    elif change == "incomplete":
        rows[1]["complete"] = False
    elif change == "adjusted":
        rows[0]["price_basis"] = "adjusted"
    elif change == "duplicate":
        rows[0]["date"] = TODAY
    elif change == "not_consecutive":
        rows[1]["previous_session"] = "2026-09-10"
    elif change == "future":
        rows[1]["date"] = "2026-09-15"
    else:
        rows[0]["date"], rows[1]["date"] = "2026-09-01", "2026-09-02"
    result = build_plan(make_basis(snapshot), market(210, daily_closes=rows), as_of=TODAY)
    assert result["execution"]["action"] == "HOLD" and not result["exit_confirmation"]


def test_weekly_confirmation_and_current_price_recheck(snapshot):
    quotes = market(205, weekly_closes=[bar("2026-09-11", 200)])
    result = build_plan(make_basis(snapshot), quotes, as_of=TODAY)
    assert result["execution"]["action"] == "SELL_ALL"
    assert result["exit_confirmation"]["frequency"] == "weekly"
    quotes["close"] = 199
    assert build_plan(make_basis(snapshot), quotes, as_of=TODAY)["execution"]["action"] == "HOLD"


@pytest.mark.parametrize("row", [bar("2026-08-28", 200), bar("2026-09-18", 200),
                                bar("2026-09-11", 200, complete=False),
                                bar("2026-09-11", 200, price_basis="adjusted"),
                                bar("2026-09-11", 199.99)])
def test_weekly_confirmation_rejects_unsafe_bars(snapshot, row):
    result = build_plan(make_basis(snapshot), market(210, weekly_closes=[row]), as_of=TODAY)
    assert result["execution"]["action"] == "HOLD"


@pytest.mark.parametrize("code", sorted(EXIT_CODES))
def test_hard_exit_overrides_missing_valuation_and_price(snapshot, code):
    snapshot["values"]["V_base"] = None
    risks = {"subject": SUBJECT, "events": [{"code": code, "confirmed": True,
                                             "observed_at": TODAY, "evidence": ["report:page-42"]}]}
    result = plan(snapshot, None, risk_events=risks)
    assert result["execution"]["action"] == "SELL_ALL"
    assert result["execution"]["sell_position_pct"] == 100
    assert result["basis"]["exit_price"] is None


def test_structured_integrity_hard_exit_and_legacy_not_parsed(snapshot):
    qualitative = {"subject": {"ticker": SUBJECT["ticker"]}, "source": "structured",
                   "parameters": {"integrity_rating": "不可靠"}}
    assert plan(snapshot, qualitative=qualitative)["execution"]["action"] == "SELL_ALL"
    qualitative["source"] = "legacy"
    assert plan(snapshot, qualitative=qualitative)["execution"]["action"] == "BUY"
    qualitative["source"] = "unavailable"
    assert plan(snapshot, qualitative=qualitative)["execution"]["action"] == "BLOCKED"


def test_unconfirmed_risk_does_not_trigger_and_exited_state_blocks_reentry(snapshot):
    events = {"subject": SUBJECT, "events": [{"code": "insolvency", "confirmed": False,
                                              "observed_at": TODAY, "evidence": ["report:42"]}]}
    assert plan(snapshot, risk_events=events)["execution"]["action"] == "BUY"
    assert plan(snapshot, state=state(exited=True))["execution"]["action"] == "DO_NOT_BUY"
    events["events"][0]["confirmed"] = "false"
    with pytest.raises(ValueError, match="hard exit"):
        plan(snapshot, risk_events=events)


@pytest.mark.parametrize("exit_kind", ["hard", "price"])
@pytest.mark.parametrize("position,expected_action,execute,status", [
    (None, "SELL_ALL", False, "unknown"), (0, "DO_NOT_BUY", False, "flat"),
    (60, "SELL_ALL", True, "long"),
])
def test_exit_respects_known_empty_and_unknown_positions(snapshot, exit_kind, position, expected_action, execute, status):
    risks = {"subject": SUBJECT, "events": [{"code": "insolvency", "confirmed": True,
                                              "observed_at": TODAY, "evidence": ["report:42"]}]}
    quotes = market(210, daily_closes=[bar("2026-09-11", 200), bar(TODAY, 210, previous_session="2026-09-11")])
    result = build_plan(make_basis(snapshot), quotes if exit_kind == "price" else market(None),
                        as_of=TODAY, state=None if position is None else state(position),
                        risk_events=risks if exit_kind == "hard" else None)
    execution = result["execution"]
    assert execution["action"] == expected_action
    assert execution["execute"] is execute
    assert execution["position_status"] == status
    assert execution["buy_now_pct"] == 0 and execution["buy_limit_price"] is None
    if position == 0:
        assert execution["sell_position_pct"] == 0
        assert "no_position" in execution["reason"]
        assert "无持仓可卖" in render_markdown(result)
    if position is None:
        assert any("sell_requires_execution_state" in warning for warning in result["warnings"])
        assert "待核实持仓" in render_markdown(result)


@pytest.mark.parametrize("currency,ticker", [("CNY", "600887.SH"), ("HKD", "00700.HK"), ("USD", "AAPL.US")])
def test_currency_is_preserved_and_cross_subject_inputs_rejected(snapshot, currency, ticker):
    snapshot["subject"] = {"ticker": ticker, "currency": currency}
    quotes = market()
    quotes["subject"] = snapshot["subject"]
    result = build_plan(make_basis(snapshot), quotes, as_of=TODAY)
    assert result["basis"]["tiers"][0]["price"] == 80
    assert result["subject"]["currency"] == currency
    quotes["subject"] = {"ticker": "WRONG", "currency": currency}
    with pytest.raises(ValueError, match="mismatch"):
        build_plan(make_basis(snapshot), quotes, as_of=TODAY)


def test_frozen_basis_survives_daily_revaluation_and_updates_on_report_or_cycle(snapshot):
    initial = make_basis(snapshot)
    changed = deepcopy(snapshot)
    changed["values"]["V_base"] = 120
    changed["as_of"] = TODAY
    changed["risk"]["valuation_mode"] = "Defensive"
    assert select_basis(changed, initial) == initial
    changed["financial_period"] = "2026-09-01"
    new = select_basis(changed, initial)
    assert new["values"]["V_base"] == 120 and new["margin"]["pct"] == 30
    changed["financial_period"] = snapshot["financial_period"]
    changed["cycle"] = "2026-09-14-reassessment"
    assert select_basis(changed, initial)["values"]["V_base"] == 120


def test_basis_tampering_period_regression_and_future_valuation(snapshot):
    initial = make_basis(snapshot)
    broken = deepcopy(initial)
    broken["tiers"][0]["price"] = 123
    with pytest.raises(ValueError, match="modified"):
        select_basis(snapshot, broken)
    for period in (None, "2025-12-31"):
        changed = {**snapshot, "financial_period": period}
        with pytest.raises(ValueError, match="older"):
            select_basis(changed, initial)
    result = build_plan(initial, market(), as_of="2026-08-31")
    assert "valuation_from_future" in result["blockers"]


@pytest.mark.parametrize("changes", [{"financial_period": None}, {"as_of": None},
                                    {"financial_period": "2026-12-31"}])
def test_missing_or_impossible_valuation_dates_block(snapshot, changes):
    snapshot.update(changes)
    result = plan(snapshot)
    assert result["execution"]["action"] == "BLOCKED"
    assert result["basis"]["tiers"] == []


def test_offline_cli_artifacts_replay_and_exit_codes(snapshot, tmp_path):
    write_json(tmp_path / "value_computed.json", snapshot)
    write_json(tmp_path / "buy_sell_market.json", market(50))
    assert main(["--output-dir", str(tmp_path), "--as-of", TODAY]) == 0
    first = (tmp_path / "buy_sell_plan.json").read_bytes()
    run_directory(tmp_path, as_of=TODAY)
    assert (tmp_path / "buy_sell_plan.json").read_bytes() == first
    result = json.loads(first)
    markdown = (tmp_path / "buy_sell_plan.md").read_text()
    assert markdown == render_markdown(result)
    assert "本次买入限价：50.00 元/股" in markdown
    for text in ("80.00 元/股", "70.00 元/股", "60.00 元/股", "50.00 元/股", "200.00 元/股", "30%", result["basis"]["basis_id"]):
        assert text in markdown
    write_json(tmp_path / "buy_sell_market.json", market(None))
    assert main(["--output-dir", str(tmp_path), "--as-of", TODAY]) == 3
    (tmp_path / "buy_sell_state.json").write_text("[]")
    with pytest.raises(SystemExit) as exc:
        main(["--output-dir", str(tmp_path), "--as-of", TODAY])
    assert exc.value.code == 2


@pytest.fixture
def value_engine(tmp_path, load_mock_response):
    from tushare_collector import TushareClient
    with patch("tushare_collector.ts") as ts:
        ts.pro_api.return_value = MagicMock()
        client = TushareClient("offline-test")
    fixtures = {"income": "income.json", "balance_sheet": "balancesheet.json", "cashflow": "cashflow.json",
                "dividends": "dividend.json", "basic_info": "daily_basic.json",
                "weekly_prices": "weekly.json", "fina_indicators": "fina_indicator.json", "risk_free_rate": "yc_cb.json"}
    for key, filename in fixtures.items():
        content = load_mock_response(filename)
        frame = pd.DataFrame(content if isinstance(content, list) else [content])
        if "end_date" in frame:
            frame = frame.sort_values("end_date", ascending=False)
        client._store[key] = frame
    return ValueAnalysisEngine("600887.SH", str(tmp_path), client)


def test_real_value_engine_adapter_exports_without_network(value_engine, tmp_path):
    markdown = value_engine.generate_output()
    export_inputs(value_engine, now=datetime(2026, 9, 14, 10, tzinfo=timezone.utc))
    snapshot = json.loads((tmp_path / "value_computed.json").read_text())
    original = {r["scenario"]: r["per_share"] for r in value_engine.computed["scenarios"]}
    assert snapshot["values"]["V_base"] == original["基准"]
    assert snapshot["values"]["V_bear"] == original["保守"]
    assert snapshot["values"]["V_bull"] == original.get("乐观", original.get("乐观(受限)"))
    assert len(snapshot["methods"]) >= 2
    assert "DCF" not in [row["method"] for row in snapshot["methods"]]
    assert "情景估值" in markdown
    quotes = market(snapshot["values"]["V_base"] / 2)
    write_json(tmp_path / "buy_sell_market.json", quotes)
    result = run_directory(tmp_path, as_of=TODAY)
    assert result["basis"]["values"] == snapshot["values"]
    assert result["execution"]["action"] in {"BUY", "HOLD"}


def test_value_cli_writes_required_plan_inputs(value_engine, tmp_path, monkeypatch):
    import value_analysis_engine
    client = value_engine.client
    monkeypatch.setattr(client, "assemble_data_pack", MagicMock(return_value="offline pack"))
    monkeypatch.setattr(value_analysis_engine, "get_token", lambda: "offline-test")
    monkeypatch.setattr("sys.argv", ["value_analysis_engine.py", "--code", "600887", "--output-dir", str(tmp_path)])
    with patch("tushare_collector.TushareClient", return_value=client):
        value_analysis_engine.main()
    assert (tmp_path / "value_computed.md").exists()
    assert (tmp_path / "value_computed.json").exists()
    assert (tmp_path / "buy_sell_market.json").exists()
    client.assemble_data_pack.assert_called_once_with("600887.SH")


def test_bank_uses_residual_income_not_industrial_methods(value_engine):
    value_engine.generate_output()
    computed = deepcopy(value_engine.computed)
    computed["sector_profile"] = {"regulated_financial": True, "bank_like": True, "valuation_family": "ResidualIncome"}
    computed["bank_health"] = {"available": False}
    with patch.object(value_engine.valuation_engine, "classify", side_effect=AssertionError("industrial model")):
        result = value_snapshot(value_engine, computed, as_of=TODAY)
    assert [m["method"] for m in result["methods"]] == ["ResidualIncome"]
    assert make_basis(result)["cv_pct"] is None
    assert result["risk"]["bank_health_missing"] is True


def test_market_adapter_preserves_dates_and_filters_incomplete_week(value_engine):
    value_engine.client._store["basic_info"] = pd.DataFrame([{"close": 210, "trade_date": "20260914"}])
    value_engine.client._store["daily_prices"] = pd.DataFrame([
        {"trade_date": "20260911", "close": 210}, {"trade_date": "20260914", "close": 205}])
    value_engine.client._store["weekly_prices"] = pd.DataFrame([
        {"trade_date": "20260911", "close": 210}, {"trade_date": "20260918", "close": 220}])
    quotes = market_snapshot(value_engine, now=datetime(2026, 9, 14, 6, tzinfo=timezone.utc))
    assert quotes["quote_date"] == TODAY
    assert quotes["daily_closes"][-1]["complete"] is False
    assert quotes["daily_closes"][-1]["previous_session"] == "2026-09-11"
    assert len(quotes["weekly_closes"]) == 1
    quotes = market_snapshot(value_engine, now=datetime(2026, 9, 14, 9, tzinfo=timezone.utc))
    assert quotes["daily_closes"][-1]["complete"] is True


def test_hk_market_without_timestamp_is_not_silently_dated_today():
    engine = SimpleNamespace(ts_code="00700.HK", market="HK", client=SimpleNamespace(_store={
        "basic_info": pd.DataFrame([{"close": 400, "end_date": "20260630"}]),
        "weekly_prices": pd.DataFrame([{"trade_date": "20260907", "close": 400}])}))
    quotes = market_snapshot(engine)
    assert quotes["close"] == 400 and quotes["quote_date"] is None
    assert quotes["weekly_closes"] == []


@pytest.mark.parametrize("ticker,expected_date", [("00700.HK", "2026-09-15"), ("AAPL.US", "2026-09-14")])
def test_actual_yfinance_market_path_captures_quote_date(value_engine, ticker, expected_date):
    client = value_engine.client
    client._yf_available = True
    timestamp = datetime(2026, 9, 14, 23, tzinfo=timezone.utc).timestamp()
    with patch("tushare_collector.yf") as yf:
        yf.Ticker.return_value.info = {"regularMarketPrice": 210, "regularMarketTime": timestamp}
        result = client._yf_hk_market_data(ticker)
    assert result["close"] == 210
    assert client._store["buy_sell_quote"] == {"close": 210, "quote_date": expected_date}
    with patch("tushare_collector.yf") as yf:
        yf.Ticker.return_value.info = {"previousClose": 200}
        result = client._yf_hk_market_data(ticker)
    assert result["close"] == 200
    assert client._store["buy_sell_quote"]["quote_date"] is None


def test_newer_daily_quote_is_used_without_fabricating_timestamp(value_engine):
    value_engine.client._store["basic_info"] = pd.DataFrame([{"trade_date": "20260910", "close": 90}])
    value_engine.client._store["daily_prices"] = pd.DataFrame([{"trade_date": "20260911", "close": 100}])
    quotes = market_snapshot(value_engine, now=datetime(2026, 9, 14, 10, tzinfo=timezone.utc))
    assert quotes["close"] == 100 and quotes["quote_date"] == "2026-09-11"


@pytest.fixture(params=[("HK", "00700.HK", "HKD"), ("US", "AAPL.US", "USD")])
def international_market(request, value_engine, snapshot):
    market_name, ticker, currency = request.param
    client = value_engine.client
    client._yf_available = True
    client._store.clear()
    # The existing US basic-info path provides only one daily row.
    client._store["daily_prices"] = pd.DataFrame([{"trade_date": "20260914", "close": 210}])
    client._store["weekly_prices"] = pd.DataFrame([{"trade_date": "20260907", "close": 210}])
    snapshot["subject"] = {"ticker": ticker, "currency": currency}
    engine = SimpleNamespace(ts_code=ticker, market=market_name, client=client)
    return engine, make_basis(snapshot), ZoneInfo(MARKET_ZONES[market_name])


def collect_international(engine, now, history, *, timestamp=True, fail_history=False):
    with patch("tushare_collector.yf") as yf, patch.object(engine.client, "_safe_call", return_value=pd.DataFrame()):
        ticker = yf.Ticker.return_value
        ticker.info = {"regularMarketPrice": 210}
        if timestamp:
            ticker.info["regularMarketTime"] = now.timestamp()
        ticker.history.side_effect = TimeoutError("offline fixture") if fail_history else None
        ticker.history.return_value = history
        rendered = engine.client.get_market_data(engine.ts_code)
        assert "210.00" in rendered
        quotes = market_snapshot(engine, now=now)
        ticker.history.assert_called_once_with(period="1mo", interval="1d", auto_adjust=False,
                                               back_adjust=False, actions=False, prepost=False)
    return quotes


@pytest.mark.parametrize("hour,prices,expected", [
    (17, [190, 190, 205, 210], "SELL_ALL"),
    (15, [190, 190, 205, 210], "HOLD"),
    (15, [190, 205, 205, 210], "SELL_ALL"),
])
def test_default_hk_us_collection_to_exit_confirmation(international_market, hour, prices, expected):
    engine, basis, zone = international_market
    now = datetime(2026, 9, 14, hour, tzinfo=zone)
    # UTC indices must be converted before extracting HK/US session labels.
    dates = pd.DatetimeIndex(["2026-09-09", "2026-09-10", "2026-09-11", TODAY], tz=zone).tz_convert("UTC")
    history = pd.DataFrame({"Close": prices, "Adj Close": [100] * 4}, index=dates)
    quotes = collect_international(engine, now, history)
    assert len(quotes["daily_closes"]) == 4
    assert quotes["daily_closes"][-1]["date"] == TODAY
    assert quotes["daily_closes"][-1]["complete"] is (hour == 17)
    assert quotes["weekly_closes"] == []
    assert quotes["daily_source"] == "yfinance.history(1d,auto_adjust=False)"
    execution_state = {**state(60), "subject": basis["subject"]}
    result = build_plan(basis, quotes, as_of=TODAY, state=execution_state)
    assert result["execution"]["action"] == expected
    if expected == "SELL_ALL":
        assert result["execution"]["execute"] is True
        assert result["exit_confirmation"]["frequency"] == "daily"
        assert all(row["complete"] for row in result["exit_confirmation"]["closes"])
    else:
        assert result["exit_confirmation"] is None


@pytest.mark.parametrize("failure", ["empty", "timeout", "one_day", "missing_close", "duplicate"])
def test_hk_us_missing_or_invalid_history_cannot_confirm(international_market, failure):
    engine, basis, zone = international_market
    now = datetime(2026, 9, 14, 17, tzinfo=zone)
    days = [TODAY] if failure == "one_day" else ["2026-09-11", TODAY]
    if failure == "duplicate":
        days = [TODAY, TODAY]
    history = pd.DataFrame({"Close": [210] * len(days)}, index=pd.DatetimeIndex(days, tz=zone))
    if failure in {"empty", "timeout"}:
        history = pd.DataFrame()
    elif failure == "missing_close":
        history = history.rename(columns={"Close": "Adj Close"})
    quotes = collect_international(engine, now, history, fail_history=failure == "timeout")
    result = build_plan(basis, quotes, as_of=TODAY, state={**state(60), "subject": basis["subject"]})
    assert result["execution"]["action"] == "HOLD"
    assert result["exit_confirmation"] is None
    if failure != "duplicate":
        assert "insufficient_complete_daily_closes" in result["warnings"]


def test_hk_us_history_supplies_actual_date_when_quote_timestamp_missing(international_market):
    engine, basis, zone = international_market
    history = pd.DataFrame({"Close": [205, 210]}, index=pd.DatetimeIndex(["2026-09-10", "2026-09-11"]))
    now = datetime(2026, 9, 14, 10, tzinfo=zone)
    quotes = collect_international(engine, now, history, timestamp=False)
    assert quotes["quote_date"] == "2026-09-11"
    assert quotes["session_date"] == TODAY
    assert build_plan(basis, quotes, as_of=TODAY)["exit_confirmation"]["frequency"] == "daily"


@pytest.mark.parametrize("market_name,instant,complete", [
    ("A", "2026-09-14T06:59:59+00:00", False), ("A", "2026-09-14T07:00:00+00:00", True),
    ("HK", "2026-09-14T08:05:00+00:00", False), ("HK", "2026-09-14T08:10:00+00:00", True),
    ("US", "2026-09-14T19:59:59+00:00", False), ("US", "2026-09-14T20:00:00+00:00", True),
    ("US", "2026-12-14T20:30:00+00:00", False), ("US", "2026-12-14T21:00:00+00:00", True),
])
def test_exchange_close_cutoffs_and_us_dst(market_name, instant, complete):
    now = datetime.fromisoformat(instant)
    assert daily_close_complete(now.date(), market_name, now) is complete


@pytest.mark.parametrize("market_name,currency,ticker,machine_zone", [
    ("US", "USD", "AAPL.US", "Asia/Shanghai"),
    ("HK", "HKD", "00700.HK", "America/Los_Angeles"),
    ("A", "CNY", "600887.SH", "America/Los_Angeles"),
])
def test_cli_session_cap_survives_machine_midnight(snapshot, tmp_path, monkeypatch, market_name, currency, ticker, machine_zone):
    subject = {"ticker": ticker, "currency": currency}
    snapshot["subject"] = subject
    write_json(tmp_path / "value_computed.json", snapshot)
    before_midnight = datetime(2026, 9, 14, 23, 59, tzinfo=ZoneInfo(machine_zone))
    session = before_midnight.astimezone(ZoneInfo(MARKET_ZONES[market_name])).date().isoformat()
    write_json(tmp_path / "buy_sell_state.json", {**state(30, 30, session), "subject": subject})
    write_json(tmp_path / "buy_sell_market.json", {**market(50, quote_date=session), "subject": subject})
    old_tz = os.environ.get("TZ")
    try:
        monkeypatch.setenv("TZ", machine_zone)
        time.tzset()
        for instant in (before_midnight, before_midnight + timedelta(minutes=2)):
            with patch("market_sessions.datetime", wraps=datetime) as clock:
                clock.now.return_value = instant.astimezone(timezone.utc)
                assert main(["--output-dir", str(tmp_path)]) == 0
                engine = SimpleNamespace(ts_code=ticker, market=market_name, client=SimpleNamespace(_store={}))
                quotes = market_snapshot(engine)
            result = json.loads((tmp_path / "buy_sell_plan.json").read_text())
            assert result["as_of"] == quotes["session_date"] == session
            assert result["execution"]["session_timezone"] == quotes["session_timezone"]
            assert result["execution"]["session_spent_pct"] == 30
            assert result["execution"]["action"] == "HOLD"
        next_session = (datetime.fromisoformat(session) + timedelta(days=1)).date().isoformat()
        with patch("market_sessions.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2030, 1, 1, tzinfo=timezone.utc)
            assert main(["--output-dir", str(tmp_path), "--as-of", next_session]) == 0
        replay = json.loads((tmp_path / "buy_sell_plan.json").read_text())
        assert replay["execution"]["session_date"] == next_session
        assert replay["execution"]["buy_now_pct"] == 30
    finally:
        if old_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", old_tz)
        time.tzset()


def test_value_export_uses_exchange_date_across_utc_midnight(value_engine, tmp_path):
    value_engine.generate_output()
    value_engine.market, value_engine.ts_code = "US", "AAPL.US"
    export_inputs(value_engine, now=datetime(2026, 9, 15, 1, tzinfo=timezone.utc))
    valuation = json.loads((tmp_path / "value_computed.json").read_text())
    quotes = json.loads((tmp_path / "buy_sell_market.json").read_text())
    assert valuation["as_of"] == quotes["session_date"] == TODAY


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_json_fails_without_publishing_fake_prices(snapshot, tmp_path, bad):
    snapshot["values"]["V_base"] = bad
    (tmp_path / "value_computed.json").write_text(json.dumps(snapshot))
    write_json(tmp_path / "buy_sell_market.json", market())
    with pytest.raises(SystemExit) as exc:
        main(["--output-dir", str(tmp_path), "--as-of", TODAY])
    assert exc.value.code == 2
    assert not (tmp_path / "buy_sell_plan.json").exists()


def test_export_cycle_is_retained_across_daily_precomputes(value_engine, tmp_path):
    value_engine.generate_output()
    now = datetime(2026, 9, 14, 10, tzinfo=timezone.utc)
    export_inputs(value_engine, cycle="2026-09-14-review", now=now)
    export_inputs(value_engine, now=now)
    assert json.loads((tmp_path / "value_computed.json").read_text())["cycle"] == "2026-09-14-review"


def test_financial_period_includes_new_interim_report(value_engine):
    value_engine.client._store["income"] = pd.DataFrame([{"end_date": "20260930"}, {"end_date": "20251231"}])
    assert financial_period(value_engine.client) == "2026-09-30"


def test_commands_and_report_require_engine_artifacts_without_recalculation():
    for directory in (".claude/commands", ".opencode/commands"):
        command = (ROOT / directory / "value-analysis.md").read_text()
        assert 'scripts/buy_sell_engine.py --output-dir "{output_dir}"' in command
        for token in ("buy_sell_plan.json", "buy_sell_plan.md", "verbatim", "without recalculating", "buy_sell_state.json", "buy_sell_risk.json"):
            assert token in command
        valuation = (ROOT / directory / "valuation.md").read_text()
        assert "does not create or overwrite" in valuation and "/value-analysis" in valuation
    for filename in ("coordinator.md", "phase2_value_analysis.md", "references/report_template.md"):
        content = (ROOT / "strategies/value" / filename).read_text()
        assert "buy_sell_plan" in content and "原样" in content and "重算" in content
