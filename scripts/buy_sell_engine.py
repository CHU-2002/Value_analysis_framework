#!/usr/bin/env python3
"""Offline, deterministic buy/sell planning. See docs/BUY_SELL_CONTRACT.md."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
import statistics

from market_sessions import CURRENCY_MARKETS, MARKET_ZONES, market_time

VERSION = "1.0"
ALLOCATIONS = (20, 30, 30, 20)
CURRENCIES = {"CNY": "元", "HKD": "港元", "USD": "美元"}
EXIT_CODES = {"financial_fraud", "governance_failure", "insolvency", "core_business_failure"}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError):
        return None


def positive(value):
    result = number(value)
    return result if result is not None and result > 0 else None


def iso_date(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y%m%d" if len(value) == 8 else "%Y-%m-%d").date()
    except ValueError:
        return None


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def load_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def write_json(path, payload):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                    allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def method_cv(methods):
    """Sample CV, matching valuation_engine.cross_validate; one method is unknown."""
    valid, excluded = [], []
    seen = set()
    if not isinstance(methods, list):
        raise ValueError("methods must be a list")
    for row in methods:
        if not isinstance(row, dict):
            raise ValueError("method entries must be objects")
        name = row.get("method")
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError("valuation methods require unique nonempty names")
        seen.add(name)
        value = positive(row.get("intrinsic"))
        if value is None:
            excluded.append(name)
        else:
            valid.append({"method": name, "intrinsic": value})
    values = [row["intrinsic"] for row in valid]
    # Scale before computing variance to avoid overflowing on malformed large inputs.
    scaled = [v / max(values) for v in values] if values else []
    cv = statistics.stdev(scaled) / statistics.mean(scaled) * 100 if len(scaled) > 1 else None
    return cv, valid, excluded


def safety_margin(cv, risk):
    base = 40 if cv is None or cv > 30 else 30 if cv > 15 else 20
    adjustments = []
    if cv is None:
        adjustments.append({"reason": "insufficient_independent_methods", "pct": 10})
    mode = risk.get("valuation_mode")
    if mode == "Defensive":
        adjustments.append({"reason": "valuation_mode=Defensive", "pct": 10})
    elif mode == "Conservative":
        adjustments.append({"reason": "valuation_mode=Conservative", "pct": 5})
    if risk.get("missing_methods"):
        adjustments.append({"reason": "missing_or_invalid_methods", "pct": 5})
    if risk.get("bank_health_missing") is True:
        adjustments.append({"reason": "bank_health_missing", "pct": 5})
    if risk.get("report_notes_missing") is True:
        adjustments.append({"reason": "report_notes_missing", "pct": 5})
    return {"cv_default_pct": base, "adjustments": adjustments,
            "unclamped_pct": base + sum(a["pct"] for a in adjustments),
            "pct": min(50, max(20, base + sum(a["pct"] for a in adjustments)))}


def _price(value, rounding):
    try:
        result = float(value.quantize(Decimal("0.01"), rounding=rounding))
    except (InvalidOperation, OverflowError):
        return None
    return positive(result)


def make_basis(snapshot):
    if snapshot.get("schema") != "investment.value_snapshot" or snapshot.get("schema_version") != VERSION:
        raise ValueError("unsupported value snapshot schema/version")
    subject = snapshot.get("subject", {})
    if not isinstance(subject, dict) or not subject.get("ticker") or subject.get("currency") not in CURRENCIES:
        raise ValueError("snapshot requires ticker and CNY/HKD/USD currency")
    if not isinstance(snapshot.get("values", {}), dict) or not isinstance(snapshot.get("risk", {}), dict):
        raise ValueError("values and risk must be objects")
    cv, methods, excluded = method_cv(snapshot.get("methods", []))
    risk = dict(snapshot.get("risk", {}))
    risk["missing_methods"] = sorted(set(risk.get("missing_methods", []) + excluded))
    margin = safety_margin(cv, risk)
    values = {key: positive(snapshot.get("values", {}).get(key))
              for key in ("V_bear", "V_base", "V_bull")}
    errors = []
    period = iso_date(snapshot.get("financial_period"))
    valuation_date = iso_date(snapshot.get("as_of"))
    if period is None:
        errors.append("missing_financial_period")
    if valuation_date is None or (period and period > valuation_date):
        errors.append("invalid_valuation_date")
    if snapshot.get("cycle") is not None and (not isinstance(snapshot["cycle"], str) or not snapshot["cycle"].strip()):
        raise ValueError("cycle must be a nonempty review ID/reason")
    if any(v is None for v in values.values()):
        errors.append("missing_or_nonpositive_valuation")
    elif not values["V_bear"] <= values["V_base"] <= values["V_bull"]:
        errors.append("unordered_valuation_scenarios")
    tiers, exit_price, exit_components = [], None, None
    if not errors:
        base = Decimal(str(values["V_base"]))
        bull = Decimal(str(values["V_bull"]))
        cumulative = 0
        for i, allocation in enumerate(ALLOCATIONS):
            discount = margin["pct"] + i * 10
            raw = base * (1 - Decimal(discount) / 100)
            cumulative += allocation
            tiers.append({"tier": i + 1, "price": _price(raw, ROUND_FLOOR),
                          "raw_price": str(raw), "discount_pct": discount,
                          "allocation_pct": allocation, "cumulative_pct": cumulative,
                          "formula": f"V_base * (1 - M - {i} * 0.10)"})
        components = (base * 2, bull * Decimal("1.2"))
        exit_components = [str(v) for v in components]
        exit_price = _price(max(components), ROUND_CEILING)
        prices = [t["price"] for t in tiers]
        if any(p is None for p in prices) or any(a <= b for a, b in zip(prices, prices[1:])):
            errors.append("buy_prices_not_distinct_at_cent_precision")
        if exit_price is None or exit_price <= values["V_base"] or (prices[0] and exit_price <= prices[0]):
            errors.append("invalid_exit_price")
    if errors:
        tiers, exit_price = [], None
    basis = {"schema": "investment.buy_sell_basis", "schema_version": VERSION,
             "subject": subject, "financial_period": snapshot.get("financial_period"),
             "valuation_as_of": snapshot.get("as_of"), "cycle": snapshot.get("cycle"),
             "source_digest": digest(snapshot), "source_snapshot": deepcopy(snapshot),
             "value_sources": snapshot.get("value_sources", {}),
             "values": values, "methods": methods, "excluded_methods": excluded,
             "cv_pct": cv, "cv_formula": "sample_stdev(method_intrinsics) / mean * 100",
             "margin": margin, "risk": risk, "tiers": tiers,
             "exit_price": exit_price, "exit_components_raw": exit_components,
             "exit_formula": "max(2 * V_base, 1.2 * V_bull)",
             "errors": errors}
    basis["basis_id"] = digest(basis)
    return basis


def select_basis(snapshot, previous=None):
    """Market-only reruns never move the stored valuation or margin."""
    candidate = make_basis(snapshot)
    if previous is None:
        return candidate
    content = {k: v for k, v in previous.items() if k != "basis_id"}
    if (previous.get("schema") != "investment.buy_sell_basis" or previous.get("schema_version") != VERSION
            or previous.get("basis_id") != digest(content)):
        raise ValueError("invalid or modified buy_sell_basis.json")
    if previous["subject"] != candidate["subject"]:
        raise ValueError("basis subject/currency mismatch")
    old_period = iso_date(previous.get("financial_period"))
    new_period = iso_date(candidate.get("financial_period"))
    if old_period and (new_period is None or new_period < old_period):
        raise ValueError("financial period missing or older than frozen basis")
    if old_period == new_period and previous.get("cycle") == candidate.get("cycle"):
        return previous
    return candidate


def _match_subject(payload, subject, label):
    other = payload.get("subject", {})
    if not isinstance(other, dict) or any(other.get(k) != subject.get(k) for k in ("ticker", "currency")):
        raise ValueError(f"{label}: ticker/currency mismatch")


def _confirmed_exit(basis, market, as_of):
    threshold = basis.get("exit_price")
    quote_date = iso_date(market.get("quote_date"))
    if threshold is None or quote_date is None:
        return None
    for frequency, max_age, count in (("daily", 7, 2), ("weekly", 10, 1)):
        rows = market.get(f"{frequency}_closes", [])
        dated = []
        dates = set()
        for row in rows:
            day = iso_date(row.get("date"))
            if day is None or day in dates:
                return None
            dates.add(day)
            if day <= as_of and (frequency != "daily" or row.get("complete") is True):
                dated.append((day, row))
        dated.sort(key=lambda item: item[0], reverse=True)
        latest = dated[:count]
        if len(latest) < count:
            continue
        if (as_of - latest[0][0]).days > max_age or latest[0][0] > quote_date:
            continue
        if frequency == "daily" and (latest[0][0] - latest[1][0]).days > 7:
            continue
        if all(row.get("complete") is True and row.get("price_basis") == "unadjusted"
               and positive(row.get("close")) is not None and row["close"] >= threshold
               for _, row in latest):
            if frequency == "daily" and latest[0][1].get("previous_session") != latest[1][0].isoformat():
                continue
            return {"frequency": frequency, "closes": [row for _, row in latest]}
    return None


def build_plan(basis, market, *, as_of, state=None, qualitative=None, risk_events=None):
    today = iso_date(as_of)
    if today is None:
        raise ValueError("as_of must be an ISO date")
    _match_subject(market, basis["subject"], "market")
    warnings, blockers, hard_exits = list(market.get("warnings", [])), list(basis["errors"]), []
    valuation_date = iso_date(basis.get("valuation_as_of"))
    if valuation_date and valuation_date > today:
        blockers.append("valuation_from_future")
    if state is None:
        state = {"committed_pct": 0, "session_spent_pct": 0, "session_date": None, "exited": False}
        warnings.append("no_execution_state: first allocation proposal; reruns are not additional orders")
        state_assumed = True
    else:
        _match_subject(state, basis["subject"], "execution state")
        state_assumed = False
    committed, spent = number(state.get("committed_pct")), number(state.get("session_spent_pct"))
    session_date = iso_date(state.get("session_date"))
    if (committed is None or spent is None or not 0 <= committed <= 100 or
            not 0 <= spent <= min(30, committed) or not isinstance(state.get("exited"), bool) or
            (state.get("session_date") is not None and session_date is None) or
            (spent > 0 and session_date is None) or (session_date and session_date > today)):
        raise ValueError("invalid execution state percentages/date/exited")
    today_spent = spent if session_date == today else 0
    position_status = "unknown" if state_assumed else "flat" if committed == 0 or state["exited"] else "long"
    if qualitative is not None:
        if qualitative.get("subject", {}).get("ticker") != basis["subject"]["ticker"]:
            raise ValueError("qualitative ticker mismatch")
        if qualitative.get("source") == "structured":
            if qualitative.get("parameters", {}).get("integrity_rating") == "不可靠":
                hard_exits.append({"code": "governance_failure", "source": "qualitative_input.parameters.integrity_rating",
                                   "evidence": qualitative.get("modules", {}).get("governance", {})})
        else:
            warnings.append("qualitative_hard_exits_not_structured")
            if qualitative.get("source") == "unavailable":
                blockers.append("qualitative_unavailable")
    else:
        warnings.append("qualitative_hard_exits_not_checked")
    if risk_events is not None:
        _match_subject(risk_events, basis["subject"], "risk events")
        for event in risk_events.get("events", []):
            observed = iso_date(event.get("observed_at"))
            if (event.get("code") not in EXIT_CODES or not isinstance(event.get("confirmed"), bool)
                    or not isinstance(event.get("evidence"), list) or not event["evidence"]
                    or not all(isinstance(e, str) and e.strip() for e in event["evidence"])
                    or observed is None or observed > today):
                raise ValueError("invalid hard exit event (code/confirmation/evidence/date)")
            if event["confirmed"]:
                hard_exits.append(event)
    close = positive(market.get("close"))
    quote_date = iso_date(market.get("quote_date"))
    if close is None:
        blockers.append("missing_or_nonpositive_market_price")
    if quote_date is None or quote_date > today or (today - quote_date).days > 7:
        blockers.append("missing_stale_or_future_quote_date")
    if market.get("price_basis") != "unadjusted":
        blockers.append("incompatible_market_price_basis")
    tiers = basis["tiers"]
    tier = max((t["tier"] for t in tiers if close is not None and close <= t["price"]), default=0)
    target = tiers[tier - 1]["cumulative_pct"] if tier else 0
    buy_pct = min(max(0, target - committed), 30 - today_spent)
    confirmation = _confirmed_exit(basis, market, today) if not blockers else None
    exit_reached = close is not None and basis["exit_price"] is not None and close >= basis["exit_price"]
    if hard_exits:
        action, reason = "SELL_ALL", "hard_exit"
    elif state["exited"]:
        action, reason = "DO_NOT_BUY", "position_already_exited"
    elif blockers:
        action, reason = "BLOCKED", "; ".join(blockers)
    elif exit_reached and confirmation:
        action, reason = "SELL_ALL", "extreme_overvaluation_confirmed"
    elif exit_reached:
        action, reason = "HOLD", "exit_threshold_reached_await_close_confirmation"
    elif buy_pct > 0:
        action, reason = "BUY", "eligible_buy_tier"
    else:
        action, reason = "HOLD", "session_cap_reached" if today_spent >= 30 and target > committed else "no_unfunded_eligible_tier"
    if action == "SELL_ALL" and position_status == "flat":
        action, reason = "DO_NOT_BUY", reason + "_no_position"
    if action == "SELL_ALL" and position_status == "unknown":
        warnings.append("sell_requires_execution_state: position unknown; do not open a short position")
    buy_pct = buy_pct if action == "BUY" else 0
    execute = action == "BUY" or (action == "SELL_ALL" and position_status == "long")
    after = 0 if action == "SELL_ALL" and execute else committed + buy_pct
    next_tier = next((t for t in tiers if t["cumulative_pct"] > after), None)
    if action in {"SELL_ALL", "DO_NOT_BUY", "BLOCKED"}:
        next_tier = None
    execution = {"action": action, "reason": reason, "execute": execute,
                 "position_status": position_status,
                 "session_date": today.isoformat(),
                 "session_timezone": MARKET_ZONES[CURRENCY_MARKETS[basis["subject"]["currency"]]],
                 "current_tier": tier if tiers and close is not None else None,
                 "target_cumulative_pct": target, "committed_pct": committed,
                  "buy_now_pct": buy_pct, "sell_position_pct": 100 if action == "SELL_ALL" else 0,
                 "buy_limit_price": tiers[tier - 1]["price"] if action == "BUY" else None,
                 "cumulative_after_fill_pct": after, "session_spent_pct": today_spent,
                 "pending_eligible_pct": max(0, target - after) if action in {"BUY", "HOLD"} else 0,
                 "next_tier": next_tier["tier"] if next_tier else None,
                 "next_price": next_tier["price"] if next_tier else None,
                 "next_execution_date": (today + timedelta(days=1)).isoformat() if after < target and action in {"BUY", "HOLD"} else None,
                 "state_assumed": state_assumed}
    plan = {"schema": "investment.buy_sell_plan", "schema_version": VERSION,
            "subject": basis["subject"], "as_of": as_of, "basis": deepcopy(basis),
            "market": {"close": close, "quote_date": market.get("quote_date"), "source": market.get("source")},
            "market_digest": digest(market), "execution": execution,
            "exit_confirmation": confirmation, "hard_exits": hard_exits,
            "warnings": warnings, "blockers": blockers}
    plan["instruction_id"] = digest(plan)
    return plan


def render_markdown(plan):
    basis, execution = plan["basis"], plan["execution"]
    unit = CURRENCIES[plan["subject"]["currency"]]
    def price(value):
        return "N/A" if value is None else f"{value:.2f} {unit}/股"
    actions = {"BUY": "执行买入", "SELL_ALL": "卖出全部持仓", "HOLD": "持有，当前不加仓",
               "DO_NOT_BUY": "禁止买入，无持仓可卖或已退出", "BLOCKED": "暂停交易，输入数据不足或无效"}
    if execution["action"] == "SELL_ALL" and not execution["execute"]:
        actions["SELL_ALL"] = "退出条件成立，待核实持仓后卖出；禁止开空仓"
    lines = ["## 买卖计划", "", f"**当前指令：{actions[execution['action']]}**",
             f"- 执行交易日：{execution['session_date']}（{execution['session_timezone']}）；持仓状态：{execution['position_status']}",
             f"- 当前价格：{price(plan['market']['close'])}；行情日期：{plan['market']['quote_date'] or 'N/A'}",
             f"- 当前档位：{execution['current_tier'] if execution['current_tier'] is not None else 'N/A'}（0 为高于 P1）；是否执行：{'是' if execution['execute'] else '否'}",
              f"- 本次买入：计划总资金的 {execution['buy_now_pct']:g}%；卖出：现有持仓的 {execution['sell_position_pct']:g}%",
             f"- 本次买入限价：{price(execution['buy_limit_price'])}",
             f"- 已投入：{execution['committed_pct']:g}%；本次成交后累计：{execution['cumulative_after_fill_pct']:g}%；当前档位目标累计：{execution['target_cumulative_pct']:g}%",
             f"- 下一档：{execution['next_tier'] or 'N/A'}；价格：{price(execution['next_price'])}；已触及待分日资金：{execution['pending_eligible_pct']:g}%",
             f"- 原因：{execution['reason']}", "",
             f"价值基准日：{basis['valuation_as_of'] or 'N/A'}；财报期：{basis['financial_period'] or 'N/A'}；周期：{basis['cycle'] or 'initial'}",
             f"V_bear = {price(basis['values']['V_bear'])}；V_base = {price(basis['values']['V_base'])}；V_bull = {price(basis['values']['V_bull'])}",
             f"方法分歧 CV：{'N/A' if basis['cv_pct'] is None else format(basis['cv_pct'], '.2f') + '%'}；计划要求安全边际 M：{basis['margin']['pct']}%",
             "", "| 档位 | 买入限价 | 资金比例 | 累计比例 | 计算式 |",
             "|------|----------|----------|----------|--------|"]
    for tier in basis["tiers"]:
        lines.append(f"| P{tier['tier']} | {price(tier['price'])} | {tier['allocation_pct']}% | {tier['cumulative_pct']}% | {tier['formula']} |")
    lines += ["", f"**极端高估卖出价：{price(basis['exit_price'])}**",
              "P_exit = max(2 * V_base, 1.2 * V_bull)。当前价格仍达标且连续两个交易日收盘或最近完整周线收盘达标后，卖出全部持仓。",
              f"价格确认：{plan['exit_confirmation']['frequency'] if plan['exit_confirmation'] else '尚未确认'}；硬退出信号数：{len(plan['hard_exits'])}。",
              "单个交易日累计买入最多使用计划资金的 30%；跳档剩余资金在后续交易日、价格仍符合对应档位时分次执行。",
              "资金比例基于该公司的独立计划总预算；成交后更新执行状态，再生成下一条指令。未提供状态时按首次建仓生成，同次重跑不代表追加订单。",
              "价格单位沿用估值币种；买价向下、卖价向上取到两位小数。实际交易遵守交易所报价单位。",
              "", "### 计算依据", ""]
    for row in basis["methods"]:
        lines.append(f"- {row['method']}：{price(row['intrinsic'])}")
    lines.append(f"- CV 基础安全边际：{basis['margin']['cv_default_pct']}%；调整：{json.dumps(basis['margin']['adjustments'], ensure_ascii=False)}；截断范围：20%-50%")
    lines.append(f"- 价值来源：{json.dumps(basis['value_sources'], ensure_ascii=False)}")
    lines.append(f"- 卖出式两项未舍入值：{basis['exit_components_raw']}")
    for event in plan["hard_exits"]:
        lines.append(f"- 硬退出：{json.dumps(event, ensure_ascii=False)}")
    for message in plan["blockers"] + plan["warnings"]:
        lines.append(f"- {message}")
    lines += [f"- 基准 ID：`{basis['basis_id']}`", f"- 指令 ID：`{plan['instruction_id']}`", ""]
    return "\n".join(lines)


def run_directory(output_dir, *, as_of=None):
    root = Path(output_dir)
    snapshot = load_json(root / "value_computed.json")
    market = load_json(root / "buy_sell_market.json")
    if as_of is None:
        as_of = market_time(CURRENCY_MARKETS[snapshot["subject"]["currency"]]).date().isoformat()
    def optional(name):
        path = root / name
        return load_json(path) if path.exists() else None
    basis = select_basis(snapshot, optional("buy_sell_basis.json"))
    plan = build_plan(basis, market, as_of=as_of, state=optional("buy_sell_state.json"),
                      qualitative=optional("qualitative_input.json"), risk_events=optional("buy_sell_risk.json"))
    write_json(root / "buy_sell_basis.json", basis)
    write_json(root / "buy_sell_plan.json", plan)
    (root / "buy_sell_plan.md").write_text(render_markdown(plan), encoding="utf-8")
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--as-of", help="Exchange-local session date YYYY-MM-DD for offline replay; defaults to the current exchange-local date")
    args = parser.parse_args(argv)
    try:
        plan = run_directory(args.output_dir, as_of=args.as_of)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(2, f"buy_sell_engine: {exc}\n")
    print(f"buy_sell_plan.json + buy_sell_plan.md: {plan['execution']['action']}")
    return 3 if plan["execution"]["action"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
