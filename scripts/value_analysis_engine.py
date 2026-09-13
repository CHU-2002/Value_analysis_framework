#!/usr/bin/env python3
"""Deterministic precompute engine for value-analysis.

Computes business-quality-aware valuation anchors that the LLM can reuse
without redoing arithmetic. The goal is to keep the value-analysis pipeline's
strong separation between deterministic computation and qualitative judgment.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import os
import re
import statistics
import sys

from config import get_token, validate_stock_code
from format_utils import format_header, format_number, format_table


REGULATED_FINANCIAL_INDUSTRIES = {"银行", "保险", "证券", "多元金融"}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


class ValueAnalysisEngine:
    """Precompute deterministic anchors for value-analysis."""

    def __init__(self, ts_code: str, output_dir: str, client):
        from valuation_engine import ValuationEngine

        self.ts_code = ts_code
        self.output_dir = output_dir
        self.client = client
        self.valuation_engine = ValuationEngine(ts_code, output_dir, client)
        self._sf = client._safe_float
        self.market = self.valuation_engine.market
        self.params = self.valuation_engine.params

    def _annual_df(self, key: str):
        return self.client._get_annual_df(key)

    def _unit(self) -> str:
        return self.client._unit_label()

    def _price_unit(self) -> str:
        return self.client._price_unit()

    def _basic_info(self) -> dict:
        return self.valuation_engine._basic_info()

    def _sector_profile(self, basic: dict | None = None) -> dict:
        basic = basic or self._basic_info()
        industry = str(basic.get("industry") or "")
        if not industry:
            market_pack_path = os.path.join(self.output_dir, "data_pack_market.md")
            if os.path.exists(market_pack_path):
                try:
                    with open(market_pack_path, encoding="utf-8") as handle:
                        content = handle.read(4000)
                    match = re.search(r"\|\s*行业\s*\|\s*([^|\n]+)\|", content)
                    if match:
                        industry = match.group(1).strip()
                except OSError:
                    pass
        if not industry:
            cache_path = os.path.join(os.path.dirname(self.output_dir), ".collector_cache", f"stock_basic_{self.ts_code}.json")
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, encoding="utf-8") as handle:
                        payload = json.load(handle)
                    if payload and isinstance(payload, list):
                        industry = str(payload[0].get("industry") or "")
                except (OSError, json.JSONDecodeError, TypeError, AttributeError, IndexError):
                    pass
        regulated_financial = industry in REGULATED_FINANCIAL_INDUSTRIES
        return {
            "industry": industry,
            "regulated_financial": regulated_financial,
            "bank_like": industry == "银行",
            "valuation_family": "ResidualIncome" if regulated_financial else "CashFlow",
        }

    def _latest_roe_pct(self) -> float | None:
        fi_df = self._annual_df("fina_indicators")
        if not fi_df.empty:
            latest = fi_df.iloc[0]
            for col in ("roe_waa", "roe"):
                value = self._sf(latest.get(col))
                if value is not None:
                    return value

        latest_income = self._latest_row("income")
        latest_balance = self._latest_row("balance_sheet")
        if latest_income is None or latest_balance is None:
            return None
        profit = self._sf(latest_income.get("n_income_attr_p"))
        equity = self._sf(latest_balance.get("total_hldr_eqy_exc_min_int"))
        if profit is None or equity in (None, 0):
            return None
        return profit / equity * 100

    def _latest_row(self, key: str):
        df = self._annual_df(key)
        return None if df.empty else df.iloc[0]

    def _per_share(self, equity_value_mm: float | None, total_shares: float | None) -> float | None:
        if equity_value_mm is None or not total_shares or total_shares <= 0:
            return None
        return equity_value_mm * 1e6 / total_shares

    def _fmt_pct(self, value: float | None) -> str:
        return "—" if value is None else f"{value:.2f}%"

    def _fmt_multiple(self, value: float | None) -> str:
        return "—" if value is None else f"{value:.2f}x"

    def _fmt_price(self, value: float | None) -> str:
        return "—" if value is None else f"{value:.2f}"

    def _fmt_years(self, value: float | None) -> str:
        return "—" if value is None else f"{value:.2f} 年"

    def _fmt_ratio(self, value: float | None) -> str:
        return "—" if value is None else f"{value:.2f}"

    def _fmt_label(self, value: str | None) -> str:
        return value or "—"

    def _growth_pct(self, current: float | None, previous: float | None) -> float | None:
        if current is None or previous is None or previous == 0:
            return None
        return (current / previous - 1) * 100

    def _series_cagr_pct(self, values: list[float | None]) -> float | None:
        result = self.valuation_engine._cagr(values)
        return None if result is None else result * 100

    def _cash_total(self, row) -> float | None:
        if row is None:
            return None
        money_cap = self._sf(row.get("money_cap")) or 0.0
        trad_asset = self._sf(row.get("trad_asset")) or 0.0
        return money_cap + trad_asset

    def _interest_bearing_debt(self, row) -> float | None:
        if row is None:
            return None
        total = 0.0
        found = False
        for col in ("st_borr", "lt_borr", "bond_payable", "non_cur_liab_due_1y"):
            value = self._sf(row.get(col))
            if value is not None:
                total += value
                found = True
        return total if found else None

    def _da_from_cf_row(self, row) -> float | None:
        if row is None:
            return None
        parts = [
            self._sf(row.get("depr_fa_coga_dpba")),
            self._sf(row.get("amort_intang_assets")),
            self._sf(row.get("lt_amort_deferred_exp")),
        ]
        clean = [p for p in parts if p is not None]
        return sum(clean) if clean else None

    def _compute_owner_earnings_series(self) -> tuple[list[dict], dict]:
        income_df = self._annual_df("income")
        cf_df = self._annual_df("cashflow")
        if income_df.empty:
            return [], {"base_ratio": None, "conservative_ratio": None}

        cf_by_year = {str(r["end_date"])[:4]: r for _, r in cf_df.iterrows()} if not cf_df.empty else {}
        capex_da_ratios = []
        for _, row in cf_df.iterrows():
            da = self._da_from_cf_row(row)
            capex = self._sf(row.get("c_pay_acq_const_fiolta"))
            if da and da > 0 and capex is not None:
                capex_da_ratios.append(abs(capex) / da)

        maint_ratio = statistics.median(capex_da_ratios) if capex_da_ratios else 1.0
        maint_ratio = _clamp(maint_ratio, 0.6, 1.2)
        conservative_ratio = _clamp(max(1.0, maint_ratio), 1.0, 1.5)

        result = []
        for _, row in income_df.iterrows():
            year = str(row["end_date"])[:4]
            cf_row = cf_by_year.get(year)
            np_attr = self._sf(row.get("n_income_attr_p"))
            ocf = self._sf(cf_row.get("n_cashflow_act")) if cf_row is not None else None
            capex = self._sf(cf_row.get("c_pay_acq_const_fiolta")) if cf_row is not None else None
            da = self._da_from_cf_row(cf_row)

            maintenance_capex = None
            if capex is not None:
                capex_abs = abs(capex)
                if da is not None:
                    maintenance_capex = min(capex_abs, da * maint_ratio)
                else:
                    maintenance_capex = capex_abs

            owner_earnings = None
            if np_attr is not None and da is not None and maintenance_capex is not None:
                owner_earnings = np_attr + da - maintenance_capex

            conservative_owner_earnings = None
            if np_attr is not None and da is not None and capex is not None:
                conservative_owner_earnings = np_attr + da - abs(capex)

            fcf = None
            if ocf is not None and capex is not None:
                fcf = ocf - abs(capex)

            result.append({
                "year": year,
                "profit": np_attr,
                "ocf": ocf,
                "capex": abs(capex) if capex is not None else None,
                "da": da,
                "maintenance_capex": maintenance_capex,
                "fcf": fcf,
                "owner_earnings": owner_earnings,
                "owner_earnings_conservative": conservative_owner_earnings,
            })

        return result, {
            "base_ratio": maint_ratio,
            "conservative_ratio": conservative_ratio,
        }

    def _compute_defensive_metrics(self, oe_series: list[dict], anchor: dict, cycle_profile: dict | None = None) -> dict:
        latest_balance = self._latest_row("balance_sheet")
        prev_balance_df = self._annual_df("balance_sheet")
        prev_balance = prev_balance_df.iloc[1] if len(prev_balance_df) > 1 else None
        latest_income = self._latest_row("income")
        income_df = self._annual_df("income")
        prev_income = income_df.iloc[1] if len(income_df) > 1 else None
        basic = self._basic_info()
        shares = basic.get("total_shares")

        total_cur_assets = self._sf(latest_balance.get("total_cur_assets")) if latest_balance is not None else None
        total_cur_liab = self._sf(latest_balance.get("total_cur_liab")) if latest_balance is not None else None
        total_liab = self._sf(latest_balance.get("total_liab")) if latest_balance is not None else None
        total_assets = self._sf(latest_balance.get("total_assets")) if latest_balance is not None else None
        equity = self._sf(latest_balance.get("total_hldr_eqy_exc_min_int")) if latest_balance is not None else None
        goodwill = self._sf(latest_balance.get("goodwill")) if latest_balance is not None else None
        intangibles = self._sf(latest_balance.get("intang_assets")) if latest_balance is not None else None
        cash_raw = self._cash_total(latest_balance)
        interest_debt_raw = self._interest_bearing_debt(latest_balance)
        latest_ocf = oe_series[0].get("ocf") if oe_series else None
        st_debt = None
        if latest_balance is not None:
            st_borr = self._sf(latest_balance.get("st_borr")) or 0.0
            due_1y = self._sf(latest_balance.get("non_cur_liab_due_1y")) or 0.0
            st_debt = st_borr + due_1y

        quick_assets = None
        if latest_balance is not None:
            quick_assets = sum(
                (self._sf(latest_balance.get(col)) or 0.0)
                for col in ("money_cap", "trad_asset", "notes_receiv", "accounts_receiv", "oth_receiv")
            )

        current_ratio = None if total_cur_assets is None or not total_cur_liab or total_cur_liab <= 0 else total_cur_assets / total_cur_liab
        quick_ratio = None if not total_cur_liab or total_cur_liab <= 0 or quick_assets is None else quick_assets / total_cur_liab
        cash_to_short_debt = None if not st_debt or st_debt <= 0 or cash_raw is None else cash_raw / st_debt
        ocf_to_short_debt = None if not st_debt or st_debt <= 0 or latest_ocf is None else latest_ocf / st_debt
        net_debt_raw = None if interest_debt_raw is None or cash_raw is None else interest_debt_raw - cash_raw
        net_debt_to_anchor = None
        if net_debt_raw is not None and anchor.get("normalized_raw") not in (None, 0):
            net_debt_to_anchor = net_debt_raw / anchor["normalized_raw"]

        goodwill_ratio = None if total_assets in (None, 0) or goodwill is None else goodwill / total_assets * 100
        ncav_raw = None if total_cur_assets is None or total_liab is None else total_cur_assets - total_liab
        tangible_equity_raw = None
        if equity is not None:
            tangible_equity_raw = equity - (goodwill or 0.0) - (intangibles or 0.0)
        ncav_per_share = self._per_share(None if ncav_raw is None else ncav_raw / 1e6, shares)
        tangible_book_per_share = self._per_share(None if tangible_equity_raw is None else tangible_equity_raw / 1e6, shares)

        operate_profit = self._sf(latest_income.get("operate_profit")) if latest_income is not None else None
        fin_exp = None
        if latest_income is not None:
            fin_exp = self._sf(latest_income.get("fin_exp"))
            if fin_exp is None:
                fin_exp = self._sf(latest_income.get("finance_exp"))
        interest_coverage = None
        if operate_profit is not None and fin_exp is not None and fin_exp != 0:
            interest_coverage = operate_profit / abs(fin_exp)

        revenue_latest = self._sf(latest_income.get("revenue")) if latest_income is not None else None
        revenue_prev = self._sf(prev_income.get("revenue")) if prev_income is not None else None
        revenue_yoy = self._growth_pct(revenue_latest, revenue_prev)

        ar_latest = self._sf(latest_balance.get("accounts_receiv")) if latest_balance is not None else None
        ar_prev = self._sf(prev_balance.get("accounts_receiv")) if prev_balance is not None else None
        inventory_latest = self._sf(latest_balance.get("inventories")) if latest_balance is not None else None
        inventory_prev = self._sf(prev_balance.get("inventories")) if prev_balance is not None else None
        ar_yoy = self._growth_pct(ar_latest, ar_prev)
        inventory_yoy = self._growth_pct(inventory_latest, inventory_prev)
        working_capital_raw = None if total_cur_assets is None or total_cur_liab is None else total_cur_assets - total_cur_liab
        working_capital_to_revenue = None
        if working_capital_raw is not None and revenue_latest not in (None, 0):
            working_capital_to_revenue = working_capital_raw / revenue_latest * 100
        latest_profit_raw = self._sf(latest_income.get("n_income_attr_p")) if latest_income is not None else None
        inventory_relevance = False
        if inventory_latest not in (None, 0):
            if total_assets not in (None, 0) and inventory_latest / total_assets > 0.02:
                inventory_relevance = True
            elif revenue_latest not in (None, 0) and inventory_latest / revenue_latest > 0.03:
                inventory_relevance = True

        asset_heavy_stable = False
        if revenue_latest not in (None, 0) and total_assets not in (None, 0) and total_assets > 0:
            asset_turnover = revenue_latest / total_assets
            if asset_turnover < 0.3 and latest_ocf not in (None, 0) and latest_profit_raw not in (None, 0):
                if latest_profit_raw > 0 and latest_ocf / latest_profit_raw >= 1.2:
                    asset_heavy_stable = True

        severe_flags = []
        caution_flags = []
        if current_ratio is not None and current_ratio < 0.8 and (ocf_to_short_debt is None or ocf_to_short_debt < 0.5):
            severe_flags.append("流动比率低于1，短期偿债缓冲不足")
        elif current_ratio is not None and current_ratio < 1.0:
            caution_flags.append("流动比率偏紧，安全边际有限")
        if cash_to_short_debt is not None and cash_to_short_debt < 0.3 and (ocf_to_short_debt is None or ocf_to_short_debt < 0.5):
            severe_flags.append("现金/短债低于1，短债覆盖不足")
        elif cash_to_short_debt is not None and cash_to_short_debt < 1.0:
            caution_flags.append("现金/短债偏低，但可部分依赖经营现金流覆盖")
        if interest_coverage is not None and interest_coverage < 3.0:
            caution_flags.append("利息保障倍数偏低，债务韧性一般")
        if goodwill_ratio is not None and goodwill_ratio > 25.0:
            severe_flags.append("商誉占总资产偏高，资产质量需谨慎")
        elif goodwill_ratio is not None and goodwill_ratio > 15.0:
            caution_flags.append("商誉占比较高，需关注并购后遗症")
        if net_debt_to_anchor is not None and net_debt_to_anchor > 4.0:
            severe_flags.append("净债务/归一化锚值偏高，财务杠杆压缩股东回报")
        if revenue_yoy is not None and ar_yoy is not None and ar_yoy - revenue_yoy > 15.0:
            severe_flags.append("应收增速明显快于收入，需防收入质量下滑")
        elif revenue_yoy is not None and ar_yoy is not None and ar_yoy - revenue_yoy > 5.0:
            caution_flags.append("应收增速快于收入，需核实回款质量")
        if inventory_relevance and revenue_yoy is not None and inventory_yoy is not None and inventory_yoy - revenue_yoy > 15.0:
            is_gip = (cycle_profile or {}).get("is_growth_investment_phase", False)
            if is_gip:
                caution_flags.append("存货增速明显快于收入，但处于增长投资期，产能与渠道扩张中的备货属正常现象")
            else:
                severe_flags.append("存货增速明显快于收入，需防需求转弱或存货积压")
        elif inventory_relevance and revenue_yoy is not None and inventory_yoy is not None and inventory_yoy - revenue_yoy > 5.0:
            caution_flags.append("存货增速快于收入，需核实渠道与库存健康度")

        if asset_heavy_stable and severe_flags:
            downgraded = severe_flags.pop(0)
            caution_flags.append(f"重资产公用事业特征明显，对短债更依赖持续经营现金流而非账面现金：{downgraded}")

        asset_backstop_price = None
        backstop_candidates = [v for v in [ncav_per_share, tangible_book_per_share] if v is not None and v > 0]
        if backstop_candidates:
            asset_backstop_price = max(backstop_candidates)

        if len(severe_flags) >= 2:
            defensive_rating = "Weak"
        elif severe_flags or len(caution_flags) >= 2:
            defensive_rating = "Adequate"
        else:
            defensive_rating = "Strong"

        return {
            "current_ratio": current_ratio,
            "quick_ratio": quick_ratio,
            "cash_to_short_debt": cash_to_short_debt,
            "ocf_to_short_debt": ocf_to_short_debt,
            "interest_coverage": interest_coverage,
            "goodwill_ratio": goodwill_ratio,
            "net_debt_to_anchor": net_debt_to_anchor,
            "ncav_per_share": ncav_per_share,
            "tangible_book_per_share": tangible_book_per_share,
            "working_capital_to_revenue": working_capital_to_revenue,
            "revenue_yoy": revenue_yoy,
            "ar_yoy": ar_yoy,
            "inventory_yoy": inventory_yoy,
            "inventory_relevance": inventory_relevance,
            "asset_backstop_price": asset_backstop_price,
            "defensive_rating": defensive_rating,
            "severe_flags": severe_flags,
            "caution_flags": caution_flags,
        }

    def _compute_capital_allocation_metrics(self, basic: dict, anchor: dict, dividend_metrics: dict) -> dict:
        latest_income = self._latest_row("income")
        latest_profit_raw = self._sf(latest_income.get("n_income_attr_p")) if latest_income is not None else None
        latest_fy_dividend_raw = dividend_metrics.get("latest_fy_dividend_raw")
        payout_to_profit = None
        if latest_fy_dividend_raw is not None and latest_profit_raw not in (None, 0):
            payout_to_profit = latest_fy_dividend_raw / latest_profit_raw * 100

        payout_to_anchor = None
        if latest_fy_dividend_raw is not None and anchor.get("normalized_raw") not in (None, 0):
            payout_to_anchor = latest_fy_dividend_raw / anchor["normalized_raw"] * 100

        rep_df = self.client._store.get("repurchase")
        repurchase_total_raw = None
        repurchase_annual_avg_raw = None
        if rep_df is not None and not rep_df.empty:
            repurchase_total_raw = 0.0
            for _, row in rep_df.iterrows():
                amount = self._sf(row.get("amount"))
                if amount is not None:
                    repurchase_total_raw += amount
            years_span = min(3, max(1, len(set(str(r.get("ann_date", ""))[:4] for _, r in rep_df.iterrows()))))
            repurchase_annual_avg_raw = repurchase_total_raw / years_span if repurchase_total_raw is not None else None

        market_cap_raw = basic.get("mkt_cap_mm") * 1e6 if basic.get("mkt_cap_mm") is not None else None
        shareholder_yield = None
        if market_cap_raw and market_cap_raw > 0:
            capital_return_raw = (latest_fy_dividend_raw or 0.0) + (repurchase_annual_avg_raw or 0.0)
            shareholder_yield = capital_return_raw / market_cap_raw * 100

        pledge_df = self.client._store.get("pledge_stat")
        pledge_ratio = None
        if pledge_df is not None and not pledge_df.empty:
            pledge_ratio = self._sf(pledge_df.iloc[0].get("pledge_ratio"))

        notes = []
        alignment = "Neutral"
        if payout_to_anchor is not None and payout_to_anchor > 90:
            notes.append("分红已接近或超过归一化锚值，需确认是否透支未来")
            alignment = "Watch"
        if repurchase_annual_avg_raw not in (None, 0):
            notes.append("存在股票回购记录，但用途未区分注销/激励，需结合年报确认是否真正增厚每股价值")
        if pledge_ratio is not None and pledge_ratio >= 30:
            notes.append("股权质押比例偏高，需警惕资金链与治理激励错配")
            alignment = "Watch"
        elif shareholder_yield is not None and shareholder_yield >= 2.0:
            alignment = "Shareholder Friendly"

        return {
            "payout_to_profit": payout_to_profit,
            "payout_to_anchor": payout_to_anchor,
            "repurchase_total_raw": repurchase_total_raw,
            "repurchase_annual_avg_raw": repurchase_annual_avg_raw,
            "shareholder_yield": shareholder_yield,
            "pledge_ratio": pledge_ratio,
            "alignment": alignment,
            "notes": notes,
        }

    def _compute_cycle_profile(self, oe_series: list[dict], sector_profile: dict) -> dict:
        income_df = self._annual_df("income")
        balance_df = self._annual_df("balance_sheet")
        latest_income = None if income_df.empty else income_df.iloc[0]
        latest_balance = None if balance_df.empty else balance_df.iloc[0]

        revenue_values = [self._sf(r.get("revenue")) for _, r in income_df.head(10).iterrows()]
        profit_values = [row.get("profit") for row in oe_series[:10]]
        anchor_like_values = [
            row.get("owner_earnings") if row.get("owner_earnings") is not None else row.get("profit")
            for row in oe_series[:10]
        ]

        positive_profit_values = [v for v in profit_values if v is not None and v > 0]
        positive_anchor_values = [v for v in anchor_like_values if v is not None and v > 0]

        profit_variability = None
        if len(positive_profit_values) >= 3 and statistics.mean(positive_profit_values) != 0:
            profit_variability = statistics.pstdev(positive_profit_values) / abs(statistics.mean(positive_profit_values))

        anchor_variability = None
        if len(positive_anchor_values) >= 3 and statistics.mean(positive_anchor_values) != 0:
            anchor_variability = statistics.pstdev(positive_anchor_values) / abs(statistics.mean(positive_anchor_values))

        sign_changes = 0
        last_sign = None
        for value in profit_values:
            if value is None or value == 0:
                continue
            sign = 1 if value > 0 else -1
            if last_sign is not None and sign != last_sign:
                sign_changes += 1
            last_sign = sign

        revenue_volatility = None
        rev_yoys = []
        for idx in range(len(revenue_values) - 1):
            current = revenue_values[idx]
            previous = revenue_values[idx + 1]
            yoy = self._growth_pct(current, previous)
            if yoy is not None:
                rev_yoys.append(yoy)
        if len(rev_yoys) >= 3:
            revenue_volatility = statistics.pstdev(rev_yoys)

        capex_ratios = []
        for row in oe_series[:10]:
            capex = row.get("capex")
            revenue = None
            year = row.get("year")
            if year is not None:
                matched = income_df[income_df["end_date"].astype(str).str[:4] == str(year)] if not income_df.empty else None
                if matched is not None and not matched.empty:
                    revenue = self._sf(matched.iloc[0].get("revenue"))
            if capex is not None and revenue not in (None, 0):
                capex_ratios.append(capex / revenue * 100)
        capex_to_revenue = statistics.median(capex_ratios) if capex_ratios else None

        asset_turnover = None
        if latest_income is not None and latest_balance is not None:
            latest_revenue = self._sf(latest_income.get("revenue"))
            latest_assets = self._sf(latest_balance.get("total_assets"))
            if latest_revenue not in (None, 0) and latest_assets not in (None, 0):
                asset_turnover = latest_revenue / latest_assets

        true_cyclical_signals = []
        asset_heavy_signals = []

        if sign_changes >= 1:
            true_cyclical_signals.append("利润在样本期内出现正负切换")
        if profit_variability is not None and profit_variability > 0.6:
            true_cyclical_signals.append("利润波动较大，可能受景气周期驱动")
        if anchor_variability is not None and anchor_variability > 0.6:
            true_cyclical_signals.append("现金流/Owner Earnings 波动较大")
        if revenue_volatility is not None and revenue_volatility > 20:
            true_cyclical_signals.append("收入同比波动较大")
        if not sector_profile.get("regulated_financial") and capex_to_revenue is not None and capex_to_revenue > 8:
            asset_heavy_signals.append("资本开支强度较高，需防止景气期扩张扭曲盈利")
        if not sector_profile.get("regulated_financial") and asset_turnover is not None and asset_turnover < 0.7:
            asset_heavy_signals.append("资产较重，需结合资产负债表看跨周期盈利")

        if sector_profile.get("regulated_financial"):
            return {
                "cyclical": False,
                "is_true_cyclical": False,
                "is_asset_heavy": False,
                "normalization_window": 5,
                "growth_window": 5,
                "growth_cap": 4.0 if sector_profile.get("bank_like") else 5.0,
                "profit_variability": None if profit_variability is None else profit_variability * 100,
                "anchor_variability": None if anchor_variability is None else anchor_variability * 100,
                "revenue_volatility": revenue_volatility,
                "sign_changes": sign_changes,
                "capex_to_revenue": capex_to_revenue,
                "asset_turnover": asset_turnover,
                "signals": [
                    "监管资本、信用成本和 ROE 比普通工业企业现金流更关键",
                    "优先采用残余收益 / P-TBV 框架，避免把银行机械当成普通 DCF 标的",
                ],
                "cyclical_signals": [],
                "asset_heavy_signals": [],
            }

        is_true_cyclical = len(true_cyclical_signals) >= 1
        is_asset_heavy = len(asset_heavy_signals) >= 2
        cyclical = is_true_cyclical or is_asset_heavy

        if is_true_cyclical:
            normalization_window = 8
            growth_window = 8
            growth_cap = 6.0
        elif is_asset_heavy:
            normalization_window = 8
            growth_window = 8
            growth_cap = 10.0
        else:
            normalization_window = 5
            growth_window = 5
            growth_cap = 12.0

        is_growth_investment_phase = False
        gip_signals = []
        if not sector_profile.get("regulated_financial"):
            rev_values_for_cagr = [
                self._sf(r.get("revenue")) for _, r in income_df.head(5).iterrows()
            ]
            rev_values_valid = [v for v in rev_values_for_cagr if v is not None and v > 0]
            rev_cagr_gip = None
            if len(rev_values_valid) >= 4:
                rev_cagr_gip = self._series_cagr_pct(rev_values_valid)

            capex_da_ratios_gip = []
            for row in oe_series[:5]:
                da = row.get("da")
                capex = row.get("capex")
                if da and da > 0 and capex is not None:
                    capex_da_ratios_gip.append(abs(capex) / da)
            capex_da_median_gip = statistics.median(capex_da_ratios_gip) if capex_da_ratios_gip else None

            if (capex_to_revenue is not None and capex_to_revenue > 8
                    and rev_cagr_gip is not None and rev_cagr_gip > 15
                    and capex_da_median_gip is not None and capex_da_median_gip > 1.3):
                is_growth_investment_phase = True
                gip_signals.append(
                    "公司处于大规模资本投资阶段（Capex/折旧>1.3x，营收CAGR>15%，Capex/营收>8%）"
                    "——大量资本开支属于建设未来产能的增长性投资，不应全部视为维护性支出；"
                    "利润、现金流和ROE的短期波动主要来自投资节奏，而非经营恶化"
                )
                growth_cap = 12.0

        all_signals = true_cyclical_signals + asset_heavy_signals + gip_signals

        return {
            "cyclical": cyclical,
            "is_true_cyclical": is_true_cyclical,
            "is_asset_heavy": is_asset_heavy,
            "is_growth_investment_phase": is_growth_investment_phase,
            "normalization_window": normalization_window,
            "growth_window": growth_window,
            "growth_cap": growth_cap,
            "profit_variability": None if profit_variability is None else profit_variability * 100,
            "anchor_variability": None if anchor_variability is None else anchor_variability * 100,
            "revenue_volatility": revenue_volatility,
            "sign_changes": sign_changes,
            "capex_to_revenue": capex_to_revenue,
            "asset_turnover": asset_turnover,
            "signals": all_signals,
            "cyclical_signals": true_cyclical_signals,
            "asset_heavy_signals": asset_heavy_signals,
            "gip_signals": gip_signals,
        }

    def _pick_anchor_metric(self, oe_series: list[dict], cycle_profile: dict, sector_profile: dict) -> dict:
        if not oe_series:
            return {
                "metric": "Profit",
                "reason": "财务数据不足，退化为利润锚",
                "values": [],
                "normalized_raw": None,
                "normalized": None,
                "yield_on_market_cap": None,
            }

        if sector_profile.get("regulated_financial"):
            profit_values = [row.get("profit") for row in oe_series]
            window = 5
            clean = [v for v in profit_values[:window] if v is not None and v > 0]
            normalized_raw = statistics.median(clean) if clean else None
            latest_market_cap_mm = self._basic_info().get("mkt_cap_mm")
            market_cap_raw = latest_market_cap_mm * 1e6 if latest_market_cap_mm is not None else None
            yield_on_market_cap = None
            if normalized_raw is not None and market_cap_raw and market_cap_raw > 0:
                yield_on_market_cap = normalized_raw / market_cap_raw * 100
            return {
                "metric": "Profit",
                "reason": "金融/银行类公司优先用利润、ROE、资本充足率和每股净资产估值，避免把监管资本行业机械当成普通现金流生意",
                "values": profit_values,
                "normalized_raw": normalized_raw,
                "normalized": None if normalized_raw is None else normalized_raw / 1e6,
                "yield_on_market_cap": yield_on_market_cap,
                "normalization_window": window,
            }

        candidates = {
            "FCF": [row.get("fcf") for row in oe_series],
            "Owner Earnings": [row.get("owner_earnings") for row in oe_series],
            "Profit": [row.get("profit") for row in oe_series],
        }

        latest_market_cap_mm = self._basic_info().get("mkt_cap_mm")
        market_cap_raw = latest_market_cap_mm * 1e6 if latest_market_cap_mm is not None else None

        scored = []
        is_gip = cycle_profile.get("is_growth_investment_phase", False)
        for name, values in candidates.items():
            raw_window = max(3, cycle_profile.get("normalization_window") or 5)
            if is_gip:
                window = min(raw_window, 5)
            else:
                window = raw_window
            window_values = values[:window]
            clean = [v for v in window_values if v is not None]
            positive_years = sum(1 for v in clean if v > 0)
            if not clean:
                continue

            positive_window = [v for v in clean if v > 0]
            fallback_recent = [v for v in clean[:3] if v > 0]
            if is_gip:
                normalized_raw = statistics.median(fallback_recent) if fallback_recent else (statistics.median(positive_window) if positive_window else None)
            elif cycle_profile.get("is_true_cyclical"):
                normalized_raw = statistics.median(positive_window) if positive_window else None
            elif cycle_profile.get("is_asset_heavy"):
                normalized_raw = statistics.median(fallback_recent) if fallback_recent else (statistics.median(positive_window) if positive_window else None)
            else:
                normalized_raw = statistics.median(fallback_recent) if fallback_recent else (statistics.median(positive_window) if positive_window else None)
            variability = None
            if len(positive_window) >= 2 and statistics.mean(positive_window) != 0:
                variability = statistics.pstdev(positive_window) / abs(statistics.mean(positive_window))

            score = positive_years * 10
            if normalized_raw is not None:
                score += 5
            if variability is not None:
                score += max(0, 5 - variability * 10)

            is_asset_heavy_only = cycle_profile.get("is_asset_heavy") and not cycle_profile.get("is_true_cyclical")
            if name == "FCF":
                if is_asset_heavy_only:
                    score -= 3
                else:
                    score += 2
            elif name == "Owner Earnings":
                if is_asset_heavy_only:
                    score += 2
                else:
                    score += 1

            yield_on_market_cap = None
            if normalized_raw is not None and market_cap_raw and market_cap_raw > 0:
                yield_on_market_cap = normalized_raw / market_cap_raw * 100

            scored.append({
                "metric": name,
                "values": values,
                "normalized_raw": normalized_raw,
                "normalized": None if normalized_raw is None else normalized_raw / 1e6,
                "positive_years": positive_years,
                "variability": variability,
                "yield_on_market_cap": yield_on_market_cap,
                "normalization_window": window,
                "score": score,
            })

        if not scored:
            return {
                "metric": "Profit",
                "reason": "财务数据不足，退化为利润锚",
                "values": [],
                "normalized_raw": None,
                "normalized": None,
                "yield_on_market_cap": None,
            }

        best = max(scored, key=lambda item: item["score"])
        reason_map = {
            "FCF": "自由现金流可用年份较多，且对股东最直接",
            "Owner Earnings": "自由现金流波动较大，所有者收益更接近长期真实赚钱能力",
            "Profit": "现金流代表性不足，退回利润锚并在后续报告中保守处理",
        }
        best["reason"] = reason_map[best["metric"]]
        if cycle_profile.get("is_true_cyclical"):
            best["reason"] += "；同时检测到周期性信号，归一化锚值已拉长到跨周期窗口"
        elif cycle_profile.get("is_asset_heavy"):
            best["reason"] += "；检测到重资产/高资本开支特征，归一化窗口已拉长；优先使用所有者收益避免扩张期FCF失真"
        return best

    def _compute_quality_guardrails(self, oe_series: list[dict], anchor: dict, defensive: dict, capital_allocation: dict, wacc_data: dict, cycle_profile: dict, sector_profile: dict) -> dict:
        latest_balance = self._latest_row("balance_sheet")
        latest_total_assets = self._sf(latest_balance.get("total_assets")) if latest_balance is not None else None
        latest_total_liab = self._sf(latest_balance.get("total_liab")) if latest_balance is not None else None
        cash_raw = self._cash_total(latest_balance)

        debt_asset_ratio = None
        if latest_total_assets and latest_total_liab is not None and latest_total_assets > 0:
            debt_asset_ratio = latest_total_liab / latest_total_assets * 100

        cash_conversion_samples = []
        for row in oe_series[:5]:
            profit = row.get("profit")
            ocf = row.get("ocf")
            if profit and profit > 0 and ocf is not None:
                cash_conversion_samples.append(ocf / profit)
        cash_conversion = statistics.median(cash_conversion_samples) if cash_conversion_samples else None

        positive_oe_years = sum(1 for row in oe_series[:5] if (row.get("owner_earnings") or 0) > 0)
        positive_fcf_years = sum(1 for row in oe_series[:5] if (row.get("fcf") or 0) > 0)
        net_cash = None
        if cash_raw is not None and latest_total_liab is not None:
            net_cash = cash_raw - latest_total_liab

        required_return = 10.0
        notes = []

        if sector_profile.get("regulated_financial"):
            required_return = 11.0 if self.market == "A" else 10.5
            notes.append("金融/银行类公司采用更保守的股权回报要求，并弱化普通工业企业式现金流折现")
            if defensive.get("defensive_rating") == "Weak":
                required_return += 0.5
                notes.append("防守层偏弱，提高要求回报率")
            elif defensive.get("defensive_rating") == "Adequate":
                required_return += 0.25
                notes.append("防守层一般，略提高要求回报率")
            if capital_allocation.get("alignment") == "Shareholder Friendly":
                required_return -= 0.25
                notes.append("存在稳定股东回报记录，可小幅下调要求回报率")
            elif capital_allocation.get("alignment") == "Watch":
                required_return += 0.25
                notes.append("资本配置或治理信号一般，略提高要求回报率")

            required_return = _clamp(required_return, 9.5, 12.5)
            severe_count = len(defensive.get("severe_flags", []))
            caution_count = len(defensive.get("caution_flags", [])) + len(capital_allocation.get("notes", []))
            if severe_count >= 2:
                valuation_mode = "Defensive"
                optimistic_allowed = False
                growth_realization_factor = 0.5
            elif severe_count == 1 or caution_count >= 2 or defensive.get("defensive_rating") == "Adequate":
                valuation_mode = "Conservative"
                optimistic_allowed = False
                growth_realization_factor = 0.6
            else:
                valuation_mode = "Normal"
                optimistic_allowed = True
                growth_realization_factor = 0.7

            return {
                "required_return": required_return,
                "wacc_reference": wacc_data.get("wacc"),
                "debt_asset_ratio": debt_asset_ratio,
                "cash_conversion": None if cash_conversion is None else cash_conversion * 100,
                "positive_oe_years": positive_oe_years,
                "positive_fcf_years": positive_fcf_years,
                "net_cash_raw": net_cash,
                "notes": notes,
                "valuation_mode": valuation_mode,
                "optimistic_allowed": optimistic_allowed,
                "growth_realization_factor": growth_realization_factor,
            }

        if positive_oe_years >= 5:
            required_return -= 0.5
            notes.append("Owner Earnings 连续5年为正，要求回报率可略降")
        elif positive_oe_years <= 3:
            required_return += 1.0
            notes.append("Owner Earnings 稳定性不足，提高要求回报率")

        if positive_fcf_years <= 2:
            required_return += 0.5
            notes.append("自由现金流波动较大，提高要求回报率")

        if cash_conversion is not None and cash_conversion >= 1.1:
            required_return -= 0.5
            notes.append("现金转化优秀，要求回报率可略降")
        elif cash_conversion is not None and cash_conversion < 0.9:
            required_return += 0.5
            notes.append("现金转化偏弱，提高要求回报率")

        is_gip = cycle_profile.get("is_growth_investment_phase", False)

        if debt_asset_ratio is not None and debt_asset_ratio < 35:
            required_return -= 0.5
            notes.append("负债压力可控，要求回报率可略降")
        elif debt_asset_ratio is not None and debt_asset_ratio > 60:
            debt_penalty = 0.5 if is_gip else 1.0
            required_return += debt_penalty
            notes.append("杠杆偏高，提高要求回报率" + ("（处于增长投资期，债务主要用于产能建设，惩罚减半）" if is_gip else ""))

        if net_cash is not None and net_cash > 0:
            required_return -= 0.5
            notes.append("净现金或接近净现金，要求回报率可略降")

        if anchor.get("metric") == "Profit":
            required_return += 0.5
            notes.append("当前只能使用利润锚，保守上调要求回报率")

        if cycle_profile.get("is_true_cyclical") and cycle_profile.get("sign_changes", 0) >= 1 and not is_gip:
            required_return += 0.5
            notes.append("利润跨周期波动明显，要求回报率进一步上调")

        if defensive.get("defensive_rating") == "Weak":
            required_return += 1.0
            notes.append("资产负债表与营运资本风险偏高，切换到更保守的估值姿态")
        elif defensive.get("defensive_rating") == "Adequate":
            defense_penalty = 0.25 if is_gip else 0.5
            required_return += defense_penalty
            notes.append("防守层不算宽裕，要求回报率需留更多缓冲" + ("（处于增长投资期，扩张阶段自然的财务紧张，惩罚减半）" if is_gip else ""))

        if capital_allocation.get("alignment") == "Watch":
            required_return += 0.5
            notes.append("资本配置或治理信号一般，提高要求回报率")
        elif capital_allocation.get("alignment") == "Shareholder Friendly":
            required_return -= 0.25
            notes.append("存在一定股东回报记录，可小幅下调要求回报率")

        if capital_allocation.get("pledge_ratio") is not None and capital_allocation["pledge_ratio"] >= 50:
            required_return += 0.5
            notes.append("高比例股权质押提升治理与流动性风险")

        required_return = _clamp(required_return, 8.0, 12.0)
        severe_count = len(defensive.get("severe_flags", []))
        caution_count = len(defensive.get("caution_flags", [])) + len(capital_allocation.get("notes", []))
        is_gip = cycle_profile.get("is_growth_investment_phase", False)
        if severe_count >= 2 or anchor.get("metric") == "Profit":
            valuation_mode = "Defensive"
            optimistic_allowed = False
            growth_realization_factor = 0.6
        elif severe_count == 1 or caution_count >= 2:
            valuation_mode = "Conservative"
            optimistic_allowed = False
            growth_realization_factor = 0.75
        elif defensive.get("defensive_rating") == "Adequate" and not is_gip:
            valuation_mode = "Conservative"
            optimistic_allowed = False
            growth_realization_factor = 0.75
        else:
            valuation_mode = "Normal"
            optimistic_allowed = True
            growth_realization_factor = 0.9
        return {
            "required_return": required_return,
            "wacc_reference": wacc_data.get("wacc"),
            "debt_asset_ratio": debt_asset_ratio,
            "cash_conversion": None if cash_conversion is None else cash_conversion * 100,
            "positive_oe_years": positive_oe_years,
            "positive_fcf_years": positive_fcf_years,
            "net_cash_raw": net_cash,
            "notes": notes,
            "valuation_mode": valuation_mode,
            "optimistic_allowed": optimistic_allowed,
            "growth_realization_factor": growth_realization_factor,
        }

    def _compute_bank_health_metrics(self) -> dict:
        """Compute bank-specific asset quality and regulatory metrics.
        
        Uses fina_indicator's bank-specific fields when available (Tushare VIP),
        falls back to reasonable defaults with a warning when unavailable.
        Key metrics from investor methodology:
        - 不良贷款率 (NPL Ratio): core risk indicator, <1% is excellent for Chinese banks
        - 拨备覆盖率 (Provision Coverage): risk buffer, >300% means "hidden profit"
        - 资本充足率 (Capital Adequacy): regulatory leverage constraint
        - 净息差 (Net Interest Margin): core profitability driver
        - 成本收入比 (Cost/Income): operating efficiency
        """
        fi_df = self._annual_df("fina_indicators")
        bank_cols = ["npl_ratio", "prov_cov_ratio", "cap_adequacy_ratio",
                     "core_cap_adequacy_ratio", "net_int_margin", "cost_income_ratio"]
        available = {}
        data_available = False
        
        if fi_df.empty:
            return {"available": False, "metrics": {}, "note": "银行指标数据不可用（无fina_indicator数据）"}
        
        for col in bank_cols:
            values = []
            for _, row in fi_df.iterrows():
                val = self._sf(row.get(col))
                if val is not None:
                    values.append(val)
            if values:
                available[col] = values
                data_available = True
        
        if not data_available:
            return {"available": False, "metrics": {}, "note": "银行特定指标不可用（Tushare免费版不含npl_ratio/prov_cov_ratio等字段，需VIP权限）"}
        
        latest = fi_df.iloc[0]
        years = [str(r["end_date"])[:4] for _, r in fi_df.iterrows()]
        
        def _trend(values: list) -> str:
            if len(values) < 2:
                return "—"
            if values[0] < values[-1]:
                return "上升"
            elif values[0] > values[-1]:
                return "下降"
            return "持平"
        
        def _assessment(npl: float | None, prov_cov: float | None, nim: float | None) -> str:
            parts = []
            if npl is not None:
                if npl < 1.0:
                    parts.append("NPL优秀(<1%)")
                elif npl < 1.5:
                    parts.append("NPL良好")
                else:
                    parts.append("NPL需关注")
            if prov_cov is not None:
                if prov_cov > 300:
                    parts.append("拨备充裕(>300%)")
                elif prov_cov > 200:
                    parts.append("拨备充足")
                elif prov_cov > 150:
                    parts.append("拨备基本达标")
                else:
                    parts.append("拨备偏紧")
            if nim is not None:
                if nim > 2.5:
                    parts.append("息差较好")
                elif nim > 2.0:
                    parts.append("息差正常")
                elif nim > 1.5:
                    parts.append("息差偏弱")
                else:
                    parts.append("息差承压")
            return "，".join(parts) if parts else "—"
        
        metrics = {}
        if "npl_ratio" in available:
            metrics["npl_ratio_latest"] = available["npl_ratio"][0]
            metrics["npl_ratio_trend"] = _trend(available["npl_ratio"])
            metrics["npl_ratio_series"] = [f"{v:.2f}" for v in available["npl_ratio"]]
        else:
            metrics["npl_ratio_latest"] = None
            metrics["npl_ratio_trend"] = "—"
            metrics["npl_ratio_series"] = []
            
        if "prov_cov_ratio" in available:
            metrics["prov_cov_latest"] = available["prov_cov_ratio"][0]
            metrics["prov_cov_trend"] = _trend(available["prov_cov_ratio"])
            metrics["prov_cov_series"] = [f"{v:.2f}" for v in available["prov_cov_ratio"]]
        else:
            metrics["prov_cov_latest"] = None
            metrics["prov_cov_trend"] = "—"
            metrics["prov_cov_series"] = []
            
        if "cap_adequacy_ratio" in available:
            metrics["car_latest"] = available["cap_adequacy_ratio"][0]
            metrics["car_series"] = [f"{v:.2f}" for v in available["cap_adequacy_ratio"]]
        else:
            metrics["car_latest"] = None
            
        if "core_cap_adequacy_ratio" in available:
            metrics["core_car_latest"] = available["core_cap_adequacy_ratio"][0]
        else:
            metrics["core_car_latest"] = None
            
        if "net_int_margin" in available:
            metrics["nim_latest"] = available["net_int_margin"][0]
            metrics["nim_trend"] = _trend(available["net_int_margin"])
            metrics["nim_series"] = [f"{v:.2f}" for v in available["net_int_margin"]]
        else:
            metrics["nim_latest"] = None
            metrics["nim_trend"] = "—"
            metrics["nim_series"] = []
            
        if "cost_income_ratio" in available:
            metrics["cir_latest"] = available["cost_income_ratio"][0]
            metrics["cir_series"] = [f"{v:.2f}" for v in available["cost_income_ratio"]]
        else:
            metrics["cir_latest"] = None
        
        metrics["quality_assessment"] = _assessment(
            metrics.get("npl_ratio_latest"),
            metrics.get("prov_cov_latest"),
            metrics.get("nim_latest")
        )
        metrics["years"] = years
        
        return {"available": True, "metrics": metrics, "note": ""}

    def _compute_dividend_metrics(self, basic: dict) -> dict:
        div_df = self.client._store.get("dividends")
        shares = basic.get("total_shares")
        close = basic.get("close")
        if div_df is None or div_df.empty or not shares or shares <= 0:
            return {
                "latest_fy_dividend_raw": None,
                "latest_fy_dps": None,
                "forward_dividend_yield": None,
                "trailing_12m_dividend_raw": None,
                "trailing_12m_dps": None,
                "trailing_12m_dividend_yield": None,
            }

        latest_record_dt = None
        latest_record_str = None
        for _, row in div_df.iterrows():
            date_str = str(row.get("record_date") or "")
            if len(date_str) == 8 and date_str.isdigit():
                try:
                    dt = datetime.strptime(date_str, "%Y%m%d")
                except ValueError:
                    continue
                if latest_record_dt is None or dt > latest_record_dt:
                    latest_record_dt = dt
                    latest_record_str = date_str

        latest_fy = str(self._latest_row("income")["end_date"])[:4] if self._latest_row("income") is not None else None
        latest_fy_dividend_raw = 0.0
        trailing_12m_dividend_raw = 0.0
        trailing_cutoff = latest_record_dt - timedelta(days=365) if latest_record_dt is not None else None

        for _, row in div_df.iterrows():
            end_year = str(row.get("end_date") or "")[:4]
            cash_div = self._sf(row.get("cash_div_tax")) or 0.0
            base_share = self._sf(row.get("base_share")) or 0.0
            payment_raw = cash_div * base_share * 10000

            if latest_fy and end_year == latest_fy:
                latest_fy_dividend_raw += payment_raw

            date_str = str(row.get("record_date") or "")
            if trailing_cutoff is not None and len(date_str) == 8 and date_str.isdigit():
                try:
                    dt = datetime.strptime(date_str, "%Y%m%d")
                except ValueError:
                    continue
                if dt >= trailing_cutoff:
                    trailing_12m_dividend_raw += payment_raw

        latest_fy_dps = latest_fy_dividend_raw / shares if latest_fy_dividend_raw > 0 else None
        trailing_12m_dps = trailing_12m_dividend_raw / shares if trailing_12m_dividend_raw > 0 else None
        forward_yield = (latest_fy_dps / close * 100) if latest_fy_dps is not None and close and close > 0 else None
        trailing_yield = (trailing_12m_dps / close * 100) if trailing_12m_dps is not None and close and close > 0 else None

        return {
            "latest_fy_dividend_raw": latest_fy_dividend_raw if latest_fy_dividend_raw > 0 else None,
            "latest_fy_dps": latest_fy_dps,
            "forward_dividend_yield": forward_yield,
            "trailing_12m_dividend_raw": trailing_12m_dividend_raw if trailing_12m_dividend_raw > 0 else None,
            "trailing_12m_dps": trailing_12m_dps,
            "trailing_12m_dividend_yield": trailing_yield,
            "latest_record_date": latest_record_str,
        }

    def _compute_growth_profile(self, anchor: dict, oe_series: list[dict], defensive: dict, cycle_profile: dict, sector_profile: dict) -> dict:
        income_df = self._annual_df("income")
        window = max(5, cycle_profile.get("growth_window") or 5)
        revenue_series = [self._sf(r.get("revenue")) for _, r in income_df.head(window).iterrows()]
        profit_series = [row.get("profit") for row in oe_series[:window]]
        oe_values = [row.get("owner_earnings") for row in oe_series[:window]]
        fcf_values = [row.get("fcf") for row in oe_series[:window]]

        rev_cagr = self._series_cagr_pct(revenue_series)
        profit_cagr = self._series_cagr_pct(profit_series)
        oe_cagr = self._series_cagr_pct(oe_values)
        fcf_cagr = self._series_cagr_pct(fcf_values)
        anchor_cagr = self._series_cagr_pct(anchor.get("values", []))

        if sector_profile.get("regulated_financial"):
            candidate_growths = [g for g in [rev_cagr, profit_cagr] if g is not None and -5 <= g <= 12]
        else:
            candidate_growths = [
                g for g in [rev_cagr, profit_cagr, oe_cagr, fcf_cagr, anchor_cagr]
                if g is not None and -5 <= g <= 20
            ]

        if candidate_growths:
            base_growth = statistics.median(candidate_growths)
        else:
            base_growth = 3.0

        lower_bound = 0.5 if sector_profile.get("regulated_financial") else 1.0
        base_growth = _clamp(base_growth, lower_bound, cycle_profile.get("growth_cap") or 12.0)
        if sector_profile.get("regulated_financial"):
            if sector_profile.get("bank_like"):
                base_growth = min(base_growth, 3.5)
            else:
                base_growth = min(base_growth, 4.5)
        if defensive.get("defensive_rating") == "Weak":
            base_growth = min(base_growth, 6.0)
        elif defensive.get("defensive_rating") == "Adequate":
            base_growth = min(base_growth, 8.0)

        if defensive.get("severe_flags"):
            base_growth = min(base_growth, 7.0)

        is_gip = cycle_profile.get("is_growth_investment_phase", False)
        if cycle_profile.get("is_true_cyclical") and not is_gip:
            base_growth = min(base_growth, 5.0)
        elif cycle_profile.get("is_true_cyclical") and is_gip:
            base_growth = min(base_growth, 8.0)
        if cycle_profile.get("is_true_cyclical") and anchor.get("metric") == "Profit":
            base_growth = min(base_growth, 4.0)
        if cycle_profile.get("is_true_cyclical") and cycle_profile.get("sign_changes", 0) >= 1 and not is_gip:
            base_growth = min(base_growth, 3.0)

        if sector_profile.get("regulated_financial"):
            conservative = max(0.0, base_growth - 1.0)
            optimistic = min(5.0 if sector_profile.get("bank_like") else 6.0, base_growth + 1.0)
        else:
            conservative = max(0.0, base_growth - 3.0)
            optimistic = min(15.0, base_growth + 3.0)
        if defensive.get("defensive_rating") == "Weak":
            optimistic = min(optimistic, base_growth + 1.0)
        if cycle_profile.get("is_true_cyclical") and not is_gip:
            optimistic = min(optimistic, base_growth + 1.5)
        elif cycle_profile.get("is_true_cyclical") and is_gip:
            optimistic = min(optimistic, base_growth + 4.0)
        if cycle_profile.get("is_true_cyclical") and anchor.get("metric") == "Profit":
            optimistic = min(optimistic, base_growth + 1.0)
        terminal = 2.0 if sector_profile.get("bank_like") else self.params["g_terminal"]

        return {
            "revenue_cagr": rev_cagr,
            "profit_cagr": profit_cagr,
            "owner_earnings_cagr": oe_cagr,
            "fcf_cagr": fcf_cagr,
            "anchor_cagr": anchor_cagr,
            "g_conservative": conservative,
            "g_base": base_growth,
            "g_optimistic": optimistic,
            "g_terminal": terminal,
            "growth_window": window,
        }

    def _discount_cash_flow(self, anchor_mm: float | None, growth_pct: float, discount_pct: float, terminal_pct: float) -> dict:
        if anchor_mm is None or anchor_mm <= 0 or discount_pct <= terminal_pct:
            return {"equity_value_mm": None, "pv_stage1_mm": None, "pv_terminal_mm": None}

        growth = growth_pct / 100
        discount = discount_pct / 100
        terminal = terminal_pct / 100
        cash_flow = anchor_mm
        stage_values = []
        pv_stage1 = 0.0

        for year in range(1, 11):
            if year <= 5:
                year_growth = growth
            else:
                fade_step = (growth - terminal) / 5
                year_growth = growth - fade_step * (year - 5)
            cash_flow *= 1 + year_growth
            stage_values.append(cash_flow)
            pv_stage1 += cash_flow / ((1 + discount) ** year)

        terminal_cash_flow = stage_values[-1] * (1 + terminal)
        terminal_value = terminal_cash_flow / (discount - terminal)
        pv_terminal = terminal_value / ((1 + discount) ** 10)

        return {
            "equity_value_mm": pv_stage1 + pv_terminal,
            "pv_stage1_mm": pv_stage1,
            "pv_terminal_mm": pv_terminal,
        }

    def _financial_intrinsic_value(self, book_per_share: float | None, roe_pct: float | None, growth_pct: float | None, required_return_pct: float | None) -> float | None:
        if book_per_share in (None, 0) or roe_pct is None or growth_pct is None or required_return_pct is None:
            return None
        roe = roe_pct / 100
        growth = growth_pct / 100
        required_return = required_return_pct / 100
        if required_return <= 0:
            return None
        if growth >= required_return:
            growth = max(0.0, required_return - 0.01)
        justified_pb = (roe - growth) / (required_return - growth)
        justified_pb = _clamp(justified_pb, 0.30, 2.50)
        return book_per_share * justified_pb

    def _compute_scenarios(self, anchor: dict, growth: dict, guardrails: dict, defensive: dict, sector_profile: dict) -> list[dict]:
        base_discount = guardrails.get("required_return") or 10.0
        optimistic_label = "乐观" if guardrails.get("optimistic_allowed", True) else "乐观(受限)"
        close = self._basic_info().get("close")

        if sector_profile.get("regulated_financial"):
            basic = self._basic_info()
            shares = basic.get("total_shares")
            latest_balance = self._latest_row("balance_sheet")
            equity = self._sf(latest_balance.get("total_hldr_eqy_exc_min_int")) if latest_balance is not None else None
            book_per_share = defensive.get("tangible_book_per_share") or self._per_share(None if equity is None else equity / 1e6, shares)
            latest_roe = self._latest_roe_pct() or 10.0
            base_roe = min(latest_roe, 13.0 if sector_profile.get("bank_like") else 14.0)
            conservative_roe = max(8.0 if sector_profile.get("bank_like") else 7.0, base_roe - 2.0)
            optimistic_roe = min(14.5 if sector_profile.get("bank_like") else 16.0, max(base_roe + 1.0, latest_roe))
            scenario_defs = [
                ("保守", growth["g_conservative"], base_discount + 0.5, conservative_roe),
                ("基准", growth["g_base"], base_discount, base_roe),
                (optimistic_label, growth["g_optimistic"] if guardrails.get("optimistic_allowed", True) else min(growth["g_optimistic"], growth["g_base"]), max(9.0, base_discount - 0.5), optimistic_roe),
            ]
            scenarios = []
            for name, g, discount, roe_assumption in scenario_defs:
                per_share = self._financial_intrinsic_value(book_per_share, roe_assumption, g, discount)
                upside = None
                if per_share is not None and close and close > 0:
                    upside = (per_share / close - 1) * 100
                scenarios.append({
                    "scenario": name,
                    "growth": g,
                    "discount": discount,
                    "terminal": growth["g_terminal"],
                    "equity_value_mm": None,
                    "pv_stage1_mm": None,
                    "pv_terminal_mm": None,
                    "per_share": per_share,
                    "upside_pct": upside,
                    "roe_assumption": roe_assumption,
                    "book_per_share": book_per_share,
                })
            return scenarios

        scenario_defs = [
            ("保守", growth["g_conservative"], base_discount + 1.0),
            ("基准", growth["g_base"], base_discount),
            (optimistic_label, growth["g_optimistic"] if guardrails.get("optimistic_allowed", True) else min(growth["g_optimistic"], growth["g_base"]), max(growth["g_terminal"] + 1.0, base_discount - 1.0, 7.0)),
        ]

        shares = self._basic_info().get("total_shares")
        scenarios = []
        for name, g, discount in scenario_defs:
            valuation = self._discount_cash_flow(anchor.get("normalized"), g, discount, growth["g_terminal"])
            per_share = self._per_share(valuation["equity_value_mm"], shares)
            upside = None
            if per_share is not None and close and close > 0:
                upside = (per_share / close - 1) * 100
            scenarios.append({
                "scenario": name,
                "growth": g,
                "discount": discount,
                "terminal": growth["g_terminal"],
                "equity_value_mm": valuation["equity_value_mm"],
                "pv_stage1_mm": valuation["pv_stage1_mm"],
                "pv_terminal_mm": valuation["pv_terminal_mm"],
                "per_share": per_share,
                "upside_pct": upside,
            })
        return scenarios

    def _compute_sensitivity(self, anchor: dict, growth: dict, guardrails: dict, sector_profile: dict, defensive: dict) -> tuple[list[str], list[list[str]]]:
        base_discount = guardrails.get("required_return") or 10.0
        growth_points = [
            max(0.0, growth["g_base"] - 2.0),
            growth["g_base"],
            min(18.0, growth["g_base"] + (2.0 if guardrails.get("optimistic_allowed", True) else 1.0)),
        ]
        discount_points = [base_discount + 1.0, base_discount, max(growth["g_terminal"] + 1.0, base_discount - 1.0, 7.0)]
        shares = self._basic_info().get("total_shares")
        if sector_profile.get("regulated_financial"):
            latest_balance = self._latest_row("balance_sheet")
            equity = self._sf(latest_balance.get("total_hldr_eqy_exc_min_int")) if latest_balance is not None else None
            book_per_share = defensive.get("tangible_book_per_share") or self._per_share(None if equity is None else equity / 1e6, shares)
            latest_roe = self._latest_roe_pct() or 10.0
            growth_points = [max(0.0, growth["g_base"] - 1.0), growth["g_base"], min(6.0, growth["g_base"] + 1.0)]
            discount_points = [base_discount + 0.5, base_discount, max(8.5, base_discount - 0.5)]

            headers = ["折现率 \\ 增长率"] + [f"{g:.1f}%" for g in growth_points]
            rows = []
            for discount in discount_points:
                row = [f"{discount:.1f}%"]
                for g in growth_points:
                    per_share = self._financial_intrinsic_value(book_per_share, latest_roe, g, discount)
                    row.append(self._fmt_price(per_share))
                rows.append(row)
            return headers, rows

        headers = ["折现率 \\ 增长率"] + [f"{g:.1f}%" for g in growth_points]
        rows = []
        for discount in discount_points:
            row = [f"{discount:.1f}%"]
            for g in growth_points:
                value = self._discount_cash_flow(anchor.get("normalized"), g, discount, growth["g_terminal"])
                per_share = self._per_share(value["equity_value_mm"], shares)
                row.append(self._fmt_price(per_share))
            rows.append(row)
        return headers, rows

    def _compute_ee(self, anchor: dict) -> dict:
        basic = self._basic_info()
        latest_balance = self._latest_row("balance_sheet")
        latest_income = self._latest_row("income")

        market_cap_mm = basic.get("mkt_cap_mm")
        total_liab_raw = self._sf(latest_balance.get("total_liab")) if latest_balance is not None else None
        cash_raw = self._cash_total(latest_balance)
        profit_raw = self._sf(latest_income.get("n_income_attr_p")) if latest_income is not None else None
        interest_debt_raw = self._interest_bearing_debt(latest_balance)

        ee = None
        if market_cap_mm is not None and total_liab_raw is not None and cash_raw is not None and profit_raw and profit_raw > 0:
            enterprise_equity_cost_raw = market_cap_mm * 1e6 + total_liab_raw - cash_raw
            ee = enterprise_equity_cost_raw / profit_raw

        owner_acquisition_multiple = None
        normalized_raw = anchor.get("normalized_raw")
        if market_cap_mm is not None and interest_debt_raw is not None and cash_raw is not None:
            if normalized_raw and normalized_raw > 0:
                owner_purchase_raw = market_cap_mm * 1e6 + interest_debt_raw - cash_raw
                owner_acquisition_multiple = owner_purchase_raw / normalized_raw

        return {
            "market_cap_mm": market_cap_mm,
            "total_liab_raw": total_liab_raw,
            "cash_raw": cash_raw,
            "profit_raw": profit_raw,
            "interest_debt_raw": interest_debt_raw,
            "enterprise_purchase_price_raw": None if market_cap_mm is None or interest_debt_raw is None or cash_raw is None else market_cap_mm * 1e6 + interest_debt_raw - cash_raw,
            "ee": ee,
            "owner_acquisition_multiple": owner_acquisition_multiple,
        }

    def _compute_rough_value_metrics(self, anchor: dict, ee_data: dict, guardrails: dict, basic: dict, scenarios: list[dict], sector_profile: dict, defensive: dict) -> dict:
        shares = basic.get("total_shares")
        close = basic.get("close")
        normalized_raw = anchor.get("normalized_raw")
        liabilities = ee_data.get("interest_debt_raw")
        cash_raw = ee_data.get("cash_raw")
        required_return = guardrails.get("required_return")

        if sector_profile.get("regulated_financial"):
            margin_reference_price = None
            for scenario in scenarios:
                if scenario.get("scenario") == "基准":
                    margin_reference_price = scenario.get("per_share")
                    break
            rough_fair_price = margin_reference_price
            deep_candidates = [
                None if rough_fair_price is None else rough_fair_price * 0.8,
                defensive.get("tangible_book_per_share"),
            ]
            deep_candidates = [p for p in deep_candidates if p is not None and p > 0]
            deep_value_price = min(deep_candidates) if deep_candidates else None
            margin_of_safety_pct = None
            if margin_reference_price is not None and close and margin_reference_price > 0:
                margin_of_safety_pct = (margin_reference_price - close) / margin_reference_price * 100
            return {
                "rough_fair_price": rough_fair_price,
                "deep_value_price": deep_value_price,
                "margin_reference_price": margin_reference_price,
                "margin_of_safety_pct": margin_of_safety_pct,
                "required_return_for_deep_value": required_return,
            }

        if normalized_raw is None or liabilities is None or cash_raw is None or not shares or not required_return:
            return {
                "rough_fair_price": None,
                "deep_value_price": None,
                "margin_of_safety_pct": None,
                "required_return_for_deep_value": None,
            }

        def _equity_price(target_return_pct: float) -> float | None:
            target_return = target_return_pct / 100
            if target_return <= 0:
                return None
            target_enterprise_raw = normalized_raw / target_return
            target_equity_raw = target_enterprise_raw - liabilities + cash_raw
            if target_equity_raw <= 0:
                return None
            return target_equity_raw / shares

        rough_fair_price = _equity_price(required_return)
        deep_required_return = min(15.0, required_return + 3.0)
        deep_value_price_by_return = _equity_price(deep_required_return)
        deep_value_price_by_discount = None if rough_fair_price is None else rough_fair_price * 0.7

        candidates = [p for p in [deep_value_price_by_return, deep_value_price_by_discount] if p is not None and p > 0]
        deep_value_price = min(candidates) if candidates else None

        margin_reference_price = None
        for scenario in scenarios:
            if scenario.get("scenario") == "基准":
                margin_reference_price = scenario.get("per_share")
                break
        if margin_reference_price is None:
            margin_reference_price = rough_fair_price

        margin_of_safety_pct = None
        if margin_reference_price is not None and close and margin_reference_price > 0:
            margin_of_safety_pct = (margin_reference_price - close) / margin_reference_price * 100

        return {
            "rough_fair_price": rough_fair_price,
            "deep_value_price": deep_value_price,
            "margin_reference_price": margin_reference_price,
            "margin_of_safety_pct": margin_of_safety_pct,
            "required_return_for_deep_value": deep_required_return,
        }

    def _compute_expected_return_metrics(self, anchor: dict, growth: dict, ee_data: dict, basic: dict, guardrails: dict, scenarios: list[dict], dividend_metrics: dict, sector_profile: dict) -> dict:
        normalized_raw = anchor.get("normalized_raw")
        market_cap_mm = basic.get("mkt_cap_mm")
        enterprise_purchase_price_raw = ee_data.get("enterprise_purchase_price_raw")

        if sector_profile.get("regulated_financial"):
            close = basic.get("close")
            dividend_yield = dividend_metrics.get("forward_dividend_yield") or 0.0

            def _scenario_return(names: tuple[str, ...]) -> float | None:
                if close in (None, 0):
                    return None
                for scenario in scenarios:
                    if scenario.get("scenario") not in names:
                        continue
                    per_share = scenario.get("per_share")
                    if per_share is None or per_share <= 0:
                        return None
                    annualized = (per_share / close) ** (1 / 5) - 1
                    return annualized * 100 + dividend_yield
                return None

            return {
                "equity_cash_yield": None if normalized_raw is None or market_cap_mm in (None, 0) else normalized_raw / (market_cap_mm * 1e6) * 100,
                "acquisition_yield": None,
                "expected_return_conservative": _scenario_return(("保守",)),
                "expected_return_base": _scenario_return(("基准",)),
                "expected_return_optimistic": _scenario_return(("乐观", "乐观(受限)")),
                "growth_realization_factor": guardrails.get("growth_realization_factor"),
                "dividend_yield_primary": True,
            }

        equity_cash_yield = None
        if normalized_raw is not None and market_cap_mm is not None and market_cap_mm > 0:
            equity_cash_yield = normalized_raw / (market_cap_mm * 1e6) * 100

        acquisition_yield = None
        if normalized_raw is not None and enterprise_purchase_price_raw and enterprise_purchase_price_raw > 0:
            acquisition_yield = normalized_raw / enterprise_purchase_price_raw * 100

        def _total_return(growth_key: str) -> float | None:
            if acquisition_yield is None:
                return None
            g = growth.get(growth_key)
            realization = guardrails.get("growth_realization_factor") or 0.75
            return None if g is None else acquisition_yield + g * realization

        return {
            "equity_cash_yield": equity_cash_yield,
            "acquisition_yield": acquisition_yield,
            "expected_return_conservative": _total_return("g_conservative"),
            "expected_return_base": _total_return("g_base"),
            "expected_return_optimistic": _total_return("g_optimistic"),
            "growth_realization_factor": guardrails.get("growth_realization_factor"),
        }

    def generate_output(self) -> str:
        basic = self._basic_info()
        sector_profile = self._sector_profile(basic)
        wacc_data = self.valuation_engine.compute_wacc()
        oe_series, maint_profile = self._compute_owner_earnings_series()
        cycle_profile = self._compute_cycle_profile(oe_series, sector_profile)
        anchor = self._pick_anchor_metric(oe_series, cycle_profile, sector_profile)
        dividend_metrics = self._compute_dividend_metrics(basic)
        defensive_metrics = self._compute_defensive_metrics(oe_series, anchor, cycle_profile)
        capital_allocation = self._compute_capital_allocation_metrics(basic, anchor, dividend_metrics)
        growth = self._compute_growth_profile(anchor, oe_series, defensive_metrics, cycle_profile, sector_profile)
        guardrails = self._compute_quality_guardrails(oe_series, anchor, defensive_metrics, capital_allocation, wacc_data, cycle_profile, sector_profile)
        scenarios = self._compute_scenarios(anchor, growth, guardrails, defensive_metrics, sector_profile)
        ee_data = self._compute_ee(anchor)
        rough_value_metrics = self._compute_rough_value_metrics(anchor, ee_data, guardrails, basic, scenarios, sector_profile, defensive_metrics)
        expected_return_metrics = self._compute_expected_return_metrics(anchor, growth, ee_data, basic, guardrails, scenarios, dividend_metrics, sector_profile)
        sensitivity_headers, sensitivity_rows = self._compute_sensitivity(anchor, growth, guardrails, sector_profile, defensive_metrics)
        bank_health = self._compute_bank_health_metrics() if sector_profile.get("bank_like") else None
        acquisition_yield = None
        if anchor.get("normalized_raw") is not None and ee_data.get("enterprise_purchase_price_raw"):
            acquisition_yield = anchor["normalized_raw"] / ee_data["enterprise_purchase_price_raw"] * 100
        payback_years = None if acquisition_yield in (None, 0) else 100 / acquisition_yield

        lines = [
            format_header(1, f"价值分析预计算 — {basic.get('name') or self.ts_code}（{self.ts_code}）"),
            "",
            "> 以下结果为 Python 确定性预计算，供 /value-analysis 在最终判断时直接引用。",
            "> LLM 负责结合 business-analysis 做定性调整，不重复做底层算术。",
            "",
            "---",
            "",
        ]

        lines.append(format_header(2, "一、市场与估值快照"))
        lines.append("")
        snapshot_rows = [
            ["当前股价", f"{self._fmt_price(basic.get('close'))} {self._price_unit()}"],
            ["总市值", f"{self._fmt_price(basic.get('mkt_cap_mm'))} {self._unit()}"],
            ["PE (TTM)", self._fmt_price(basic.get('pe_ttm'))],
            ["PB", self._fmt_price(basic.get('pb'))],
            ["默认要求回报率", self._fmt_pct(guardrails.get('required_return'))],
            ["WACC 参考", self._fmt_pct(guardrails.get('wacc_reference'))],
            ["无风险利率", self._fmt_pct(wacc_data.get('rf'))],
            ["预期股息率（FY）", self._fmt_pct(dividend_metrics.get('forward_dividend_yield'))],
            ["预期股息率（TTM）", self._fmt_pct(dividend_metrics.get('trailing_12m_dividend_yield'))],
        ]
        lines.append(format_table(["项目", "值"], snapshot_rows, alignments=["l", "r"]))
        lines.append("")

        lines.append(format_header(2, "二、赚钱能力锚点"))
        lines.append("")
        anchor_rows = []
        for row in oe_series[:5]:
            anchor_rows.append([
                row["year"],
                format_number(row.get("profit")),
                format_number(row.get("ocf")),
                format_number(row.get("fcf")),
                format_number(row.get("owner_earnings")),
                format_number(row.get("owner_earnings_conservative")),
            ])
        lines.append(format_table(
            ["年份", "归母净利润", "经营现金流", "自由现金流", "Owner Earnings", "保守 OE"],
            anchor_rows,
            alignments=["c", "r", "r", "r", "r", "r"],
        ))
        lines.append("")
        lines.append(f"**默认估值锚**: {anchor['metric']}")
        lines.append(f"**选择理由**: {anchor['reason']}")
        normalized_mm = anchor.get('normalized')
        lines.append(f"**归一化锚值**: {'—' if normalized_mm is None else f'{normalized_mm:,.2f}'} {self._unit()}")
        lines.append(f"**归一化窗口**: {anchor.get('normalization_window', cycle_profile.get('normalization_window'))} 年")
        lines.append(f"**锚值收益率（相对市值）**: {self._fmt_pct(anchor.get('yield_on_market_cap'))}")
        lines.append(f"**维持性 Capex 系数**: {maint_profile['base_ratio']:.2f}x" if maint_profile.get("base_ratio") is not None else "**维持性 Capex 系数**: —")
        lines.append("**保守 OE 说明**: 默认把全部 Capex 视作维护性投入，用于防止 Owner Earnings 过于乐观。")
        lines.append("")

        if bank_health:
            lines.append(format_header(2, "二A、银行资产质量与监管指标"))
            lines.append("")
            m = bank_health["metrics"]
            if not bank_health["available"]:
                lines.append(f"> {bank_health['note']}")
                lines.append("> 以下分析将主要依赖 ROE、利润趋势和股息率进行银行估值判断。")
            else:
                bank_rows = [
                    ["不良贷款率 (NPL)", self._fmt_pct(m.get("npl_ratio_latest")), m.get("npl_ratio_trend", "—"),
                     "核心风控指标。<1%优秀，<1.5%良好，>2%需警惕"],
                    ["拨备覆盖率", self._fmt_pct(m.get("prov_cov_latest")), m.get("prov_cov_trend", "—"),
                     "风险缓冲垫。>300%充裕（隐含隐藏利润），>200%充足"],
                    ["资本充足率", self._fmt_pct(m.get("car_latest")), "—",
                     "监管杠杆约束。银行股必须维持足够资本金"],
                    ["核心一级资本充足率", self._fmt_pct(m.get("core_car_latest")), "—",
                     "最严格资本要求。越高越安全，但过高可能拉低ROE"],
                    ["净息差 (NIM)", self._fmt_pct(m.get("nim_latest")), m.get("nim_trend", "—"),
                     "核心盈利驱动。息差收窄是当前银行利润承压主因"],
                    ["成本收入比", self._fmt_pct(m.get("cir_latest")), "—",
                     "经营效率。越低越好，反映费用控制能力"],
                ]
                lines.append(format_table(
                    ["指标", "最新值", "趋势", "解读"],
                    bank_rows,
                    alignments=["l", "r", "c", "l"],
                ))
                lines.append("")
                lines.append(f"**资产质量综合评价**: {m.get('quality_assessment', '—')}")
                if m.get("prov_cov_latest") and m["prov_cov_latest"] > 300:
                    lines.append(f"> 拨备覆盖率超过300%，表明公司\"隐藏了利润\"——将更多当期收益放入了风险准备金。在极端行情下，高拨备银行生存能力更强，利润调节空间也更大。")
                if m.get("npl_ratio_latest") and m["npl_ratio_latest"] < 1.0:
                    lines.append(f"> 不良贷款率低于1%，风控质量突出。在中国，个人贷款违约成本远高于企业贷款，高零售占比的银行（如招行、邮储）通常不良率更低。")
                if m.get("nim_latest") is not None:
                    if m["nim_latest"] < 2.0:
                        lines.append(f"> 净息差已降至{m['nim_latest']:.2f}%，核心盈利承压。主要受LPR下行、存款定期化和零售贷款收缩三重压力影响。")
                    lines.append("> 净息差是当前银行股最关键的观察指标——它反映了\"面粉成本（存款利率）\"与\"面包价格（贷款利率）\"之间的利差。")
            lines.append("")

        lines.append(format_header(2, "三、跨周期归一化判断"))
        lines.append("")
        is_true_cyclical = cycle_profile.get("is_true_cyclical", False)
        is_asset_heavy = cycle_profile.get("is_asset_heavy", False)
        cycle_label = "否"
        if is_true_cyclical and is_asset_heavy:
            cycle_label = "是（周期 + 重资产）"
        elif is_true_cyclical:
            cycle_label = "是（周期特征）"
        elif is_asset_heavy:
            cycle_label = "是（重资产/高资本开支）"
        is_gip = cycle_profile.get("is_growth_investment_phase", False)
        cycle_rows = [
            ["是否判定为周期/重资产特征", cycle_label],
            ["是否处于增长投资期（GIP）", "是" if is_gip else "否"],
            ["归一化窗口", f"{cycle_profile.get('normalization_window')} 年"],
            ["增长窗口", f"{growth.get('growth_window')} 年"],
            ["利润波动系数", self._fmt_pct(cycle_profile.get("profit_variability"))],
            ["锚值波动系数", self._fmt_pct(cycle_profile.get("anchor_variability"))],
            ["收入同比波动", self._fmt_pct(cycle_profile.get("revenue_volatility"))],
            ["Capex/收入", self._fmt_pct(cycle_profile.get("capex_to_revenue"))],
            ["资产周转率", self._fmt_ratio(cycle_profile.get("asset_turnover"))],
        ]
        lines.append(format_table(["指标", "值"], cycle_rows, alignments=["l", "r"]))
        lines.append("")
        if cycle_profile.get("signals"):
            lines.append("**跨周期信号**：")
            for note in cycle_profile["signals"]:
                lines.append(f"- {note}")
            lines.append("")
        if is_gip:
            lines.append("> **增长投资期（GIP）提示**：公司当前处于大规模资本投资阶段，")
            lines.append("> 大量资本开支是用于建设未来产能的增长性投资，不应全部视为维护性支出。")
            lines.append("> LLM 在进行价值分析时应：")
            lines.append("> (1) 区分维护性Capex（≈D&A）和增长性Capex（超出D&A部分→建设未来盈利资产）")
            lines.append("> (2) 不要将增长期Capex简单视作\"股东拿不到的现金\"——它在建的是未来的竞争优势")
            lines.append("> (3) 可考虑使用更低维持性Capex系数（如G=1.0x），将增长性投资的价值纳入长期增速假设")
            lines.append("> (4) 短期利润受压、FCF为负、ROE波动等信号，在GIP语境下主要反映投资节奏，而非经营恶化")
            lines.append("")

        lines.append(format_header(2, "四、防守层与资产 Backstop"))
        lines.append("")
        defensive_rows = [
            ["防守评级", self._fmt_label(defensive_metrics.get("defensive_rating"))],
            ["流动比率", self._fmt_ratio(defensive_metrics.get("current_ratio"))],
            ["速动比率", self._fmt_ratio(defensive_metrics.get("quick_ratio"))],
            ["现金/短债", self._fmt_ratio(defensive_metrics.get("cash_to_short_debt"))],
            ["经营现金流/短债", self._fmt_ratio(defensive_metrics.get("ocf_to_short_debt"))],
            ["利息保障倍数", self._fmt_multiple(defensive_metrics.get("interest_coverage"))],
            ["净债务/归一化锚值", self._fmt_ratio(defensive_metrics.get("net_debt_to_anchor"))],
            ["商誉/总资产", self._fmt_pct(defensive_metrics.get("goodwill_ratio"))],
            ["净流动资产/股", self._fmt_price(defensive_metrics.get("ncav_per_share"))],
            ["每股有形净资产", self._fmt_price(defensive_metrics.get("tangible_book_per_share"))],
        ]
        lines.append(format_table(["指标", "值"], defensive_rows, alignments=["l", "r"]))
        lines.append("")
        if defensive_metrics.get("severe_flags") or defensive_metrics.get("caution_flags"):
            lines.append("**防守层提示**：")
            for note in defensive_metrics.get("severe_flags", []):
                lines.append(f"- 严重：{note}")
            for note in defensive_metrics.get("caution_flags", []):
                lines.append(f"- 关注：{note}")
            lines.append("")

        lines.append(format_header(2, "五、资本配置与股东回报"))
        lines.append("")
        capital_rows = [
            ["资本配置姿态", self._fmt_label(capital_allocation.get("alignment"))],
            ["分红/利润", self._fmt_pct(capital_allocation.get("payout_to_profit"))],
            ["分红/归一化锚值", self._fmt_pct(capital_allocation.get("payout_to_anchor"))],
            ["年均回购金额", f"{format_number(capital_allocation.get('repurchase_annual_avg_raw'))} {self._unit()}"],
            ["股东收益率", self._fmt_pct(capital_allocation.get("shareholder_yield"))],
            ["股权质押比例", self._fmt_pct(capital_allocation.get("pledge_ratio"))],
        ]
        lines.append(format_table(["指标", "值"], capital_rows, alignments=["l", "r"]))
        lines.append("")
        if capital_allocation.get("notes"):
            lines.append("**资本配置提示**：")
            for note in capital_allocation["notes"]:
                lines.append(f"- {note}")
            lines.append("")

        lines.append(format_header(2, "六、要求回报率护栏"))
        lines.append("")
        guardrail_rows = [
            ["默认要求回报率", self._fmt_pct(guardrails.get("required_return"))],
            ["WACC 参考", self._fmt_pct(guardrails.get("wacc_reference"))],
            ["估值姿态", self._fmt_label(guardrails.get("valuation_mode"))],
            ["现金转化中位数", self._fmt_pct(guardrails.get("cash_conversion"))],
            ["负债/总资产", self._fmt_pct(guardrails.get("debt_asset_ratio"))],
            ["Owner Earnings 为正年数", str(guardrails.get("positive_oe_years"))],
            ["FCF 为正年数", str(guardrails.get("positive_fcf_years"))],
        ]
        lines.append(format_table(["指标", "值"], guardrail_rows, alignments=["l", "r"]))
        lines.append("")
        if guardrails.get("notes"):
            lines.append("**护栏说明**：")
            for note in guardrails["notes"]:
                lines.append(f"- {note}")
            lines.append("")

        lines.append(format_header(2, "七、毛毛估与回报指标"))
        lines.append("")
        rough_rows = [
            ["毛毛估合理参考价", self._fmt_price(rough_value_metrics.get("rough_fair_price"))],
            ["充分安全边际参考价", self._fmt_price(rough_value_metrics.get("deep_value_price"))],
            ["净流动资产保护价", self._fmt_price(defensive_metrics.get("ncav_per_share"))],
            ["有形净资产保护价", self._fmt_price(defensive_metrics.get("tangible_book_per_share"))],
            ["当前安全边际参考价", self._fmt_price(rough_value_metrics.get("margin_reference_price"))],
            ["当前安全边际", self._fmt_pct(rough_value_metrics.get("margin_of_safety_pct"))],
            ["预期回报率（保守）", self._fmt_pct(expected_return_metrics.get("expected_return_conservative"))],
            ["预期回报率（基准）", self._fmt_pct(expected_return_metrics.get("expected_return_base"))],
            ["预期回报率（乐观）", self._fmt_pct(expected_return_metrics.get("expected_return_optimistic"))],
            ["预期股息率（FY）", self._fmt_pct(dividend_metrics.get("forward_dividend_yield"))],
            ["预期股息率（TTM）", self._fmt_pct(dividend_metrics.get("trailing_12m_dividend_yield"))],
        ]
        lines.append(format_table(["指标", "值"], rough_rows, alignments=["l", "r"]))
        lines.append("")
        if sector_profile.get("regulated_financial"):
            lines.append("> 毛毛估合理参考价：对金融/银行类公司，优先按 ROE、资本成本与有形净资产的残余收益框架粗估，而不是直接套工业企业式 Owner Earnings DCF。")
            lines.append("> 充分安全边际参考价：在基准价值上进一步要求折价，并结合有形净资产 backstop 交叉验证。")
            lines.append("> 当前安全边际：默认相对\"基准情景内在价值\"计算；对银行要理解为相对正常化 ROE 与资本约束的折价，不宜机械理解为可立即分配的现金流折价。")
            lines.append("> **银行股息率估值法**：在当前经济下行、银行业绩承压期，股息率是最直观的估值锚。当前股息率 vs 历史股息率区间 vs 国债收益率，是判断银行股是否便宜的核心参考。")
            if dividend_metrics.get("forward_dividend_yield") is not None:
                lines.append(f"> 当前股息率 {dividend_metrics['forward_dividend_yield']:.2f}%，对比10年期国债 {wacc_data.get('rf', 0):.2f}%，息差优势{'明显' if dividend_metrics['forward_dividend_yield'] > wacc_data.get('rf', 0) + 2 else '一般'}。银行业\"存款搬家\"——储户将低息存款转为高股息银行股——是2024年以来银行股上涨的重要驱动力。")
        else:
            lines.append("> 毛毛估合理参考价：按归一化现金流与当前要求回报率粗估，不追求精确。")
            lines.append("> 充分安全边际参考价：在毛毛估基础上，再额外要求更高回报或更深折价。")
            lines.append("> 当前安全边际：默认相对“基准情景内在价值”计算，而不是直接相对极保守毛毛估价格，避免误把 owner-yield 下限当成主结论。")
        lines.append("> 资产保护价：不是主结论，而是在周期/重资产/高不确定性场景下回答“如果预测错了还有什么保护本金”。")
        lines.append("")

        lines.append(format_header(2, "八、EE 收购视角"))
        lines.append("")
        ee_rows = [
            ["市值", f"{self._fmt_price(ee_data.get('market_cap_mm'))} {self._unit()}"],
            ["总负债", f"{format_number(ee_data.get('total_liab_raw'))} {self._unit()}"],
            ["有息负债", f"{format_number(ee_data.get('interest_debt_raw'))} {self._unit()}"],
            ["现金", f"{format_number(ee_data.get('cash_raw'))} {self._unit()}"],
            ["归母净利润", f"{format_number(ee_data.get('profit_raw'))} {self._unit()}"],
            ["EE = (市值 + 负债 - 现金) / 利润", self._fmt_multiple(ee_data.get('ee'))],
            ["Owner Earnings 收购倍数", self._fmt_multiple(ee_data.get('owner_acquisition_multiple'))],
            ["收购收益率（锚值/企业收购价）", self._fmt_pct(acquisition_yield)],
            ["静态回本年限", self._fmt_years(payback_years)],
        ]
        lines.append(format_table(["变量", "值"], ee_rows, alignments=["l", "r"]))
        lines.append("")
        lines.append("> 解释：EE 不是单独结论，而是站在“买下整个公司”的角度观察经营实体定价是否昂贵。")
        lines.append("")

        lines.append(format_header(2, "九、默认增长假设"))
        lines.append("")
        growth_rows = [
            ["收入 CAGR", self._fmt_pct(growth.get("revenue_cagr"))],
            ["利润 CAGR", self._fmt_pct(growth.get("profit_cagr"))],
            ["Owner Earnings CAGR", self._fmt_pct(growth.get("owner_earnings_cagr"))],
            ["FCF CAGR", self._fmt_pct(growth.get("fcf_cagr"))],
            ["增长观察窗口", f"{growth.get('growth_window')} 年"],
            ["默认保守增长", self._fmt_pct(growth.get("g_conservative"))],
            ["默认基准增长", self._fmt_pct(growth.get("g_base"))],
            ["默认乐观增长", self._fmt_pct(growth.get("g_optimistic"))],
            ["终值增长率", self._fmt_pct(growth.get("g_terminal"))],
        ]
        lines.append(format_table(["指标", "值"], growth_rows, alignments=["l", "r"]))
        lines.append("")

        lines.append(format_header(2, "十、情景估值"))
        lines.append("")
        scenario_rows = []
        for scenario in scenarios:
            scenario_rows.append([
                scenario["scenario"],
                self._fmt_pct(scenario["growth"]),
                self._fmt_pct(scenario["discount"]),
                self._fmt_pct(scenario["terminal"]),
                self._fmt_price(scenario["per_share"]),
                self._fmt_pct(scenario["upside_pct"]),
            ])
        lines.append(format_table(
            ["情景", "增长", "折现率", "终值增长", "内在价值/股", "相对现价"],
            scenario_rows,
            alignments=["c", "r", "r", "r", "r", "r"],
        ))
        lines.append("")
        if not guardrails.get("optimistic_allowed", True):
            lines.append("> 乐观情景已受限：当前防守层、锚值质量或资本配置不足以支持积极外推。")
            lines.append("")

        lines.append(format_header(2, "十一、敏感性表"))
        lines.append("")
        lines.append(format_table(sensitivity_headers, sensitivity_rows, alignments=["l", "r", "r", "r"]))
        lines.append("")

        lines.append(format_header(2, "十二、给 LLM 的调整接口"))
        lines.append("")
        is_gip = cycle_profile.get("is_growth_investment_phase", False)
        interface_rows = [
            ["估值锚", anchor["metric"], "如 business-analysis 认为利润含金量偏低，可降级为更保守锚值"],
            ["跨周期归一化", "是（周期/重资产）" if cycle_profile.get("is_true_cyclical") or cycle_profile.get("is_asset_heavy") else "否", "若行业强周期或重资产，应优先采用更长窗口与资产 backstop"],
            ["增长投资期（GIP）", "是" if is_gip else "否", "若为是：增长性Capex≠维护性Capex，当前利润/FCF被投资节奏压低，估值应反映投资完成后的盈利潜力"],
            ["基准增长", self._fmt_pct(growth.get("g_base")), "结合护城河、行业空间、管理层兑现记录调整；GIP公司可上调至接近行业实际增速"],
            ["要求回报率", self._fmt_pct(guardrails.get("required_return")), "更接近长期投资者机会成本，而非机械WACC；GIP公司的高负债/低流动比率反映的是产能建设而非经营恶化"],
            ["终值增长", self._fmt_pct(growth.get("g_terminal")), "强护城河可维持，弱护城河应下调"],
            ["估值姿态", self._fmt_label(guardrails.get("valuation_mode")), "若诚信/复杂性/防守层不足，最终动作建议应显著更严格；GIP公司不应因Adequate防守层自动降为Conservative"],
            ["EE", self._fmt_multiple(ee_data.get("ee")), "用收购视角解释当前估值是否匹配质量；GIP公司当前利润被投资压低，EE偏高不代表必然高估"],
            ["毛毛估价格", self._fmt_price(rough_value_metrics.get("rough_fair_price")), "极保守的owner-yield资本化下限，GIP公司不应将其误当作主结论——它假设公司零增长且所有Capex都是维护性"],
            ["深度安全边际价", self._fmt_price(rough_value_metrics.get("deep_value_price")), "用于判断是否便宜到值得强出手"],
            ["资产保护价", self._fmt_price(defensive_metrics.get("asset_backstop_price")), "周期/重资产/高不确定性标的需同时参考资产负债表 backstop；GIP公司有形资产在增长，backstop会随时间上升"],
        ]
        lines.append(format_table(["项目", "Python 默认", "LLM 调整说明"], interface_rows, alignments=["l", "r", "l"]))
        lines.append("")
        lines.append("输出文件应由 /value-analysis 最终写成 `{company}_{code}_价值分析报告.md`。")

        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Value-analysis precompute engine")
    parser.add_argument("--code", required=True, help="Stock code (e.g. 600887, 00700.HK, AAPL)")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    args = parser.parse_args()

    ts_code = validate_stock_code(args.code)
    token = get_token()

    from tushare_collector import TushareClient

    print(f"[value_analysis_engine] 正在采集 {ts_code} 数据...", file=sys.stderr)
    client = TushareClient(token)
    client.assemble_data_pack(ts_code)

    print("[value_analysis_engine] 正在计算价值分析锚点...", file=sys.stderr)
    engine = ValueAnalysisEngine(ts_code, args.output_dir, client)
    output_md = engine.generate_output()

    os.makedirs(args.output_dir, exist_ok=True)
    out_path = os.path.join(args.output_dir, "value_computed.md")
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(output_md)

    print(f"[value_analysis_engine] 完成: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
