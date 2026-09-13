"""Adapters from existing collector/valuation contracts to offline plan inputs."""

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from buy_sell_engine import iso_date, number, write_json


def financial_period(client):
    dates = []
    for key in ("income", "balance_sheet", "cashflow", "fina_indicators"):
        frame = client._store.get(key)
        if frame is not None and "end_date" in frame:
            dates.extend(day for value in frame["end_date"] if (day := iso_date(str(value))))
    return max(dates).isoformat() if dates else None


def value_snapshot(engine, computed, *, as_of, cycle=None):
    scenarios = {row["scenario"]: row for row in computed["scenarios"]}
    fields = {"V_bear": "保守", "V_base": "基准",
              "V_bull": "乐观" if "乐观" in scenarios else "乐观(受限)"}
    values = {key: number(scenarios.get(label, {}).get("per_share")) for key, label in fields.items()}
    primary = computed["sector_profile"]["valuation_family"]
    methods = [{"method": primary, "intrinsic": values["V_base"]}]
    missing = []
    # The primary cash-flow model replaces generic DCF, not an extra CV sample.
    # Ordinary industrial comparables are not appropriate for regulated financials.
    if not computed["sector_profile"]["regulated_financial"]:
        valuation = engine.valuation_engine
        cls = valuation.classify()
        method_map = {"DDM": lambda: valuation.ddm(computed["wacc_data"]["ke"]),
                      "PE_Band": valuation.pe_band, "PEG": valuation.peg, "PS": valuation.ps}
        for name in cls["methods"]:
            if name not in method_map:
                continue
            try:
                result = method_map[name]()
            except (ArithmeticError, ValueError, KeyError, TypeError):
                result = None
            intrinsic = number(result.get("intrinsic")) if result else None
            methods.append({"method": name, "intrinsic": intrinsic})
            if intrinsic is None or intrinsic <= 0:
                missing.append(name)
    return {"schema": "investment.value_snapshot", "schema_version": "1.0",
            "subject": {"ticker": engine.ts_code,
                        "currency": {"A": "CNY", "HK": "HKD", "US": "USD"}[engine.market]},
            "as_of": as_of, "financial_period": financial_period(engine.client), "cycle": cycle,
            "values": values, "value_sources": {key: f"value_analysis_engine.scenarios[{label}].per_share"
                                                 for key, label in fields.items()},
            "methods": methods,
            "risk": {"valuation_mode": computed["guardrails"]["valuation_mode"],
                     "missing_methods": missing,
                     "bank_health_missing": computed["sector_profile"].get("bank_like", False)
                     and not (computed["bank_health"] or {}).get("available", False),
                     "report_notes_missing": not (Path(engine.output_dir) / "data_pack_report.md").is_file()},
            "scenarios": computed["scenarios"]}


def market_snapshot(engine, *, now=None):
    now = now or datetime.now().astimezone()
    local = now.astimezone(ZoneInfo({"A": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}[engine.market]))
    today = local.date()
    basic = engine.client._store.get("basic_info")
    row = basic.iloc[0] if basic is not None and not basic.empty else {}
    quote_date = iso_date(str(row.get("trade_date", "")))
    quote = engine.client._store.get("buy_sell_quote", {})
    if engine.market == "HK":
        quote_date = iso_date(quote.get("quote_date"))
    close = number(quote.get("close")) if quote else number(row.get("close"))
    if quote:
        quote_date = iso_date(quote.get("quote_date"))
    daily = engine.client._store.get("daily_prices")
    daily_rows = []
    if daily is not None and not daily.empty and "trade_date" in daily:
        ordered = daily.sort_values("trade_date").drop_duplicates("trade_date")
        previous = None
        for _, item in ordered.iterrows():
            day = iso_date(str(item.get("trade_date")))
            if day is None:
                continue
            daily_rows.append({"date": day.isoformat(), "close": number(item.get("close")),
                               "complete": day < today or (day == today and local.time() >= time(16)),
                               "previous_session": previous, "price_basis": "unadjusted"})
            previous = day.isoformat()
        if daily_rows:
            latest = daily_rows[-1]
            latest_date = iso_date(latest["date"])
            if latest["close"] is not None and (quote_date is None or latest_date > quote_date):
                close, quote_date = latest["close"], latest_date
    weekly_rows = []
    weekly = engine.client._store.get("weekly_prices")
    if engine.market == "A" and weekly is not None and "trade_date" in weekly:
        for _, item in weekly.iterrows():
            day = iso_date(str(item.get("trade_date")))
            # Only completed Friday-labelled Tushare bars. Holiday weeks may use daily confirmation.
            if day and day.weekday() == 4 and day < today:
                weekly_rows.append({"date": day.isoformat(), "close": number(item.get("close")),
                                    "complete": True, "price_basis": "unadjusted"})
    return {"schema": "investment.buy_sell_market", "schema_version": "1.0",
            "subject": {"ticker": engine.ts_code,
                        "currency": {"A": "CNY", "HK": "HKD", "US": "USD"}[engine.market]},
            "retrieved_at": now.isoformat(), "quote_date": quote_date.isoformat() if quote_date else None,
            "close": close, "price_basis": "unadjusted", "source": "collector._store",
            "daily_closes": daily_rows[-3:], "weekly_closes": sorted(weekly_rows, key=lambda r: r["date"])[-2:]}


def export_inputs(engine, *, cycle=None, now=None):
    now = now or datetime.now().astimezone()
    root = Path(engine.output_dir)
    previous_path = root / "value_computed.json"
    if cycle is None and previous_path.exists():
        from buy_sell_engine import load_json
        cycle = load_json(previous_path).get("cycle")
    snapshot = value_snapshot(engine, engine.computed, as_of=now.date().isoformat(), cycle=cycle)
    write_json(root / "value_computed.json", snapshot)
    write_json(root / "buy_sell_market.json", market_snapshot(engine, now=now))
