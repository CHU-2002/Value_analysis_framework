"""全市场已确认批次的保守预取：同源整期数据按原目标分片，不能证明则回退。

不改变目标集合、批次完成状态或客户端 VIP 配置；所有远程调用仍经 DataAccess。
缺标的从不记 empty，分页未完整终止时整组不写仓。
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd

from .access import MODE_OFFLINE
from .dataframe_codec import decode_frame
from .security import redact, token_fingerprint
from .gaps import DONE_KINDS
from .store import target_key, param_key

FINANCIAL = frozenset(("income", "balancesheet", "cashflow", "fina_indicator"))


def _fields(params):
    return {part.strip() for part in str(params.get("fields") or "").split(",") if part.strip()}


def _slice(frame, target):
    params = target.get("params") or {}
    if "ts_code" not in frame.columns or not _fields(params).issubset(frame.columns):
        return None
    if params.get("ts_code", target["ticker"]) != target["ticker"]:
        return None
    result = frame[frame["ts_code"].astype(str) == target["ticker"]]
    if target["dataset"] in FINANCIAL:
        if "end_date" not in frame.columns:
            return None
        result = result[result["end_date"].astype(str) == str(target["period"])]
    for name, value in params.items():
        if name in ("fields", "ts_code", "limit", "offset"):
            continue
        if name in ("start_date", "end_date") and target["dataset"] == "daily":
            if "trade_date" not in result.columns:
                return None
            dates = result["trade_date"].astype(str)
            result = result[dates >= str(value)] if name == "start_date" else result[dates <= str(value)]
            continue
        column = "end_date" if name == "period" else name
        if column not in frame.columns:
            return None
        result = result[result[column].astype(str) == str(value)]
    # A missing ticker/variant is not evidence of a legitimate empty response.
    return None if result.empty else result.reset_index(drop=True)


def prefetch(store, targets, access, *, completed=None, page_size=1000, max_pages=100,
             should_pause=None, max_requests=None, whole_market=False):
    """返回 actual_requests/prefetched_targets/archive_hits/fallback_targets/failures/paused。

    金融整期用于实际 VIP 客户端，或明确 whole_market 全市场批次的官方 VIP 接口。必须连续分页直到
    空末页；重复页、跨页重复行、字段缺失、权限错误、暂停或预算不足均回退。
    stock_basic 仅从已有目录原始记录分区（零请求并继承源元数据）。daily 按每个日历日
    确认真实响应和完整分页，全窗口成功后才分区；缺标的从不写 empty。成功目标不重写。
    """
    if page_size <= 0 or max_pages <= 0:
        raise ValueError("分页大小及页数上限必须为正")
    should_pause = should_pause or (lambda: False)
    completed = completed or {}
    report = {"actual_requests": 0, "prefetched_targets": 0, "archive_hits": 0,
              "fallback_targets": 0, "stock_basic_reused": 0, "checkpoint_reused": 0, "source_apis": {}, "failures": [], "paused": False}
    pending = []
    for target in targets:
        if completed.get(target_key(target), {}).get("result") in DONE_KINDS or store.serves_target(target):
            report["archive_hits"] += 1
        else:
            pending.append(target)
    budget = len(pending) if max_requests is None else min(int(max_requests), len(pending))
    calls_before = access.remote_calls
    source_frames = None
    grouped = defaultdict(list)
    for target in pending:
        if should_pause():
            report["paused"] = True
            report["fallback_targets"] += 1
            continue
        dataset = target["dataset"]
        if dataset == "stock_basic":
            if source_frames is None:
                source_frames = [(record, decode_frame(record["columns_json"], record["rows_json"])) for record in store.find_family("", "stock_basic", period="latest")
                                 if record["result"] == "ok"]
            for source, frame in source_frames:
                partition = _slice(frame, target)
                if partition is not None:
                    store.write_frame(ticker=target["ticker"], dataset=dataset, period=target["period"],
                                      params=target["params"], frame=partition, result="ok",
                                      fetched_at=source["fetched_at"], token_fingerprint=source["token_fingerprint"],
                                      batch_id=source.get("batch_id"), tier_label=source.get("tier_label") or "",
                                      quota_profile=source.get("quota_profile") or "")
                    report["stock_basic_reused"] += 1
                    report["prefetched_targets"] += 1
                    break
            else:
                report["fallback_targets"] += 1
        elif dataset in FINANCIAL and str(target.get("period") or "") not in ("", "latest") \
                and (whole_market or getattr(access.client, "_vip_mode", False)) and access.mode != MODE_OFFLINE \
                and "offset" not in target["params"]:
            params = {key: value for key, value in target["params"].items() if key != "ts_code"}
            params.setdefault("period", target["period"])
            grouped[(dataset, json.dumps(params, sort_keys=True))].append(target)
        elif dataset == "daily" and whole_market and access.mode != MODE_OFFLINE and access.client is not None \
                and {"start_date", "end_date"}.issubset(target["params"]) \
                and set(target["params"]) <= {"ts_code", "fields", "start_date", "end_date", "limit"}:
            params = {key: value for key, value in target["params"].items() if key != "ts_code"}
            grouped[(dataset, json.dumps(params, sort_keys=True))].append(target)
        else:
            report["fallback_targets"] += 1
    for (dataset, canonical), group in grouped.items():
        if should_pause():
            report["paused"] = True
            report["fallback_targets"] += len(group)
            continue
        params = json.loads(canonical)
        official_alias = dataset + "_vip" if dataset in FINANCIAL and whole_market else dataset
        report["source_apis"][dataset] = official_alias
        requested = _fields(params)
        required = {"ts_code", "trade_date" if dataset == "daily" else "end_date"} | ({"report_type"} if "report_type" in params else set())
        if requested:
            params["fields"] = ",".join(sorted(requested | required))
        else:
            params.pop("fields", None)
        frames = []
        try:
            if dataset == "daily":
                start = datetime.strptime(str(params.pop("start_date")), "%Y%m%d")
                end = datetime.strptime(str(params.pop("end_date")), "%Y%m%d")
                if end < start:
                    raise ValueError("日线窗口结束日期早于开始日期")
                # Each calendar day is confirmed by an actual response; no inferred holidays.
                date_params = [{**params, "trade_date": (start + timedelta(days=day)).strftime("%Y%m%d")}
                               for day in range((end - start).days + 1)]
            else:
                date_params = [params]
            for request in date_params:
                access.current_request = {"dataset": official_alias, "params": request}
                access._notify("replaying")
                source_params = {key: value for key, value in request.items() if key not in ("limit", "offset")}
                source_period = str(request.get("period") or "latest")
                checkpoint_key = f"market_bulk_checkpoint:{dataset}:{source_period}:{param_key(source_params)}"
                cached = store.find("", dataset, source_period, params=source_params)
                marker = store.meta_get(checkpoint_key)
                cache_frame = None
                if cached and cached["result"] in DONE_KINDS \
                        and cached.get("batch_id") == access.batch_id \
                        and cached.get("token_fingerprint") == token_fingerprint(access.token) \
                        and marker == cached["content_sha256"] \
                        and (requested or not cached["params"].get("fields")):
                    candidate = decode_frame(cached["columns_json"], cached["rows_json"])
                    if (requested | required).issubset(candidate.columns):
                        if dataset != "daily" or candidate.empty or candidate["trade_date"].astype(str).eq(request["trade_date"]).all():
                            cache_frame = candidate
                if cache_frame is not None:
                    report["checkpoint_reused"] += 1
                    if not cache_frame.empty:
                        frames.append(cache_frame)
                    continue
                source_frames = []
                seen_rows = set()
                offset = 0
                for page in range(max_pages):
                    if should_pause():
                        report["paused"] = True
                        raise ValueError("用户暂停预取")
                    remaining = budget - (access.remote_calls - calls_before)
                    if remaining < int(getattr(access.client, "MAX_RETRIES", 5)):
                        raise ValueError("预取请求预算不足，回退原目标")
                    frame = access._invoke(official_alias, {**request, "limit": page_size, "offset": offset})
                    if frame is None:
                        raise ValueError("接口没有返回结构化响应")
                    if frame.empty:
                        break
                    if not (requested | required).issubset(frame.columns):
                        raise ValueError("整期/整日响应缺少请求字段或分区字段")
                    if frame["ts_code"].isna().any() or frame["ts_code"].astype(str).str.strip().eq("").any():
                        raise ValueError("响应存在缺失标的代码，无法安全分区")
                    if dataset == "daily" and not frame["trade_date"].astype(str).eq(request["trade_date"]).all():
                        raise ValueError("整日响应包含其他交易日期，无法确认窗口完整性")
                    # Detect ignored offsets without discarding any source rows.
                    row_signatures = {json.dumps(row, sort_keys=True, default=str) for row in frame.to_dict(orient="records")}
                    if seen_rows & row_signatures:
                        raise ValueError("分页返回重复行，无法确认完整性")
                    seen_rows.update(row_signatures)
                    source_frames.append(frame)
                    offset += len(frame)
                else:
                    raise ValueError("分页未到空末页，不能确认完整性")
                complete_frame = pd.concat(source_frames, ignore_index=True, sort=False) if source_frames else pd.DataFrame(columns=sorted(requested | required))
                access._persist("", dataset, source_period, source_params, complete_frame,
                                "empty" if complete_frame.empty else "ok", None)
                checkpoint = store.find("", dataset, source_period, params=source_params, include_rows=False)
                store.meta_set(checkpoint_key, checkpoint["content_sha256"])
                if not complete_frame.empty:
                    frames.append(complete_frame)
            if frames:
                frame = pd.concat(frames, ignore_index=True, sort=False)
                # One grouping pass avoids scanning the full annual market frame for every company.
                by_ticker = {str(ticker): part for ticker, part in frame.groupby("ts_code", sort=False)}
                for target in group:
                    source = by_ticker.get(str(target["ticker"]))
                    partition = _slice(source, target) if source is not None else None
                    if partition is None:
                        report["fallback_targets"] += 1
                        continue
                    access._persist(target["ticker"], dataset, target["period"], target["params"], partition, "ok", None)
                    report["prefetched_targets"] += 1
            else:
                report["fallback_targets"] += len(group)
        except Exception as exc:  # noqa: BLE001 -- 优化失败不改变原逐目标执行路径。
            report["failures"].append({"dataset": dataset, "official_alias": official_alias, "params": json.loads(canonical),
                                       "reason": redact(str(exc), (access.token,))})
            report["fallback_targets"] += len(group)
    report["actual_requests"] = max(0, access.remote_calls - calls_before)
    return report
