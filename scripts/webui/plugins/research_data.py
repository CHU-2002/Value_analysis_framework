"""REQ-015.4: immutable collection plans and persisted batch explanations."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import Counter
from datetime import date, datetime, timezone

from datalayer.access import DataAccess
from datalayer.config import default_periods
from datalayer.pull import PullBatch, _pid_alive, targets_for
from datalayer.registry import DATASETS, profile_names
from datalayer.security import resolve_token
from datalayer.store import DataStore, target_key
from datalayer.universe import Universe, market_of, normalize_ticker

from ..core import envelope
from ..core.errors import BadRequest, BatchNotFound, BatchRunning, NoToken, QuotaConfirmRequired
from ..core.models import PanelSpec, Param
from .companies import resolve_company

HISTORY_FIELDS = {
    "daily": "ts_code,trade_date,open,high,low,close,vol,amount",
    "daily_basic": "ts_code,trade_date,pe_ttm,pb",
    "adj_factor": "ts_code,trade_date,adj_factor",
    "trade_cal": "exchange,cal_date,is_open,pretrade_date",
    "suspend_d": "ts_code,trade_date,suspend_timing,suspend_type",
}
RESULTS = {
    "ok": ("完整", "可离线查看；报告需显式重建/生成"),
    "empty": ("合法空响应", "数据源在此范围无数据；不会补零"),
    "no_permission": ("无权限", "核对账号接口权限后手动补缺口"),
    "rate_limited": ("限频", "等待配额恢复后手动补缺口"),
    "error": ("采集错误", "查看技术摘要并修复原因后手动补缺口"),
    "unattempted": ("未尝试", "确认计划后开始采集"),
    "stale": ("过期/覆盖不足", "旧记录不能覆盖所需窗口/字段；补缺口"),
}
PHASES = {"pending": "排队", "running": "正在采集", "paused": "暂停", "done": "完成",
          "partial": "部分成功", "failed": "中断", "waiting_rate_limit": "等待限频",
          "waiting_retry": "等待重试"}


def business_group(dataset):
    if dataset in HISTORY_FIELDS or dataset.endswith("daily") or dataset == "weekly":
        return "行情与估值"
    if dataset in ("top10_holders", "pledge_stat", "repurchase", "dividend"):
        return "股东治理与分红"
    if dataset == "yc_cb":
        return "利率"
    if any(part in dataset for part in ("income", "balancesheet", "cashflow", "fina_")):
        return "财报"
    return "公司基本信息"


def _company(ctx, value):
    if not value:
        raise BadRequest("请先选择公司，或选择自选股范围")
    try:
        info = resolve_company(ctx, value)
        return info["ticker"], info["display_name"]
    except Exception as exc:
        try:
            ticker = normalize_ticker(value)
            market_of(ticker)
        except Exception:
            raise BadRequest("当前公司无法解析，请重新选择") from exc
        return ticker, ticker


def _dates(start, end):
    try:
        first = datetime.strptime(str(start), "%Y%m%d").date()
        last = datetime.strptime(str(end), "%Y%m%d").date()
    except ValueError as exc:
        raise BadRequest("行情日期需为 YYYYMMDD") from exc
    if first > last or first.year < 1990 or last > date.today():
        raise BadRequest("行情起止日期无效；不能采集未来日期")
    return first, last


def _split_years(start, end):
    first, last = _dates(start, end)
    for year in range(first.year, last.year + 1):
        yield max(first, date(year, 1, 1)).strftime("%Y%m%d"), min(last, date(year, 12, 31)).strftime("%Y%m%d")


def _state(store, target):
    record = store.find(target["ticker"], target["dataset"], target["period"],
                        params=target["params"], include_rows=False)
    family = store.find_family(target["ticker"], target["dataset"], params=target["params"],
                               period=None if target["period"] == "latest" else target["period"])
    replayed = DataAccess._replay(family, target["params"])
    if replayed is not None:
        if record is None:
            params = target["params"]
            if params.get("start_date") or params.get("end_date"):
                family = [item for item in family
                          if (item.get("params") or {}).get("start_date")
                          and (item.get("params") or {}).get("end_date")
                          and (not params.get("start_date")
                               or str(item["params"]["end_date"]) >= str(params["start_date"]))
                          and (not params.get("end_date")
                               or str(item["params"]["start_date"]) <= str(params["end_date"]))]
            record = max(family, key=lambda item: item.get("fetched_at") or "") if family else None
        # 不相交年份的空响应不能决定这个目标的结果；按真正重放后的数据判定。
        result = "empty" if replayed.empty else "ok"
    elif record:
        result = record["result"] if record["result"] not in ("ok", "empty") else "stale"
    else:
        reason = store.gap_reason(target["ticker"], target["dataset"], params=target["params"],
                                  period=None if target["period"] == "latest" else target["period"])
        result = (reason or {}).get("result") or "unattempted"
        if result == "unattempted" and store.records(ticker=target["ticker"], dataset=target["dataset"], limit=1):
            result = "stale"
    return result, record


def make_plan(ctx, inputs=None):
    if inputs is not None and not isinstance(inputs, dict):
        raise BadRequest("采集参数需为对象")
    inputs = dict(inputs if inputs is not None else ctx.query)
    store = DataStore(ctx.config.archive_root)
    scope = inputs.get("scope") or "current"
    if scope not in ("current", "watchlist"):
        raise BadRequest("公司范围只能是当前公司或自选股")
    tier = inputs.get("tier") or "frugal"
    if tier not in profile_names():
        raise BadRequest("数据档位无效")
    periods = str(inputs.get("periods") or ",".join(default_periods())).split(",")
    if len(periods) > 80 or any(not re.fullmatch(r"\d{8}", value) for value in periods):
        raise BadRequest("财报期次需为逗号分隔的 YYYYMMDD，最多 80 期")
    for period in periods:
        try:
            datetime.strptime(period, "%Y%m%d")
        except ValueError as exc:
            raise BadRequest("财报期次日期无效") from exc
    if scope == "current":
        ticker, name = _company(ctx, inputs.get("company"))
        companies = [{"ticker": ticker, "display_name": name, "market": market_of(ticker)}]
    else:
        companies = Universe(store).entries(enabled_only=True)
        if not companies:
            raise BadRequest("自选股尚无启用公司；先在下方维护清单")
    metrics_text = str(inputs.get("chart_metrics") or "")
    metrics = metrics_text.split(",") if metrics_text else []
    if any(metric not in HISTORY_FIELDS for metric in metrics):
        raise BadRequest("行情指标不在采集白名单内")
    if metrics and scope != "current":
        raise BadRequest("历史行情补齐只用于当前公司，请重新选择范围")
    targets = []
    for company in companies:
        if metrics:
            if company["market"] not in ("SH", "SZ"):
                raise BadRequest("这些历史指标当前只支持 A 股；其他市场不可用")
            for start, end in _split_years(inputs.get("chart_start"), inputs.get("chart_end")):
                for metric in metrics:
                    params = {"start_date": start, "end_date": end, "fields": HISTORY_FIELDS[metric]}
                    if metric == "trade_cal":
                        params["exchange"] = "SZSE" if company["market"] == "SZ" else "SSE"
                    else:
                        params["ts_code"] = company["ticker"]
                    targets.append({"ticker": "" if metric == "trade_cal" else company["ticker"],
                                    "company_ticker": company["ticker"], "dataset": metric,
                                    "period": "latest", "params": params})
        else:
            generated = targets_for([company["ticker"]], periods, tier,
                                    markets={company["ticker"]: company["market"]})
            for target in generated:
                target["company_ticker"] = company["ticker"]
            targets.extend(generated)
    targets = list({target_key(target): target for target in targets}.values())
    rows = []
    today_text = date.today().strftime("%Y%m%d")
    future_periods = sorted(period for period in periods if period > today_text)
    for target in targets:
        state, record = _state(store, target)
        label, next_step = RESULTS[state]
        if target["period"] in future_periods:
            state, label, next_step = "unattempted", "期次尚未结束", "选择已结束期次；不期望此期财报已发布"
        rows.append({**target, "group": business_group(target["dataset"]),
                     "label": DATASETS[target["dataset"]].label, "state": state,
                     "state_label": label, "next_step": next_step,
                     "fetched_at": (record or {}).get("fetched_at") or "未知",
                     "error_excerpt": (record or {}).get("error_excerpt") or "",
                     "impact": "行情/估值缺口影响对应曲线、复权与分位覆盖" if business_group(target["dataset"]) == "行情与估值"
                     else "此组缺口可能影响数据包与报告证据；不自动更新报告"})
    counts = Counter(row["state"] for row in rows)
    complete = counts["ok"] + counts["empty"]
    remaining = len(targets) - complete
    normalized = {"scope": scope, "company": inputs.get("company") or "", "tier": tier,
                  "periods": ",".join(periods), "chart_metrics": metrics_text,
                  "chart_start": inputs.get("chart_start") or "", "chart_end": inputs.get("chart_end") or ""}
    digest = hashlib.sha256(json.dumps({"targets": targets, "inputs": normalized}, sort_keys=True).encode()).hexdigest()
    return {"inputs": normalized, "companies": companies, "targets": targets, "rows": rows,
            "digest": digest, "total": len(targets), "complete": complete, "remaining": remaining,
            "requests_estimate": remaining, "reuse_estimate": complete, "counts": dict(counts),
            "groups": dict(Counter(row["group"] for row in rows)),
            "existing_records": len(store.records(ticker=companies[0]["ticker"] if scope == "current" else None, include_rows=False)),
            "denominator": "所选公司范围 × 财报期次 × 数据档位（行情补齐按指标 × 年度窗口）；包含未尝试目标，合法空响应算已完成但无数值",
            "blockers": ["所选期次尚未结束，不期望财报已发布：" + "、".join(future_periods)] if future_periods else [],
            "notice": "请求预估按待补目标计；缓存/重试会改变实际请求次数。账号权限尚未验证，耗时未知。采集不自动生成报告。"}


def explain_batch(store, batch):
    completed = batch.get("completed") or {}
    rows = []
    for target in batch.get("targets") or []:
        outcome = completed.get(target_key(target)) or {}
        state = outcome.get("result") or "unattempted"
        if state in ("ok", "empty") and not store.serves_target(target):
            state = "stale"
        record = store.find(target["ticker"], target["dataset"], target["period"], params=target["params"], include_rows=False)
        rows.append({"company": target.get("company_ticker") or target["ticker"] or "市场公共数据",
                     "group": business_group(target["dataset"]), "dataset": target["dataset"],
                     "period": target["period"], "state": state, "state_label": RESULTS.get(state, (state, ""))[0],
                     "next_step": RESULTS.get(state, (state, ""))[1],
                     "fetched_at": (record or {}).get("fetched_at") or "未知",
                     "error_excerpt": outcome.get("error_excerpt") or "",
                     "impact": "图表覆盖/复权/估值可能不足" if business_group(target["dataset"]) == "行情与估值" else "报告证据可能不足；报告尚未更新"})
    counts = Counter(row["state"] for row in rows)
    progress = batch.get("progress") or {}
    status = batch.get("status") or "pending"
    if status == "running" and not _pid_alive(batch.get("owner_pid")):
        status = "paused"
    if status == "done" and counts["stale"]:
        status = "partial"
    phase = progress.get("phase") if status == "running" else status
    usage = batch.get("usage") or {}
    failure = counts["error"] + counts["no_permission"] + counts["rate_limited"]
    return {"batch_id": batch["batch_id"], "status": status, "status_label": PHASES.get(phase, PHASES.get(status, status)),
            "current": progress.get("current") or {}, "completed": len(completed), "total": len(rows),
            "success": counts["ok"] + counts["empty"], "failures": failure,
            "uncompleted": counts["unattempted"] + counts["stale"], "empty": counts["empty"],
            "actual_requests": None if usage.get("actual_requests_incomplete") else usage.get("actual_requests"),
            "reused": usage.get("archive_hits", 0),
            "new": max(0, len(completed) - int(usage.get("archive_hits", 0)) - failure),
            "rows": rows, "can_resume": status in ("paused", "partial", "failed"),
            "notice": "仅统计本批次目标。合法空响应没有数值；图表可离线刷新，报告仍需显式重建/生成。耗时未知。"}


def _body(ctx):
    try:
        value = json.loads(ctx.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadRequest("请求需为 JSON") from exc
    if not isinstance(value, dict):
        raise BadRequest("请求需为对象")
    return value


class CollectionRunner:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lock = threading.Lock()
        self.active = {}

    def start(self, planned, batch_id=None):
        token = resolve_token()
        if not token:
            raise NoToken("未配置数据源凭据；请在项目 .env 或环境变量配置")
        store = DataStore(self.ctx.config.archive_root)
        batch_id = batch_id or PullBatch.create_id()
        with self.lock:
            if any(thread.is_alive() for thread, _ in self.active.values()):
                raise BatchRunning("已有采集运行中；请等待完成或暂停")
            if any(batch.get("status") == "running" and _pid_alive(batch.get("owner_pid")) for batch in store.list_batches()):
                raise BatchRunning("原始仓已有采集运行中")
            targets = planned["targets"]
            existing = store.load_batch(batch_id)
            if not existing:
                now = datetime.now(timezone.utc).isoformat()
                existing = {"batch_id": batch_id, "profile": planned["inputs"]["tier"], "targets": targets,
                            "status": "pending", "completed": {}, "created_at": now,
                            "progress": {"completed": 0, "total": len(targets), "inputs": planned["inputs"]},
                            "usage": {"new_requests": 0, "archive_hits": 0, "actual_requests": 0}}
                store.append_batch(existing)
            pause = threading.Event()
            thread = threading.Thread(target=self._run, args=(store, existing, token, pause), daemon=True)
            self.active[batch_id] = (thread, pause)
            thread.start()
        return explain_batch(store, store.load_batch(batch_id))

    def _run(self, store, batch, token, pause):
        # The HTTP thread reads its connection while the worker writes snapshots.
        # Give the worker its own SQLite connection; never share one transaction
        # across the request and collection threads.
        store = DataStore(store.root)
        try:
            from tushare_collector import TushareClient

            access = TushareClient(token, store=store, batch_id=batch["batch_id"])._access
            PullBatch(store, batch["targets"], batch["profile"], access, token=token,
                      should_pause=pause.is_set).run(batch_id=batch["batch_id"], confirm=True)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            latest = store.load_batch(batch["batch_id"]) or batch
            latest.update(status="failed", owner_pid=None)
            latest.setdefault("progress", {})["error"] = type(exc).__name__
            store.append_batch(latest)
            if self.ctx.log:
                self.ctx.log(f"collection {batch['batch_id']} failed: {type(exc).__name__}")
        finally:
            store.close()

    def pause(self, batch_id):
        with self.lock:
            running = self.active.get(batch_id)
            if running:
                running[1].set()
        return {"batch_id": batch_id, "message": "暂停已请求；当前请求完成后落盘并暂停"}


def _runner(ctx):
    # registry lifetime equals server lifetime; startup installs runner lazily under a module lock.
    with _RUNNER_LOCK:
        runner = getattr(ctx.registry, "collection_runner", None)
        if runner is None:
            runner = CollectionRunner(ctx)
            ctx.registry.collection_runner = runner
        return runner


_RUNNER_LOCK = threading.Lock()


def _plan(ctx, **_):
    return envelope.ok(make_plan(ctx))


def _run(ctx, **_):
    body = _body(ctx)
    if body.get("confirmed") is not True:
        raise QuotaConfirmRequired("请先核对采集范围、请求预估并明确确认")
    planned = make_plan(ctx, body.get("inputs") or {})
    if planned["blockers"]:
        raise BadRequest("；".join(planned["blockers"]) + "；请选择已结束期次")
    if body.get("digest") != planned["digest"]:
        raise BadRequest("采集范围/期次已变化，请重新检查计划并确认")
    return envelope.ok(_runner(ctx).start(planned))


def _batches(ctx, **_):
    store = DataStore(ctx.config.archive_root)
    company = ctx.query.get("company")
    ticker = _company(ctx, company)[0] if company else ""
    batches = store.list_batches(limit=100)
    if ticker:
        batches = [batch for batch in batches if any((target.get("company_ticker") or target.get("ticker")) == ticker for target in batch["targets"])]
    return envelope.ok({"batches": [explain_batch(store, batch) for batch in batches]})


def _resume(ctx, batch_id, **_):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", batch_id):
        raise BadRequest("批次标识无效")
    body = _body(ctx)
    if body.get("confirmed") is not True:
        raise QuotaConfirmRequired("恢复采集也需明确确认，会消耗配额")
    store = DataStore(ctx.config.archive_root)
    batch = store.load_batch(batch_id)
    if not batch:
        raise BatchNotFound("批次不存在")
    planned = {"targets": batch["targets"], "inputs": {"tier": batch["profile"]}}
    return envelope.ok(_runner(ctx).start(planned, batch_id=batch_id))


def _pause(ctx, batch_id, **_):
    return envelope.ok(_runner(ctx).pause(batch_id))


def _panel(ctx, company=None, **_):
    return {"company": company or ctx.query.get("company") or "", "periods": ",".join(default_periods()),
            "guide": "先检查计划，再明确确认联网采集；浏览计划与已有批次不联网。"}


def contribute(registry):
    registry.panel(PanelSpec(id="data.research", kind="collection", render="client", title="采集计划与结果",
                             provider=_panel, params=(Param("company", source="selection.company"),)))
    registry.route("GET", "/api/v1/research-data/plan", _plan)
    registry.route("POST", "/api/v1/research-data/run", _run)
    registry.route("GET", "/api/v1/research-data/batches", _batches)
    registry.route("POST", "/api/v1/research-data/batches/{batch_id}/resume", _resume)
    registry.route("POST", "/api/v1/research-data/batches/{batch_id}/pause", _pause)
