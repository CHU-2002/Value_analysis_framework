"""REQ-015.1: offline, point-in-time daily ranking and natural-period OHLC.

Reads SQLite with mode=ro: browsing never creates a warehouse or calls a remote API.
Values are source observations, not prices divided by the latest financial report.
"""
from __future__ import annotations

import bisect
import calendar
import json
import math
import re
import sqlite3
from collections import deque
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

from ..core.errors import BadRequest

CYCLES = ("day", "week", "month", "quarter", "year")
WINDOWS = ("3", "5", "10", "all")
ADJUSTMENTS = ("none", "forward", "backward")


def number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def date_value(value):
    text = str(value or "").replace("-", "")[:8]
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except (ValueError, TypeError):
        return None


def years_before(day, years):
    return day.replace(year=day.year - years, day=min(day.day, calendar.monthrange(day.year - years, day.month)[1]))


def read_records(root, ticker, dataset):
    """Keep source versions; overlapping daily observations prefer latest fetched record."""
    path = Path(root).expanduser() / "store.db"
    if not path.is_file():
        return [], []
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        records = connection.execute(
            "SELECT rows_json,fetched_at,content_sha256,result,params_json FROM raw_record "
            "WHERE ticker=? AND dataset=? ORDER BY fetched_at, id", (ticker, dataset),
        ).fetchall()
    rows, sources = [], []
    for record in records:
        source = {"dataset": dataset, "fetched_at": record["fetched_at"],
                  "version": record["content_sha256"], "result": record["result"]}
        sources.append(source)
        if record["result"] == "ok":
            for row in json.loads(record["rows_json"]):
                if row.get("ts_code") and str(row["ts_code"]) != ticker:
                    continue
                rows.append({**row, "source": source})
    return rows, sources


def daily_rows(prices, valuations, factors):
    def keyed(rows):
        return {date_value(r.get("trade_date")): r for r in rows if date_value(r.get("trade_date"))}
    prices, valuations, factors = keyed(prices), keyed(valuations), keyed(factors)
    rows = []
    for day in sorted(prices.keys() | valuations.keys()):
        price, val = prices.get(day, {}), valuations.get(day, {})
        rows.append({"date": day.isoformat(), "price_available": bool(price),
                     **{key: number(price.get(key)) for key in ("open", "high", "low", "close", "vol")},
                     "pe_ttm": number(val.get("pe_ttm")), "pb": number(val.get("pb")),
                     "adj_factor": number(factors.get(day, {}).get("adj_factor")),
                     "source": {"price": price.get("source"), "valuation": val.get("source"),
                                "adjustment": factors.get(day, {}).get("source")}})
    return rows


def rank_daily(rows, window="5"):
    if window not in WINDOWS:
        raise BadRequest("无效分位统计窗口")
    result = [dict(row) for row in rows]
    for metric in ("pe_ttm", "pb"):
        ordered, history = [], deque()
        for row in result:
            day = date_value(row["date"])
            boundary = years_before(day, int(window)) if window != "all" else date.min
            while history and history[0][0] < boundary:
                _, old = history.popleft()
                ordered.pop(bisect.bisect_left(ordered, old))
            value = row[metric]
            valid = value is not None and value > 0
            if valid:
                bisect.insort(ordered, value)
                history.append((day, value))
            row[metric + "_rank"] = 100 * bisect.bisect_left(ordered, value) / len(ordered) if valid else None
            row[metric + "_rank_info"] = {
                "samples": len(ordered), "start": history[0][0].isoformat() if history else None,
                "end": day.isoformat(), "incomplete": window != "all" and (not rows or date_value(rows[0]["date"]) > boundary),
                "reason": "缺少当日指标" if value is None else ("非正值不参加排名" if not valid else ""),
            }
    return result


def period_bounds(day, cycle):
    if cycle == "day":
        return day, day
    if cycle == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    month = (day.month - 1) // 3 * 3 + 1 if cycle == "quarter" else (1 if cycle == "year" else day.month)
    end_month = month + (2 if cycle == "quarter" else (11 if cycle == "year" else 0))
    return date(day.year, month, 1), date(day.year, end_month, calendar.monthrange(day.year, end_month)[1])


