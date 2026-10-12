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


def test_market_bulk_financial_pagination_and_missing_fallback(tmp_path):
    from datalayer.access import DataAccess
    from datalayer.market_bulk import prefetch
    from datalayer.store import target_key
    targets = [{"ticker": code, "dataset": "income", "period": "20260630",
                "params": {"ts_code": code, "period": "20260630", "report_type": "1",
                           "fields": "ts_code,end_date,report_type,revenue"}} for code in ("600001.SH", "000001.SZ", "920001.BJ")]
    calls = []
    def respond(**params):
        calls.append(params)
        pages = {0: [{"ts_code": "600001.SH", "end_date": "20260630", "report_type": "1", "revenue": 1},
                     {"ts_code": "600001.SH", "end_date": "20260630", "report_type": "1", "revenue": 2}],
                 2: [{"ts_code": "000001.SZ", "end_date": "20260630", "report_type": "1", "revenue": 3}]}
        return pd.DataFrame(pages.get(params["offset"], []))
    store = DataStore(tmp_path)
    client = SimpleNamespace(_vip_mode=True, MAX_RETRIES=1, pro=SimpleNamespace(income_vip=respond))
    access = DataAccess(store, client=client, token="test", rate_limit_seconds=0)
    report = prefetch(store, targets, access, page_size=6000)
    assert report["actual_requests"] == 3 and report["prefetched_targets"] == 2 and report["fallback_targets"] == 1
    assert [item["offset"] for item in calls] == [0, 2, 3]
    assert all(item["limit"] == 6000 for item in calls)
    assert all("ts_code" not in call for call in calls)
    assert store.serves_target(targets[0]) and store.serves_target(targets[1]) and not store.serves_target(targets[2])
    assert len(json.loads(store.find("600001.SH", "income", "20260630", params=targets[0]["params"])["rows_json"])) == 2
    assert prefetch(store, targets, access, completed={target_key(targets[2]): {"result": "ok"}})["archive_hits"] == 3


def test_market_bulk_repeated_page_fields_permission_and_offline(tmp_path):
    from datalayer.access import DataAccess
    from datalayer.market_bulk import prefetch
    target = {"ticker": "600001.SH", "dataset": "income", "period": "20260630",
              "params": {"ts_code": "600001.SH", "period": "20260630", "report_type": "6", "fields": "ts_code,end_date,report_type,revenue"}}
    for scenario in ("repeat", "missing", "permission", "wrong_variant"):
        store = DataStore(tmp_path / scenario)
        def respond(**params):
            if scenario == "permission":
                raise RuntimeError("没有权限 test-token")
            if scenario == "wrong_variant" and params["offset"]:
                return pd.DataFrame()
            data = {"ts_code": "600001.SH", "end_date": "20260630", "report_type": "1" if scenario == "wrong_variant" else "6", "revenue": 1}
            if scenario == "missing":
                del data["revenue"]
            return pd.DataFrame([data])
        client = SimpleNamespace(_vip_mode=True, MAX_RETRIES=1, pro=SimpleNamespace(income_vip=respond))
        access = DataAccess(store, client=client, token="test-token", rate_limit_seconds=0)
        report = prefetch(store, [target, {**target, "ticker": "000001.SZ"}, {**target, "ticker": "920001.BJ"}], access, page_size=1)
        assert report["prefetched_targets"] == 0 and not store.serves_target(target)
        assert "test-token" not in json.dumps(report)
    access.mode = "offline"
    before = access.remote_calls
    assert prefetch(store, [target], access)["actual_requests"] == 0
    assert access.remote_calls == before
    access.mode = "online"
    client._vip_mode = False
    assert prefetch(store, [target], access)["actual_requests"] == 0
    client._vip_mode = True
    paused = prefetch(store, [target], access, should_pause=lambda: True)
    assert paused["paused"] and paused["actual_requests"] == 0
    capped = prefetch(store, [target], access, max_requests=0)
    assert capped["actual_requests"] == 0 and capped["fallback_targets"] == 1


def test_market_bulk_stock_basic_existing_directory_and_pause(tmp_path):
    from datalayer.access import DataAccess
    from datalayer.market_bulk import prefetch
    store = DataStore(tmp_path)
    frame = pd.DataFrame([row("600001.SH", "SSE")])
    store.write_frame(ticker="", dataset="stock_basic", period="latest", params={"exchange": "SSE", "list_status": "L"}, frame=frame, result="ok", fetched_at="2026-01-01T00:00:00+00:00", token_fingerprint="source-token-fingerprint", batch_id="directory-refresh")
    targets = [{"ticker": code, "dataset": "stock_basic", "period": "latest", "params": {"ts_code": code, "fields": "ts_code,name"}}
               for code in ("600001.SH", "000001.SZ")]
    access = DataAccess(store, token="test", rate_limit_seconds=0)
    report = prefetch(store, targets, access)
    assert report["prefetched_targets"] == 1 and report["fallback_targets"] == 1 and report["actual_requests"] == 0
    assert store.serves_target(targets[0]) and not store.serves_target(targets[1])
    saved = store.find("600001.SH", "stock_basic", "latest", params=targets[0]["params"])
    assert report["stock_basic_reused"] == 1
    assert saved["fetched_at"] == "2026-01-01T00:00:00+00:00"
    assert saved["batch_id"] == "directory-refresh" and saved["token_fingerprint"] == "source-token-fingerprint"


