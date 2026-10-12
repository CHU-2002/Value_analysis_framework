# 覆盖需求：REQ-016.4、REQ-016（AC-3、AC-4；AC-4.1 / AC-4.2 / AC-4.3）
"""全市场目录过滤、覆盖缺口、快照可追踪性及批次大规模增量保存。"""
import json
from types import SimpleNamespace

import pandas as pd
import pytest
from datalayer.directory import Directory
from datalayer.errors import UsageError
from datalayer.pull import api_eligible, targets_for
from datalayer.store import DataStore
from datalayer.universe import Universe


def row(code, exchange, market="主板", name="*ST公司", status="L"):
    return {"ts_code": code, "name": name, "exchange": exchange, "market": market,
            "list_status": status, "curr_type": "CNY", "list_date": "20000101"}


def client_for(payload):
    calls = []
    def call(dataset, **params):
        calls.append((dataset, params))
        value = payload[params["exchange"]]
        if isinstance(value, Exception):
            raise value
        return pd.DataFrame(value)
    return SimpleNamespace(_access=SimpleNamespace(mode="online", call=call, token="private-token")), calls


BASE = {"SSE": [row("600001.SH", "SSE")], "SZSE": [row("000001.SZ", "SZSE")],
        "BSE": [row("920001.BJ", "BSE", "北交所")]}


def test_snapshot_separate_filtered_and_historical(tmp_path):
    store = DataStore(tmp_path)
    Universe(store).add("600887.SH", "伊利")
    directory = Directory(store)
    assert directory.latest() is None
    payload = {key: list(value) for key, value in BASE.items()}
    payload["SSE"] += [row("900901.SH", "SSE", "B股"), row("510001.SH", "SSE", "ETF"),
                       row("689001.SH", "SSE", "CDR"), row("600002.SH", "SSE", status="D")]
    client, calls = client_for(payload)
    first = directory.refresh(client)
    assert len(calls) == 3 and client._access.mode == "online"
    assert first["count"] == 3 and first["exclusions"]["count"] == 4
    assert first["gaps"] == []
    assert all(item["status"] == "confirmed" for item in first["coverage"].values())
    assert [entry["ticker"] for entry in Universe(store).entries()] == ["600887.SH"]
    client, _ = client_for({**BASE, "SSE": [row("600003.SH", "SSE")],
                           "SZSE": [row("000001.SZ", "SZSE", name="新名称")]})
    second = directory.refresh(client)
    assert first["snapshot_id"] != second["snapshot_id"]
    assert directory.get(first["snapshot_id"]) == first
    assert directory.latest() == second and len(directory.list()) == 2
    diff = directory.compare(first["snapshot_id"], second["snapshot_id"])
    assert [item["ticker"] for item in diff["added"]] == ["600003.SH"]
    assert [item["ticker"] for item in diff["removed"]] == ["600001.SH"]
    assert len(diff["changed"]) == 1
    store.close()
    assert Directory(DataStore(tmp_path)).get(first["snapshot_id"]) == first


def test_uncertain_exchange_and_redacted_errors(tmp_path):
    directory = Directory(DataStore(tmp_path))
    client, _ = client_for({"SSE": [row("600001.SH", "SSE", market="未知")],
                           "SZSE": ValueError("private-token 无权限"), "BSE": []})
    snapshot = directory.refresh(client)
    assert len(snapshot["gaps"]) == 3 and snapshot["count"] == 0
    assert "private-token" not in json.dumps(snapshot)
    assert all(value["status"] == "unknown" for value in snapshot["coverage"].values())
    client._access.mode = "offline"
    with pytest.raises(UsageError):
        directory.refresh(client)
    with pytest.raises(UsageError):
        directory.compare("missing", snapshot["snapshot_id"])
    client, _ = client_for({**BASE, "BSE": [{"ts_code": "920001.BJ"}]})
    assert directory.refresh(client)["coverage"]["BSE"]["status"] == "unknown"
    bad = dict(BASE["BSE"][0])
    bad["curr_type"] = None
    client, _ = client_for({**BASE, "BSE": [bad], "SSE": BASE["SSE"] * 2})
    snapshot = directory.refresh(client)
    assert snapshot["coverage"]["BSE"]["status"] == "unknown"
    assert snapshot["coverage"]["SSE"]["status"] == "unknown"
    assert snapshot["coverage"]["SSE"]["count"] == 1
    assert snapshot["exclusions"]["by_reason"]["重复代码"] == 1



def test_bj_supported_for_watchlist_and_market_calendar(tmp_path, monkeypatch):
    store = DataStore(tmp_path)
    assert Universe(store).add("920001.BJ", "北交所公司")["entry"]["market"] == "BJ"
    assert api_eligible("stock_basic", "BJ") and not api_eligible("hk_basic", "BJ")
    from datalayer import registry
    monkeypatch.setitem(registry.PROFILES, "bulk", ("trade_cal", "stock_basic"))
    targets = targets_for(["920001.BJ"], ["20251231"], "bulk")
    assert any(target["dataset"] == "trade_cal" and target["params"]["exchange"] == "BSE" for target in targets)


def test_incremental_batch_persistence_and_compatibility(tmp_path):
    store = DataStore(tmp_path)
    batch = {"batch_id": "large", "profile": "frugal", "status": "running", "targets": [{"ticker": "a"}],
             "completed": {}, "progress": {"directory": {"snapshot_id": "immutable"}}, "usage": {}}
    store.append_batch(batch, changed_keys=[])
    statements = []
    store._connect().set_trace_callback(statements.append)
    batch["completed"]["key"] = {"ticker": "a", "dataset": "daily", "period": "latest", "result": "ok"}
    store.append_batch(batch, changed_keys=["key"])
    assert not any("DELETE" in sql or "targets_json=" in sql for sql in statements)
    assert store.load_batch("large")["completed"]["key"]["result"] == "ok"
    assert json.loads((tmp_path / "batches/large.json").read_text())["completed"] == {}
    batch["status"] = "paused"
    store.append_batch(batch, changed_keys=[])
    assert json.loads((tmp_path / "batches/large.json").read_text())["completed"]["key"]["result"] == "ok"
    assert store.load_batch("large")["progress"]["directory"]["snapshot_id"] == "immutable"
    batch["completed"] = {}
    store.append_batch(batch)
    assert store.load_batch("large")["completed"] == {}
