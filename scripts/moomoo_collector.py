"""Read-only moomoo OpenD adapter for the existing US analysis pipeline."""

from __future__ import annotations

import os
import re
import math

import pandas as pd

from format_utils import format_header, format_number, format_table
from tushare_modules import (
    AssemblyMixin, DerivedMetricsMixin, FinancialsMixin, InfrastructureMixin,
    OtherDataMixin, YFinanceMixin, US_BALANCE_MAP, US_CASHFLOW_MAP,
    US_INCOME_MAP,
)


# Stable moomoo financial field IDs, scoped by statement type. Display names
# are localized by OpenD and therefore cannot be used as identifiers.
INCOME_FIELDS = {
    8002: "revenue", 8003: "oper_cost", 8004: "gross_profit",
    8010: "rd_exp", 8017: "operate_profit", 8037: "n_income",
    8043: "n_income_attr_p", 8047: "basic_eps", 8048: "diluted_eps",
}
BALANCE_FIELDS = {
    8004: "money_cap", 8007: "accounts_receiv", 8017: "inventories",
    8002: "total_cur_assets", 8024: "fix_assets", 8001: "total_assets",
    8051: "acct_payable", 8058: "st_borr", 8049: "total_cur_liab",
    8069: "lt_borr", 8048: "total_liab",
    8085: "total_hldr_eqy_exc_min_int",
}
CASHFLOW_FIELDS = {
    8015: "n_cashflow_act", 8042: "n_cashflow_inv_act",
    8056: "n_cash_flows_fnc_act", 8019: "depr_fa_coga_dpba",
    8061: "c_pay_dist_dpcp_int_exp",
}
METRIC_FIELDS = {
    14029: "roe_avg", 14002: "gross_profit_ratio",
    14005: "net_profit_ratio",
}
STATEMENT_TYPES = {
    "us_income": (1, INCOME_FIELDS, US_INCOME_MAP),
    "us_balancesheet": (2, BALANCE_FIELDS, US_BALANCE_MAP),
    "us_cashflow": (3, CASHFLOW_FIELDS, US_CASHFLOW_MAP),
}
REQUIRED_FIELDS = {
    "us_income": {"revenue", "n_income"},
    "us_balancesheet": {"total_assets", "total_liab", "total_hldr_eqy_exc_min_int"},
    "us_cashflow": {"n_cashflow_act", "c_pay_acq_const_fiolta"},
    "us_fina_indicator": {"roe_avg"},
}
DIVIDEND_RE = re.compile(r"(?:1\s*股\s*派息|dividend\s*of\s*)(\d+(?:\.\d+)?)\s*USD", re.I)


def moomoo_code(ts_code: str) -> str:
    """Convert the project's AAPL.US notation to moomoo's US.AAPL."""
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]*\.US", ts_code.upper()):
        raise ValueError("moomoo 数据源目前只支持美股代码，例如 AAPL.US")
    return "US." + ts_code[:-3].upper()


def _checked(result, endpoint: str):
    ret, data = result
    if ret != 0:
        raise RuntimeError(f"moomoo {endpoint} 失败: {data}")
    return data


