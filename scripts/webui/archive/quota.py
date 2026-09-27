"""Quota profiles and deterministic collection estimates."""

from __future__ import annotations

PROFILES = {
    "frugal": ("stock_basic", "daily", "income", "balancesheet", "cashflow", "fina_indicator"),
    "bulk": ("stock_basic", "daily", "daily_basic", "income", "balancesheet", "cashflow",
             "dividend", "fina_audit", "fina_indicator", "fina_mainbz", "pledge_stat",
             "repurchase", "top10_holders", "weekly", "yc_cb", "hk_balancesheet",
             "hk_cashflow", "hk_daily", "hk_fina_indicator", "hk_income", "us_balancesheet",
             "us_cashflow", "us_daily", "us_fina_indicator", "us_income"),
}


def estimate_calls(targets) -> int:
    return len(tuple(targets))


def targets_for_profile(tickers, periods, profile, api_params=None):
    if profile not in PROFILES:
        raise ValueError(f"未知配额档案：{profile}")
    params_by_api = api_params or {}
    targets = []
    selected_periods = tuple(periods)
    for ticker in tickers:
        market = str(ticker).upper().rsplit(".", 1)[-1] if "." in str(ticker) else ""
        selected_apis = tuple(
            api for api in PROFILES[profile]
            if (not api.startswith("hk_") or market == "HK")
            and (not api.startswith("us_") or market in ("US", "NYSE", "NASDAQ"))
            and (api != "stock_basic" or market in ("SH", "SZ", ""))
        )
        periods_for_profile = (
            (max(selected_periods),) if profile == "frugal" and selected_periods else selected_periods
        )
        for api in selected_apis:
            api_periods = selected_periods if api in {
                "income", "balancesheet", "cashflow", "fina_indicator", "fina_audit", "fina_mainbz",
                "hk_income", "hk_balancesheet", "hk_cashflow", "hk_fina_indicator",
                "us_income", "us_balancesheet", "us_cashflow", "us_fina_indicator"
            } else ("latest",)
            if profile == "frugal" and api in {
                "income", "balancesheet", "cashflow", "fina_indicator", "fina_audit", "fina_mainbz",
                "hk_income", "hk_balancesheet", "hk_cashflow", "hk_fina_indicator",
                "us_income", "us_balancesheet", "us_cashflow", "us_fina_indicator"
            }:
                api_periods = periods_for_profile
            for period in api_periods:
                params = dict(params_by_api.get(api, {}))
                params.setdefault("ts_code", ticker)
                if period != "latest":
                    params.setdefault("period", period)
                targets.append({"ticker": ticker, "dataset": api, "period": period, "params": params})
    return targets