def aggregate(rows, cycle="day", adjustment="none", as_of=None):
    if cycle not in CYCLES or adjustment not in ADJUSTMENTS:
        raise BadRequest("无效交易周期或复权选项")
    effective = [row.get("adj_factor") for row in rows]
    can_adjust = bool(rows) and all(value is not None and value > 0 for value in effective)
    if adjustment != "none" and not can_adjust:
        raise BadRequest("复权依据不完整；请更新复权因子或选择不复权")
    anchor = (effective[-1] if adjustment == "forward" else effective[0]) if adjustment != "none" else 1
    groups = {}
    for original in rows:
        row = dict(original)
        if adjustment != "none":
            ratio = row["adj_factor"] / anchor
            for key in ("open", "high", "low", "close"):
                row[key] = row[key] * ratio if row[key] is not None else None
        start, end = period_bounds(date_value(row["date"]), cycle)
        groups.setdefault((start, end), []).append(row)
    points = []
    today = as_of or date.today()
    for (start, end), items in groups.items():
        complete_ohlc = all(all(item[k] is not None for k in ("open", "high", "low", "close")) for item in items)
        last = items[-1]
        point = {**last, "start": items[0]["date"], "end": last["date"],
                 "period_start": start.isoformat(), "period_end": end.isoformat(),
                 "ongoing": end >= today, "trading_days": len(items),
                 "open": items[0]["open"] if complete_ohlc else None,
                 "high": max(item["high"] for item in items) if complete_ohlc else None,
                 "low": min(item["low"] for item in items) if complete_ohlc else None,
                 "close": last["close"] if complete_ohlc else None,
                 "vol": sum(item["vol"] for item in items) if all(item["vol"] is not None for item in items) else None,
                 "ohlc_reason": "" if complete_ohlc else "缺少开/高/低/收字段，不能生成该期间 K 线"}
        points.append(point)
    for index, point in enumerate(points):
        for length in (5, 10, 20, 60):
            sample = points[max(0, index - length + 1):index + 1]
            point[f"ma{length}"] = sum(p["close"] for p in sample) / length if len(sample) == length and all(p["close"] is not None for p in sample) else None
    return points, can_adjust


def timeline_payload(rows, *, company, cycle="day", window="5", adjustment="none", range="1", start="", end="", sources=None, as_of=None, calendar_rows=None, suspension_rows=None):
    observed = rows
    rows = [dict(row) for row in rows]
    suspended = {date_value(r.get("trade_date")) for r in suspension_rows or [] if r.get("suspend_type", "S") == "S"}
    price_days = {row["date"] for row in rows if row.get("price_available", any(row.get(k) is not None for k in ("open", "high", "low", "close")))}
    if rows and calendar_rows:
        # Calendar evidence can establish a missing final trading day. Keep
        # valuation-only days and never borrow the preceding day's estimate.
        known = {row["date"] for row in rows}
        first = date_value(rows[0]["date"])
        final = date_value(end) if range == "custom" else period_bounds(date_value(rows[-1]["date"]), cycle)[1]
        final = min(final or date_value(rows[-1]["date"]), as_of or date.today())
        for calendar_day in calendar_rows:
            day = date_value(calendar_day.get("cal_date"))
            if day and first <= day <= final and number(calendar_day.get("is_open")) == 1 and day not in suspended and day.isoformat() not in known:
                rows.append({"date": day.isoformat(), "price_available": False,
                             **{key: None for key in ("open", "high", "low", "close", "vol", "pe_ttm", "pb", "adj_factor")},
                             "source": {"calendar": calendar_day.get("source")}})
                known.add(day.isoformat())
        rows.sort(key=lambda row: row["date"])
    ranked = rank_daily(rows, window)
    points, can_adjust = aggregate(ranked, cycle, adjustment, as_of)
    last = date_value(rows[-1]["date"]) if rows else (as_of or date.today())
    if range not in ("1", "3", "5", "all", "custom"):
        raise BadRequest("无效显示范围")
    lower = years_before(last, int(range)) if range in ("1", "3", "5") else date.min
    upper = last
    if range == "custom":
        lower, upper = date_value(start), date_value(end)
        if not lower or not upper or lower > upper:
            raise BadRequest("请提供有效自定义起止日期")
    # Periods are built on full history before clipping: range never alters OHLC/ranking/MA.
    visible = [p for p in points if lower <= date_value(p["end"]) <= upper]
    gaps = []
    for row in calendar_rows or []:
        day = date_value(row.get("cal_date"))
        if day and lower <= day <= upper and number(row.get("is_open")) == 1 and day.isoformat() not in price_days:
            gaps.append({"date": day.isoformat(), "reason": "停牌" if day in suspended else "缺少行情（未确认停牌）"})
    requested_start = lower if lower != date.min else (date_value(rows[0]["date"]) if rows else years_before(last, 10))
    acquisition_start = requested_start
    if window != "all":
        first_visible = date_value(visible[0]["start"]) if visible else requested_start
        acquisition_start = min(requested_start, years_before(first_visible, int(window)))
    return {"kind": "research_timeline", "company": company, "points": visible,
            "settings": {"cycle": cycle, "window": window, "adjustment": adjustment, "range": range,
                         "start": requested_start.isoformat(), "end": upper.isoformat()},
            "can_adjust": can_adjust, "sources": sources or [], "gaps": gaps,
            "coverage": {"start": rows[0]["date"] if rows else None, "end": rows[-1]["date"] if rows else None,
                         "daily_samples": sum(row.get("price_available", any(row.get(k) is not None for k in ("open", "high", "low", "close"))) for row in observed), "requested_start": requested_start.isoformat(),
                         "incomplete": not rows or date_value(rows[0]["date"]) > requested_start,
                         "calendar_available": bool(calendar_rows)},
            "update_href": "#data?" + urlencode({"company": company, "chart_start": acquisition_start.strftime("%Y%m%d"),
                  "chart_end": upper.strftime("%Y%m%d"), "chart_metrics": "daily,daily_basic,adj_factor,trade_cal,suspend_d"}),
            "source_note": "Tushare 原始日行情/每日指标/复权因子；事后修订以采集时间与 sha256 版本区分，不宣称还原当时已知财务信息。年末估算 PE 保留在旧数据包中，不能替代真实每日 PE。",
            "empty_hint": "尚未采集 OHLC 日行情。年度最高/最低汇总不能生成 K 线。" if not rows else ("此范围无交易数据" if not visible else "")}