def test_market_bulk_daily_complete_window_and_explicit_official_vip(tmp_path):
    from datalayer.access import DataAccess
    from datalayer.market_bulk import prefetch
    targets = [{"ticker": f"60000{i}.SH", "dataset": "daily", "period": "latest",
                "params": {"ts_code": f"60000{i}.SH", "start_date": "20260101", "end_date": "20260102", "fields": "ts_code,trade_date,close"}} for i in range(6)]
    calls = []
    def daily(**params):
        calls.append(params)
        if params["trade_date"] == "20260102" or params["offset"] >= 2:
            return pd.DataFrame()
        return pd.DataFrame([{"ts_code": "600000.SH", "trade_date": "20260101", "close": 1},
                             {"ts_code": "600001.SH", "trade_date": "20260101", "close": 2}])
    store = DataStore(tmp_path)
    client = SimpleNamespace(_vip_mode=False, MAX_RETRIES=1, pro=SimpleNamespace(daily=daily))
    access = DataAccess(store, client=client, token="test", rate_limit_seconds=0, batch_id="confirmed")
    assert prefetch(store, targets, access)["actual_requests"] == 0
    report = prefetch(store, targets, access, whole_market=True, page_size=2)
    assert report["actual_requests"] == 3 and report["prefetched_targets"] == 2 and report["fallback_targets"] == 4
    assert {call["trade_date"] for call in calls} == {"20260101", "20260102"}
    assert all("start_date" not in call and "ts_code" not in call for call in calls)
    assert store.serves_target(targets[0]) and not store.serves_target(targets[2])
    assert store.find("600000.SH", "daily", "latest", params=targets[0]["params"])["batch_id"] == "confirmed"
    finance = [{"ticker": code, "dataset": "income", "period": "20260630", "params": {"ts_code": code, "period": "20260630", "fields": "ts_code,end_date,revenue"}} for code in ("600000.SH", "600001.SH")]
    def income_vip(**params):
        return pd.DataFrame([{"ts_code": "600000.SH", "end_date": "20260630", "revenue": 1}]) if not params["offset"] else pd.DataFrame()
    client.pro.income_vip = income_vip
    assert prefetch(store, finance, access)["actual_requests"] == 0
    assert prefetch(store, finance, access, whole_market=True)["prefetched_targets"] == 1
    assert client._vip_mode is False


def test_market_bulk_daily_incomplete_dates_never_persist(tmp_path):
    from datalayer.access import DataAccess
    from datalayer.market_bulk import prefetch
    targets = [{"ticker": f"60000{i}.SH", "dataset": "daily", "period": "latest",
                "params": {"ts_code": f"60000{i}.SH", "start_date": "20260101", "end_date": "20260102", "fields": "ts_code,trade_date,close"}} for i in range(6)]
    for scenario in ("repeat", "missing", "missing_ticker", "wrong_date", "late_error", "pause", "budget"):
        store = DataStore(tmp_path / scenario)
        def daily(**params):
            if params["trade_date"] == "20260102":
                raise RuntimeError("late error")
            if params["offset"] and scenario != "repeat":
                return pd.DataFrame()
            data = {"ts_code": "600000.SH", "trade_date": "20260102" if scenario == "wrong_date" else "20260101", "close": 1}
            if scenario == "missing":
                del data["close"]
            if scenario == "missing_ticker":
                data["ts_code"] = None
            return pd.DataFrame([data])
        client = SimpleNamespace(_vip_mode=False, MAX_RETRIES=1, pro=SimpleNamespace(daily=daily))
        access = DataAccess(store, client=client, token="test", rate_limit_seconds=0)
        report = prefetch(store, targets, access, whole_market=True, page_size=1,
                          max_requests=1 if scenario == "budget" else 6,
                          should_pause=(lambda: access.remote_calls > 0) if scenario == "pause" else None)
        assert report["prefetched_targets"] == 0 and report["fallback_targets"] == 6
        assert not store.serves_target(targets[0])
        if scenario == "late_error":
            source_params = {"trade_date": "20260101", "fields": "close,trade_date,ts_code"}
            source = store.find("", "daily", "latest", params=source_params)
            assert source and source["result"] == "ok"
            recovered_calls = []
            def recovered(**params):
                recovered_calls.append(params)
                if params["trade_date"] == "20260102" or params["offset"]:
                    return pd.DataFrame()
                return pd.DataFrame([{"ts_code": "600000.SH", "trade_date": "20260101", "close": 1}])
            client.pro.daily = recovered
            resumed = prefetch(store, targets, access, whole_market=True, page_size=1)
            assert resumed["checkpoint_reused"] == 1 and resumed["actual_requests"] == 1
            assert [call["trade_date"] for call in recovered_calls] == ["20260102"]
            assert resumed["prefetched_targets"] == 1 and resumed["fallback_targets"] == 5
            assert store.find("", "daily", "latest", params=source_params)["fetched_at"] == source["fetched_at"]
            again = prefetch(store, targets, access, whole_market=True, page_size=1)
            assert again["checkpoint_reused"] == 2 and again["actual_requests"] == 0
            assert all(store.result_of(target) != "empty" for target in targets)
            different_token = DataAccess(store, client=client, token="other", rate_limit_seconds=0)
            assert prefetch(store, targets, different_token, whole_market=True, page_size=1)["checkpoint_reused"] == 0
