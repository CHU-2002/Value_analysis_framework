"""一次性导入既有资产，幂等可重复（`REQ-011` 的 `AC-8`）。

两条来源：

1. `~/turtle_archive/**`：`REQ-009.4` 的原始存档（`manifest.jsonl` 逐条指着数据文件）；
2. `output/.collector_cache/**`：旧的 7 天 TTL 文件缓存（`stock_basic_<code>.json`、
   `us_daily_all.parquet`）。

规则（`DATA_LAYER_PLAN` §9）：

- **同一份数据只落一个地方**：导入完成后旧路径**停写**或只读，不出现「两处都在写」；
- **幂等**：同键已存在即跳过；同键但内容摘要不同记为**冲突**，不覆盖、只报告
  （要不要覆盖由使用者显式决定）；
- **不删旧文件**：资产不主动删（`REQ-009.4` 的 `AC-4.3`）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .dataframe_codec import encode_frame
from .store import canonical_params, param_key

CACHE_DATASETS = ("stock_basic", "hk_basic", "us_basic")
US_DAILY_SNAPSHOT_PARAMS = {"scope": "all_market", "limit": 6000}


def _frame_from_payload(payload) -> pd.DataFrame:
    if payload is None:
        return pd.DataFrame()
    if isinstance(payload, dict):
        payload = [payload]
    if isinstance(payload, list):
        return pd.DataFrame.from_records(payload)
    return pd.DataFrame(payload)


def _record_from_payload(*, ticker, dataset, period, params, payload, result="ok",
                         error_excerpt=None, **meta) -> dict:
    encoded = encode_frame(_frame_from_payload(payload))
    return {
        "ticker": ticker, "dataset": dataset, "period": period, "params": params,
        "result": result, "error_excerpt": error_excerpt, **encoded, **meta,
    }


def _import_one(store, record, *, dry_run: bool, report: dict, origin: str,
                force: bool = False) -> None:
    """写一条导入记录，并把结果计入报告（导入 / 跳过 / 冲突）。"""

    params_json = canonical_params(record.get("params"))
    digest = param_key(record.get("params"))
    existing = store.find(str(record["ticker"]), str(record["dataset"]), str(record["period"]),
                          param_digest=digest, include_rows=False)
    if existing is not None:
        if existing["content_sha256"] != record.get("content_sha256"):
            if not force:
                report["conflicts"] += 1
                report["details"].append({
                    "origin": origin, "ticker": record["ticker"], "dataset": record["dataset"],
                    "period": record["period"], "action": "conflict",
                    "existing_sha256": existing["content_sha256"],
                    "incoming_sha256": record.get("content_sha256"),
                })
                return
            if dry_run:
                report["imported"] += 1
                report["details"].append({"origin": origin, "ticker": record["ticker"],
                                          "dataset": record["dataset"],
                                          "period": record["period"],
                                          "action": "would-overwrite"})
                return
            record.setdefault("params_json", params_json)
            store.save_raw(record, on_conflict="replace")
            report["imported"] += 1
            report["details"].append({"origin": origin, "ticker": record["ticker"],
                                      "dataset": record["dataset"], "period": record["period"],
                                      "action": "overwritten"})
            return
        report["skipped"] += 1
        return
    if dry_run:
        report["imported"] += 1
        report["details"].append({"origin": origin, "ticker": record["ticker"],
                                  "dataset": record["dataset"], "period": record["period"],
                                  "action": "would-import"})
        return
    record.setdefault("params_json", params_json)
    store.save_raw(record, on_conflict="skip")
    report["imported"] += 1
    report["details"].append({"origin": origin, "ticker": record["ticker"],
                              "dataset": record["dataset"], "period": record["period"],
                              "action": "imported"})


def _new_report(source: str) -> dict:
    return {"source": source, "imported": 0, "skipped": 0, "conflicts": 0, "details": []}


def import_archive(store, root=None, *, dry_run: bool = False, force: bool = False) -> dict:
    """导入 `REQ-009.4` 的原始存档（按其 `manifest.jsonl` 逐条）。"""

    report = _new_report("turtle_archive")
    archive_root = Path(root).expanduser() if root else store.root
    manifest = archive_root / "manifest.jsonl"
    if not manifest.is_file():
        report["note"] = f"没有找到 {manifest}"
        return report
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            report["conflicts"] += 1
            report["details"].append({"origin": "manifest", "action": "unreadable", "line": line[:120]})
            continue
        relative = entry.get("file")
        if not relative:
            # 新仓自己写的 manifest 行没有 file（记录在 store.db 里），导入时跳过。
            report["skipped"] += 1
            continue
        data_path = archive_root / relative
        try:
            payload = json.loads(data_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report["conflicts"] += 1
            report["details"].append({"origin": relative, "action": "unreadable-data"})
            continue
        record = _record_from_payload(
            ticker=str(entry.get("ticker", "")),
            dataset=str(entry.get("dataset", "")),
            period=str(entry.get("period", "latest")) or "latest",
            params=(entry.get("api") or {}).get("params") or {},
            payload=payload,
            result=str(entry.get("result", "ok")),
            error_excerpt=entry.get("error_excerpt"),
            fetched_at=entry.get("fetched_at"),
            token_fingerprint=entry.get("token_fingerprint", ""),
            tier_label=entry.get("tier_label", ""),
            quota_profile=entry.get("quota_profile", ""),
            framework_version=entry.get("framework_version"),
            batch_id=entry.get("batch_id"),
        )
        if not record["ticker"] or not record["dataset"]:
            report["conflicts"] += 1
            report["details"].append({"origin": relative, "action": "missing-key"})
            continue
        _import_one(store, record, dry_run=dry_run, report=report, origin=relative,
                    force=force)
    return report


def import_collector_cache(store, cache_dir=None, *, dry_run: bool = False,
                           force: bool = False) -> dict:
    """导入 `output/.collector_cache/**`（基本信息 JSON + 美股全市场 parquet）。"""

    report = _new_report("collector_cache")
    folder = Path(cache_dir).expanduser() if cache_dir else Path("output/.collector_cache")
    if not folder.is_dir():
        report["note"] = f"没有找到 {folder}"
        return report
    for path in sorted(folder.iterdir()):
        if path.suffix == ".json":
            # 数据集名自带下划线（`stock_basic`），所以按**已知数据集前缀**匹配：
            # 按第一个下划线切会把 `stock_basic_600887.SH` 切成 `stock` / `basic_600887.SH`，
            # 条目于是静默落进「跳过」分支，导入报告却显示成「幂等跳过」。
            dataset = next((name for name in CACHE_DATASETS
                            if path.stem.startswith(f"{name}_")), None)
            ticker = path.stem[len(dataset) + 1:] if dataset else ""
            if not dataset or not ticker:
                report["skipped"] += 1
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                report["conflicts"] += 1
                report["details"].append({"origin": path.name, "action": "unreadable"})
                continue
            record = _record_from_payload(ticker=ticker, dataset=dataset, period="latest",
                                          params={"ts_code": ticker}, payload=payload)
        elif path.suffix == ".parquet":
            try:
                frame = pd.read_parquet(path)
            except Exception as exc:  # noqa: BLE001（缺 pyarrow / 文件损坏都只记一条）
                report["conflicts"] += 1
                report["details"].append({"origin": path.name, "action": "unreadable",
                                          "error": f"{type(exc).__name__}: {exc}"[:200]})
                continue
            encoded = encode_frame(frame)
            record = {"ticker": "", "dataset": "us_daily", "period": "latest",
                      "params": US_DAILY_SNAPSHOT_PARAMS, "result": "ok",
                      "error_excerpt": None, **encoded}
        else:
            continue
        _import_one(store, record, dry_run=dry_run, report=report, origin=path.name,
                    force=force)
    return report


def import_all(store, *, archive_root=None, cache_dir=None, dry_run: bool = False,
               force: bool = False) -> dict:
    """两条来源各导一次，合并成一份迁移报告。"""

    archive = import_archive(store, archive_root, dry_run=dry_run, force=force)
    cache = import_collector_cache(store, cache_dir, dry_run=dry_run, force=force)
    merged = _new_report("all")
    for report in (archive, cache):
        merged["imported"] += report["imported"]
        merged["skipped"] += report["skipped"]
        merged["conflicts"] += report["conflicts"]
        merged["details"].extend(report["details"])
        if report.get("note"):
            merged.setdefault("notes", []).append(f"{report['source']}: {report['note']}")
    merged["by_source"] = {report["source"]: {key: report[key]
                                              for key in ("imported", "skipped", "conflicts")}
                           for report in (archive, cache)}
    if not dry_run:
        store.meta_set("migration_last_report", json.dumps(
            {key: merged[key] for key in ("imported", "skipped", "conflicts")},
            ensure_ascii=False))
    return merged


def format_report(report: dict) -> str:
    lines = [f"迁移 [{report['source']}]：导入 {report['imported']} / 跳过 {report['skipped']} "
             f"/ 冲突 {report['conflicts']}"]
    for note in report.get("notes", []):
        lines.append(f"  注意：{note}")
    for item in report["details"]:
        if item.get("action") in ("conflict", "unreadable", "missing-key", "unreadable-data"):
            lines.append(f"  {item.get('action')}: {item.get('origin')}")
    return "\n".join(lines)