class MoomooClient(
    InfrastructureMixin, YFinanceMixin, FinancialsMixin, OtherDataMixin,
    DerivedMetricsMixin, AssemblyMixin,
):
    """Expose normalized moomoo US data to the existing computation mixins."""

    def __init__(self, quote_context=None, *, host=None, port=None,
                 enable_yfinance=True, as_of=None):
        self._owns_context = quote_context is None
        if quote_context is None:
            try:
                from moomoo import OpenQuoteContext
            except ImportError as exc:
                raise RuntimeError(
                    "缺少 moomoo SDK，请安装 requirements.txt 中的 moomoo_api"
                ) from exc
            host = host or os.environ.get("MOOMOO_HOST", "127.0.0.1")
            port = int(port or os.environ.get("MOOMOO_PORT", "11111"))
            quote_context = OpenQuoteContext(host=host, port=port)
        self.quote_context = quote_context
        self._store = {}
        self._financial_cache = {}
        self._snapshot_cache = {}
        self._fetched = set()
        self._yf_available = enable_yfinance
        self._fy_end_month = 12
        self._currency = "USD"
        self._source_label = "moomoo OpenAPI"
        self._as_of = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now()

    def close(self):
        if self._owns_context:
            self.quote_context.close()

    def _detect_fy_end_month(self, df: pd.DataFrame) -> int:
        # The report's ANNUAL marker is authoritative; inferring the fiscal
        # month from a mix of quarterly dates can select Q1 for calendar FYs.
        return self._fy_end_month

    def _snapshot(self, ts_code: str) -> pd.Series:
        code = moomoo_code(ts_code)
        if code not in self._snapshot_cache:
            data = _checked(self.quote_context.get_market_snapshot([code]),
                            "get_market_snapshot")
            if not isinstance(data, pd.DataFrame) or data.empty:
                raise RuntimeError(f"moomoo 未返回 {code} 的行情")
            row = data[data["code"] == code]
            if row.empty:
                raise RuntimeError(f"moomoo 行情中缺少 {code}")
            snapshot = row.iloc[0]
            price = self._safe_float(snapshot.get("last_price"))
            market_value = self._safe_float(snapshot.get("total_market_val"))
            if not price or price <= 0 or not market_value or market_value <= 0:
                raise RuntimeError("moomoo 行情缺少有效价格或总市值")
            self._snapshot_cache[code] = snapshot
            self._fetched.add("snapshot")
        return self._snapshot_cache[code]

    def _financial_reports(self, ts_code: str, statement_type: int) -> list[dict]:
        key = (moomoo_code(ts_code), statement_type)
        if key in self._financial_cache:
            return self._financial_cache[key]
        reports = []
        next_key = None
        for _ in range(4):
            data = _checked(self.quote_context.get_financials_statements(
                key[0], statement_type=statement_type,
                financial_type="QUARTERLY_ANNUAL", currency_code="USD",
                next_key=next_key, num=50,
            ), "get_financials_statements")
            if not isinstance(data, dict):
                raise RuntimeError("moomoo 财报响应格式错误")
            page = data.get("report_list")
            if not isinstance(page, list):
                raise RuntimeError("moomoo 财报缺少 report_list")
            for report in page:
                if report.get("currency_code") != "USD":
                    raise RuntimeError("moomoo 财报币种不是 USD")
                reports.append(report)
            annual = [r for r in reports if r.get("financial_type") == "ANNUAL"]
            next_key = data.get("next_key")
            if len(annual) >= 10 or next_key in (None, "", "-1"):
                break
        if not reports or not any(r.get("financial_type") == "ANNUAL" for r in reports):
            raise RuntimeError("moomoo 缺少年度财报")
        annual_date = next(r["date_time_str"] for r in reports
                           if r.get("financial_type") == "ANNUAL")
        self._fy_end_month = int(annual_date[5:7])
        self._financial_cache[key] = reports
        return reports

    @staticmethod
    def _report_values(report: dict) -> dict[int, dict]:
        values = {}
        for item in report.get("item_list", []):
            if "field_id" not in item or "data" not in item:
                continue
            try:
                valid = math.isfinite(float(item["data"]))
            except (TypeError, ValueError, OverflowError):
                valid = False
            if valid:
                values[int(item["field_id"])] = item
        return values

    def _statement_lines(self, ts_code: str, api_name: str) -> pd.DataFrame:
        statement_type, field_map, name_map = STATEMENT_TYPES[api_name]
        rows = []
        for report in self._financial_reports(ts_code, statement_type):
            end_date = report["date_time_str"].replace("-", "")
            items = self._report_values(report)
            values = {field: items[id_]["data"] for id_, field in field_map.items()
                      if id_ in items}
            if api_name == "us_cashflow" and 8072 in items and 8015 in items:
                # The source's FCF and OCF imply capital expenditure. Avoid
                # treating net fixed-asset transactions as gross capex.
                capex = items[8015]["data"] - items[8072]["data"]
                if capex >= 0:
                    values["c_pay_acq_const_fiolta"] = capex
            if report.get("financial_type") == "ANNUAL":
                missing = REQUIRED_FIELDS[api_name] - values.keys()
                if missing:
                    raise RuntimeError(
                        f"moomoo {api_name} {end_date} 缺少核心字段: {', '.join(sorted(missing))}"
                    )
            for field, value in values.items():
                rows.append({"ts_code": ts_code[:-3], "end_date": end_date,
                             "ind_name": name_map[field], "ind_value": value})
        self._fetched.add(api_name)
        return pd.DataFrame(rows, columns=["ts_code", "end_date", "ind_name", "ind_value"])

    def _metrics(self, ts_code: str) -> pd.DataFrame:
        records = []
        def by_date(statement_type):
            result = {}
            for report in self._financial_reports(ts_code, statement_type):
                stamp = report["date_time_str"]
                if stamp not in result or report.get("financial_type") == "ANNUAL":
                    result[stamp] = self._report_values(report)
            return result

        income_by_date = by_date(1)
        balance_by_date = by_date(2)
        for report in self._financial_reports(ts_code, 4):
            date_str = report["date_time_str"]
            items = self._report_values(report)
            row = {"ts_code": ts_code[:-3], "end_date": date_str.replace("-", "")}
            row.update({field: items[id_]["data"] for id_, field in METRIC_FIELDS.items()
                        if id_ in items})
            income = income_by_date.get(date_str, {})
            balance = balance_by_date.get(date_str, {})
            if 8002 in income:
                row["operate_income_yoy"] = income[8002].get("yoy")
            if 8043 in income:
                row["holder_profit_yoy"] = income[8043].get("yoy")
            if 8001 in balance and 8048 in balance and balance[8001]["data"]:
                row["debt_asset_ratio"] = (
                    balance[8048]["data"] / balance[8001]["data"] * 100
                )
            if report.get("financial_type") == "ANNUAL":
                missing = REQUIRED_FIELDS["us_fina_indicator"] - row.keys()
                if missing:
                    raise RuntimeError("moomoo 年度财务指标缺少 ROE")
            records.append(row)
        self._fetched.add("us_fina_indicator")
        return pd.DataFrame(records, columns=[
            "ts_code", "end_date", "roe_avg", "gross_profit_ratio",
            "net_profit_ratio", "debt_asset_ratio", "operate_income_yoy",
            "holder_profit_yoy", "bps", "pe_ttm", "pb_ttm",
            "total_market_cap",
        ])

    def _safe_call(self, api_name: str, **kwargs) -> pd.DataFrame:
        ts_code = kwargs.get("ts_code")
        if not ts_code:
            raise ValueError("moomoo 数据源必须指定美股代码")
        ts_code = ts_code if ts_code.endswith(".US") else f"{ts_code}.US"
        moomoo_code(ts_code)
        if api_name == "us_basic":
            s = self._snapshot(ts_code)
            return pd.DataFrame([{
                "ts_code": ts_code[:-3], "name": s.get("name"),
                "enname": s.get("name"), "market": "US",
                "list_date": str(s.get("listing_date", "")).replace("-", ""),
            }])
        if api_name == "us_daily":
            s = self._snapshot(ts_code)
            trade_date = str(s.get("update_time", ""))[:10].replace("-", "")
            if not re.fullmatch(r"\d{8}", trade_date):
                raise RuntimeError("moomoo 行情缺少有效交易日期")
            return pd.DataFrame([{
                "ts_code": ts_code[:-3], "trade_date": trade_date,
                "open": s.get("open_price"), "high": s.get("high_price"),
                "low": s.get("low_price"), "close": s.get("last_price"),
                "vol": s.get("volume"), "amount": s.get("turnover"),
                "pe": s.get("pe_ttm_ratio"), "pb": s.get("pb_ratio"),
                "total_mv": s.get("total_market_val"),
            }])
        if api_name in STATEMENT_TYPES:
            return self._statement_lines(ts_code, api_name)
        if api_name == "us_fina_indicator":
            return self._metrics(ts_code)
        raise ValueError(f"moomoo 适配层不支持 Tushare 接口 {api_name}")

    def _cached_basic_call(self, api_name: str, **kwargs) -> pd.DataFrame:
        return self._safe_call(api_name, **kwargs)

    def _cached_us_daily(self, ts_code: str = None) -> pd.DataFrame:
        return self._safe_call("us_daily", ts_code=ts_code)

    def _history(self, ts_code: str, *, weekly: bool) -> pd.DataFrame:
        end = self._as_of.normalize()
        start = end - pd.DateOffset(years=10 if weekly else 1)
        frames = []
        page_key = None
        for _ in range(20):
            ret, data, next_key = self.quote_context.request_history_kline(
                moomoo_code(ts_code), start=start.date().isoformat(),
                end=end.date().isoformat(),
                ktype="K_WEEK" if weekly else "K_DAY", autype="None",
                max_count=1000, page_req_key=page_key,
            )
            if ret != 0:
                raise RuntimeError(f"moomoo 历史 K 线失败: {data}")
            if not isinstance(data, pd.DataFrame):
                raise RuntimeError("moomoo 历史 K 线响应格式错误")
            frames.append(data)
            if next_key is None:
                break
            if next_key == page_key:
                raise RuntimeError("moomoo 历史 K 线分页未前进")
            page_key = next_key
        else:
            raise RuntimeError("moomoo 历史 K 线超过分页上限")
        if not frames:
            return pd.DataFrame()
        result = pd.concat(frames, ignore_index=True)
        if result.empty:
            return result
        result = result.rename(columns={"volume": "vol", "turnover": "amount"})
        result["trade_date"] = result["time_key"].astype(str).str[:10].str.replace("-", "", regex=False)
        result["ts_code"] = ts_code[:-3]
        return result[["ts_code", "trade_date", "open", "high", "low",
                       "close", "vol", "amount"]].drop_duplicates("trade_date")

    def _yf_weekly_history(self, ts_code: str) -> pd.DataFrame:
        history = self._history(ts_code, weekly=True)
        if not history.empty:
            self._fetched.add("weekly")
        return history

    def _get_market_data_us(self, ts_code: str) -> str:
        s = self._snapshot(ts_code)
        rows = [
            ["最新价格 (USD)", f"{s['last_price']:.2f}"],
            ["52周最高", f"{s['highest52weeks_price']:.2f}"],
            ["52周最低", f"{s['lowest52weeks_price']:.2f}"],
            ["总市值 (百万美元)", format_number(s["total_market_val"], divider=1e6)],
        ]
        return (format_header(2, "2. 市场行情") + "\n\n" +
                format_table(["指标", "数值"], rows, alignments=["l", "r"]))

    def _get_dividends_us(self, ts_code: str) -> str:
        data = _checked(self.quote_context.get_corporate_actions_dividends(
            moomoo_code(ts_code)), "get_corporate_actions_dividends")
        if not isinstance(data, dict):
            raise RuntimeError("moomoo 分红响应格式错误")
        totals = {}
        for item in data.get("dividend_list", []):
            match = DIVIDEND_RE.search(str(item.get("statement", "")))
            ex_date = str(item.get("ex_date", ""))
            if not match or not re.fullmatch(r"\d{4}/\d{2}/\d{2}", ex_date):
                raise RuntimeError("moomoo 分红说明无法解析为每股美元金额或除息日期")
            year = int(ex_date[:4])
            totals[year] = totals.get(year, 0.0) + float(match.group(1))
        current_year = self._as_of.year
        annual = sorted(((year, amount) for year, amount in totals.items()
                         if year < current_year), reverse=True)[:5]
        if annual:
            self._store["dividends"] = pd.DataFrame([
                {"end_date": f"{year}1231", "cash_div_tax": amount,
                 "base_share": 1, "div_proc": "实施"}
                for year, amount in annual
            ])
        self._fetched.add("dividends")
        lines = [format_header(2, "6. 分红历史"), ""]
        shown = [[str(y), f"{v:.4f}"] for y, v in annual]
        if current_year in totals:
            shown.insert(0, [f"{current_year} 年内累计", f"{totals[current_year]:.4f}"])
        if shown:
            lines.append(format_table(["年度", "每股股息 (USD)"],
                                      shown,
                                      alignments=["l", "r"]))
        else:
            lines.append("暂无分红数据")
        lines.append("\n*数据来源: moomoo OpenAPI*")
        return "\n".join(lines)

    def get_segments(self, ts_code: str) -> str:
        data = _checked(self.quote_context.get_financials_revenue_breakdown(
            moomoo_code(ts_code)), "get_financials_revenue_breakdown")
        if not isinstance(data, dict) or data.get("currency_code") != "USD":
            raise RuntimeError("moomoo 主营构成缺少 USD 币种")
        rows = []
        for group in data.get("breakdown_list", []):
            if group.get("type") == "BUSINESS":
                for item in group.get("item_list", []):
                    rows.append([item.get("name", ""),
                                 format_number(item.get("main_oper_income"), divider=1e6),
                                 f"{item.get('ratio', 0):.2f}"])
        if not rows:
            return format_header(2, "9. 主营业务构成") + "\n\n数据缺失\n"
        self._fetched.add("segments")
        return (format_header(2, "9. 主营业务构成") + "\n\n" +
                f"*报告期: {data.get('period', '未知')}；币种: USD*\n\n" +
                format_table(["业务名称", "营业收入 (百万美元)", "占比 (%)"], rows,
                             alignments=["l", "r", "r"]))

    def assemble_data_pack(self, ts_code: str) -> str:
        moomoo_code(ts_code)
        pack = super().assemble_data_pack(ts_code)
        required = {"snapshot", "us_income", "us_balancesheet",
                    "us_cashflow", "us_fina_indicator", "weekly", "dividends"}
        missing = required - self._fetched
        if missing:
            raise RuntimeError("moomoo 核心数据不完整: " + ", ".join(sorted(missing)))
        stores = ("basic_info", "income", "balance_sheet", "cashflow",
                  "fina_indicators", "weekly_prices")
        empty = [key for key in stores if not isinstance(self._store.get(key), pd.DataFrame)
                 or self._store[key].empty]
        if empty:
            raise RuntimeError("moomoo 分析输入为空: " + ", ".join(empty))
        return pack
