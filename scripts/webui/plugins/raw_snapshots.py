"""Versioned offline raw-archive bridge into the existing output source jail.

SQLite is opened read-only. Cheap record metadata selects the immutable snapshot;
rows_json is decoded only when that revision is absent. Snapshots are derived copies
inside output, never an expansion of DataStore's source-file permissions.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
from pathlib import Path

from ..core.errors import BadRequest, ParseFailed, PathOutsideRoot
from ..core.security import safe_join

SNAPSHOT_VERSION = 1
DATASETS = frozenset(("daily", "daily_basic", "adj_factor", "suspend_d", "trade_cal", "income"))
_LOCK = threading.RLock()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _decode_rows(encoded):
    rows = json.loads(encoded)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ParseFailed("原始存档记录不是有效行列表")
    return rows


def _snapshot_path(config, company, datasets, revision):
    root = Path(config.output_root)
    parts = (".research_sources", _digest(company)[:20], _digest(datasets)[:20], revision)
    # Check the unresolved chain too: a snapshot must not overwrite an unrelated
    # output file through a symlink even when its target happens to remain in jail.
    candidate = root
    for part in (*parts, "raw.json"):
        candidate = candidate / part
        if candidate.is_symlink():
            raise PathOutsideRoot("原始源快照路径不能使用符号链接")
    return safe_join(root, *parts, "raw.json")


def _write_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".raw-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError) as exc:
        # This temporary derived file was created by this operation, not user data.
        Path(temporary).unlink(missing_ok=True)
        raise ParseFailed("原始源快照写入失败", hint="检查 output 目录权限与磁盘空间。") from exc


def ensure_raw_snapshot(config, company, datasets):
    """Return ``(base, revision, reused)`` for one consistent SQLite revision.

    Revision covers the query scope, adapter format and every matching record's
    stable metadata. Reading rows and metadata in one read transaction prevents a
    concurrent archive update from assigning old metadata to new raw content.
    """
    from datalayer.universe import market_of, normalize_ticker
    from datalayer.errors import UniverseError
    try:
        if normalize_ticker(company) != company:
            raise BadRequest("原始源快照需要规范公司代码")
        market_of(company)
    except UniverseError as exc:
        raise BadRequest("原始源快照需要合法公司代码") from exc
    datasets = tuple(sorted(set(datasets)))
    if not datasets or not set(datasets) <= DATASETS:
        raise BadRequest("原始源快照请求了未知数据组")
    archive = Path(config.archive_root).expanduser() / "store.db"
    scope = {"format": SNAPSHOT_VERSION, "company": company, "datasets": datasets,
             "global_ticker": "", "global_dataset": "trade_cal"}
    with _LOCK:
        connection = None
        try:
            if archive.is_file():
                connection = sqlite3.connect(archive.resolve().as_uri() + "?mode=ro", uri=True)
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN")
                where = f"dataset IN ({','.join('?' for _ in datasets)}) AND (ticker=? OR (ticker='' AND dataset='trade_cal'))"
                arguments = (*datasets, company)
                metadata = [dict(row) for row in connection.execute(
                    "SELECT id,ticker,dataset,fetched_at,content_sha256,result,params_json "
                    f"FROM raw_record WHERE {where} ORDER BY fetched_at,id", arguments,
                )]
            else:
                metadata = []
            revision = _digest({"scope": scope, "records": metadata})
            path = _snapshot_path(config, company, datasets, revision)
            if path.is_file():
                return path.parent, revision, True
            records = [] if connection is None else connection.execute(
                "SELECT ticker,dataset,rows_json,fetched_at,content_sha256,result "
                f"FROM raw_record WHERE {where} ORDER BY fetched_at,id", arguments,
            ).fetchall()
            payload = {"snapshot_version": SNAPSHOT_VERSION, "company": company,
                       "revision": revision, "datasets": {}, "global_datasets": {}}
            for name in datasets:
                payload["datasets"][name] = {"rows": [], "sources": []}
            for record in records:
                target = payload["global_datasets"] if record["ticker"] == "" else payload["datasets"]
                entry = target.setdefault(record["dataset"], {"rows": [], "sources": []})
                source = {"dataset": record["dataset"], "fetched_at": record["fetched_at"],
                          "version": record["content_sha256"], "result": record["result"]}
                entry["sources"].append(source)
                if record["result"] == "ok":
                    for row in _decode_rows(record["rows_json"]):
                        if record["ticker"] and row.get("ts_code") and str(row["ts_code"]) != company:
                            continue
                        entry["rows"].append({**row, "source": source})
            _write_atomic(path, payload)
            return path.parent, revision, False
        except (sqlite3.Error, json.JSONDecodeError) as exc:
            raise ParseFailed("原始存档无法生成源快照", hint="检查存档格式与 SQLite 读取权限。") from exc
        finally:
            if connection is not None:
                connection.close()