def _canonical_company(ctx, company):
    from datalayer.universe import market_of, normalize_ticker
    from datalayer.errors import UniverseError
    try:
        ticker = normalize_ticker(company)
        market_of(ticker)
        return ticker
    except UniverseError:
        from .companies import resolve_company
        resolved = resolve_company(ctx, company)
        try:
            ticker = normalize_ticker(resolved["ticker"])
            market_of(ticker)
            return ticker
        except UniverseError as exc:
            raise BadRequest("请先选择有股票代码的公司") from exc


def _snapshot_payload(sources):
    if len(sources) != 1:
        raise BadRequest("行情源快照必须是一份完整版本")
    return json.loads(Path(sources[0]).read_text(encoding="utf-8"))


def parse_timeline_snapshot(sources, params):
    raw = _snapshot_payload(sources)
    datasets = raw["datasets"]
    source_versions = [source for entry in datasets.values() for source in entry["sources"]]
    global_calendar = raw["global_datasets"].get("trade_cal", {"rows": [], "sources": []})
    source_versions.extend(global_calendar["sources"])
    company = params["company"]
    exchange = "SSE" if company.endswith(".SH") else "SZSE"
    calendar_rows = [row for row in global_calendar["rows"]
                     if not row.get("exchange") or row["exchange"] == exchange]
    if not calendar_rows:
        calendar_rows = datasets["trade_cal"]["rows"]
    settings = {key: params[key] for key in ("cycle", "window", "adjustment", "range", "start", "end")}
    settings["as_of"] = date_value(params["as_of"])
    data = timeline_payload(daily_rows(datasets["daily"]["rows"], datasets["daily_basic"]["rows"],
                            datasets["adj_factor"]["rows"]), company=company, sources=source_versions,
                            calendar_rows=calendar_rows, suspension_rows=datasets["suspend_d"]["rows"], **settings)
    if not company.endswith((".SH", ".SZ")):
        data["update_href"] = "#data?" + urlencode({"company": company})
        data["source_note"] = "当前市场的历史 OHLC/估值补齐计划尚不可用；仅显示已有本地观察，不自动联网。"
    return data


