"""Quota profiles and deterministic collection estimates."""

from __future__ import annotations

PROFILES = {
    "frugal": ("stock_basic", "daily", "income", "balancesheet", "cashflow", "fina_indicator"),
    # REQ-011：`bulk` 的语义是「扫描得到的全集」，而 `hk_basic` / `us_basic` 一直被
    # `get_basic_info` 调用却不在任何档位里——港股/美股的基本信息因此永远进不了存档。
    # 这里补齐（对 A 股标的会被市场过滤掉，故既有目标集合不变）。
    # `tests/test_data_pull.py` 断言它与 `datalayer.registry.PROFILES` 及扫描集合一致。
    "bulk": ("stock_basic", "hk_basic", "us_basic", "daily", "daily_basic", "income",
             "balancesheet", "cashflow", "dividend", "fina_audit", "fina_indicator",
             "fina_mainbz", "pledge_stat", "repurchase", "top10_holders", "weekly", "yc_cb",
             "hk_balancesheet", "hk_cashflow", "hk_daily", "hk_fina_indicator", "hk_income",
             "us_balancesheet", "us_cashflow", "us_daily", "us_fina_indicator", "us_income"),
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
