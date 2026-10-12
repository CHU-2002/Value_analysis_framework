"""全市场已确认批次的保守预取：同源整期数据按原目标分片，不能证明则回退。

不改变目标集合、批次完成状态或客户端 VIP 配置；所有远程调用仍经 DataAccess。
缺标的从不记 empty，分页未完整终止时整组不写仓。
"""
from __future__ import annotations

import json
from collections import defaultdict

import pandas as pd

from .access import MODE_OFFLINE
from .dataframe_codec import decode_frame
from .security import redact
from .gaps import DONE_KINDS
from .store import target_key

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
        column = "end_date" if name == "period" else name
        if column not in frame.columns:
            return None
        result = result[result[column].astype(str) == str(value)]
    # A missing ticker/variant is not evidence of a legitimate empty response.
    return None if result.empty else result.reset_index(drop=True)


def prefetch(store, targets, access, *, completed=None, page_size=1000, max_pages=100,
             should_pause=None, max_requests=None):
    """返回 actual_requests/prefetched_targets/archive_hits/fallback_targets/failures/paused。

    金融整期只用于实际 VIP 客户端，依其既有 VIP 映射路由。必须连续分页直到
    空末页；重复页、跨页重复行、字段缺失、权限错误、暂停或预算不足均回退。
    stock_basic 仅从已有目录原始记录分区（零请求）。成功目标不重写。
    """
    if page_size <= 0 or max_pages <= 0:
        raise ValueError("分页大小及页数上限必须为正")
    should_pause = should_pause or (lambda: False)
    completed = completed or {}
    report = {"actual_requests": 0, "prefetched_targets": 0, "archive_hits": 0,
              "fallback_targets": 0, "failures": [], "paused": False}
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
                source_frames = [decode_frame(record["columns_json"], record["rows_json"]) for record in store.find_family("", "stock_basic", period="latest")
                                 if record["result"] == "ok"]
            for frame in source_frames:
                partition = _slice(frame, target)
                if partition is not None:
                    access._persist(target["ticker"], dataset, target["period"], target["params"], partition, "ok", None)
                    report["prefetched_targets"] += 1
                    break
            else:
                report["fallback_targets"] += 1
        elif dataset in FINANCIAL and str(target.get("period") or "") not in ("", "latest") \
                and getattr(access.client, "_vip_mode", False) and access.mode != MODE_OFFLINE \
                and "offset" not in target["params"]:
            params = {key: value for key, value in target["params"].items() if key != "ts_code"}
            params.setdefault("period", target["period"])
            grouped[(dataset, json.dumps(params, sort_keys=True))].append(target)
        else:
            report["fallback_targets"] += 1
    for (dataset, canonical), group in grouped.items():
        if should_pause():
            report["paused"] = True
            report["fallback_targets"] += len(group)
            continue
        params = json.loads(canonical)
        requested = _fields(params)
        required = {"ts_code", "end_date"} | ({"report_type"} if "report_type" in params else set())
        if requested:
            params["fields"] = ",".join(sorted(requested | required))
        else:
            params.pop("fields", None)
        frames, seen_rows = [], set()
        try:
            for page in range(max_pages):
                if should_pause():
                    report["paused"] = True
                    raise ValueError("用户暂停预取")
                remaining = budget - (access.remote_calls - calls_before)
                if remaining < int(getattr(access.client, "MAX_RETRIES", 5)):
                    raise ValueError("预取请求预算不足，回退原目标")
                frame = access._invoke(dataset, {**params, "limit": page_size, "offset": page * page_size})
                if frame is None:
                    raise ValueError("接口没有返回结构化响应")
                if frame.empty:
                    break
                if not (requested | required).issubset(frame.columns):
                    raise ValueError("整期响应缺少请求字段或分区字段")
                # Detect ignored offsets without discarding any source rows.
                row_signatures = {json.dumps(row, sort_keys=True, default=str) for row in frame.to_dict(orient="records")}
                if seen_rows & row_signatures:
                    raise ValueError("分页返回重复行，无法确认完整性")
                seen_rows.update(row_signatures)
                frames.append(frame)
            else:
                raise ValueError("分页未到空末页，不能确认完整性")
            if frames:
                frame = pd.concat(frames, ignore_index=True, sort=False)
                for target in group:
                    partition = _slice(frame, target)
                    if partition is None:
                        report["fallback_targets"] += 1
                        continue
                    access._persist(target["ticker"], dataset, target["period"], target["params"], partition, "ok", None)
                    report["prefetched_targets"] += 1
            else:
                report["fallback_targets"] += len(group)
        except Exception as exc:  # noqa: BLE001 -- 优化失败不改变原逐目标执行路径。
            report["failures"].append({"dataset": dataset, "params": json.loads(canonical),
                                       "reason": redact(str(exc), (access.token,))})
            report["fallback_targets"] += len(group)
    report["actual_requests"] = max(0, access.remote_calls - calls_before)
    return report