def parse_income_snapshot(sources, params):
    raw = _snapshot_payload(sources)
    income = raw["datasets"]["income"]
    return {**financial_income(income["rows"]), "available": bool(income["rows"]),
            "sources": income["sources"], "company": params["company"]}


RESEARCH_DATASETS = ("charts.research_timeline", "charts.raw_income")


def register_research_datasets(registry):
    from ..core.models import DatasetSpec
    from ..datastore import parsers
    for name, parser in zip(RESEARCH_DATASETS, (parse_timeline_snapshot, parse_income_snapshot)):
        parsers.register_parser(name, parser, replace=True)
        registry.dataset(DatasetSpec(name=name, sources=("raw.json",), parser=name, parser_version=2 if name == "charts.research_timeline" else 1))


def cached_timeline(ctx, company, **settings):
    from .raw_snapshots import ensure_raw_snapshot
    company = _canonical_company(ctx, company)
    params = {"cycle": "day", "window": "5", "adjustment": "none", "range": "1", "start": "", "end": ""}
    params.update(settings)
    day = params.get("as_of") or date.today()
    day = day if isinstance(day, date) else date_value(day)
    if day is None:
        raise BadRequest("无效数据观察日期")
    params.update(company=company, as_of=day.isoformat())
    base, revision, reused = ensure_raw_snapshot(ctx.config, company,
        ("daily", "daily_basic", "adj_factor", "suspend_d", "trade_cal"))
    params["raw_revision"] = revision
    data, meta = ctx.registry.datastore.get("charts.research_timeline", base=base, params=params)
    return data, {**meta, "raw_revision": revision, "source_snapshot_cached": reused}


def load_timeline(ctx, company, **settings):
    data, meta = cached_timeline(ctx, company, **settings)
    return {**data, "meta": meta}


def cached_financial_income(ctx, company):
    from .raw_snapshots import ensure_raw_snapshot
    company = _canonical_company(ctx, company)
    base, revision, reused = ensure_raw_snapshot(ctx.config, company, ("income",))
    data, meta = ctx.registry.datastore.get("charts.raw_income", base=base,
        params={"company": company, "raw_revision": revision})
    return data, {**meta, "raw_revision": revision, "source_snapshot_cached": reused}


def financial_income(rows):
    """Raw cumulative income -> annual, half-year and actual single-quarter values."""
    by_end = {}
    for row in rows:
        day = date_value(row.get("end_date"))
        if day and str(row.get("report_type", "1")) == "1":
            by_end[day] = row
    labels, series, reasons = {}, [], {}
    for basis in ("annual", "half", "quarter"):
        days = [day for day in sorted(by_end) if (basis == "quarter" or day.month == (12 if basis == "annual" else 6))]
        labels[basis] = [str(day.year) if basis == "annual" else f"{day.year}H1" if basis == "half" else f"{day.year}Q{(day.month - 1)//3+1}" for day in days]
        for field, title in (("revenue", "营业收入"), ("n_income_attr_p", "归母净利润")):
            values = []
            for day in days:
                value = number(by_end[day].get(field))
                if basis == "quarter" and day.month != 3:
                    prior_month = day.month - 3
                    prior_day = date(day.year, prior_month, calendar.monthrange(day.year, prior_month)[1])
                    prior = number(by_end.get(prior_day, {}).get(field))
                    if value is None or prior is None:
                        reasons[f"{day.isoformat()}:{field}"] = "缺少本期或上一季度累计值，不能换算单季"
                        value = None
                    else:
                        value -= prior
                values.append(value / 1_000_000 if value is not None else None)
            series.append({"name": title, "basis": basis, "cumulative": basis != "quarter", "values": values})
    return {"labels": labels["annual"], "series": series, "unit": "百万元", "basis": "annual",
            "bases": [key for key, value in labels.items() if value], "labels_by_basis": labels,
            "series_by_basis": {basis: [i for i, item in enumerate(series) if item["basis"] == basis] for basis in labels},
            "basis_labels": {"annual": "年度", "half": "半年", "quarter": "单季"},
            "basis_kind": {"annual": "累计（全年）", "half": "累计（半年）", "quarter": "单期（累计值相减）"},
            "missing_reasons": reasons, "source_note": "原始合并利润表；单季=本期累计−上季度累计（Q1直接使用）。缺少依据不造值。"}
