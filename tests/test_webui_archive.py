# 覆盖需求：REQ-009.4（手动触发采集与长期存档）—— AC-4.1~AC-4.7
import hashlib
import json
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from webui.archive import (
    ArchiveBatch, ArchiveStore, classify_result, completeness, estimate_calls,
    resolve_token, targets_for_profile, token_fingerprint,
)
from webui.core.errors import NoToken, QuotaConfirmRequired
from webui.core.context import RequestContext
from webui.core.registry import build_registry
from webui.plugins.collect import contribute


class FakeAdapter:
    def __init__(self, values=None, *, fail_at=None):
        self.values = values or {}
        self.fail_at = fail_at
        self.calls = []

    def fetch(self, target):
        self.calls.append(target["dataset"])
        if self.fail_at == len(self.calls):
            raise KeyboardInterrupt()
        return self.values.get(target["dataset"], [{"value": 1}])


def _target(dataset="income", period="20260630"):
    return {"ticker": "600887.SH", "dataset": dataset, "period": period,
            "params": {"ts_code": "600887.SH", "period": period}}


def test_token_resolution_precedence_and_fingerprint(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text('TUSHARE_TOKEN="file-secret"\n', encoding="utf-8")
    assert resolve_token({}, dotenv) == "file-secret"
    assert resolve_token({"TUSHARE_TOKEN": "env-secret"}, dotenv) == "env-secret"
    assert token_fingerprint("env-secret") == hashlib.sha256(b"env-secret").hexdigest()[:8]


def test_profiles_expand_targets_and_estimate_calls():
    frugal = targets_for_profile(["600887.SH"], ["20260630", "20251231"], "frugal")
    bulk = targets_for_profile(["600887.SH"], ["20260630"], "bulk")
    assert estimate_calls(frugal) == len(frugal)
    assert len(bulk) > len(frugal)
    assert {target["period"] for target in frugal if target["dataset"] in
            ("income", "balancesheet", "cashflow", "fina_indicator")} == {"20260630"}
    assert not any(target["dataset"].startswith(("hk_", "us_")) for target in bulk)


def test_archive_persists_raw_payload_metadata_and_manifest(tmp_path):
    store = ArchiveStore(tmp_path / "archive", clock=lambda: datetime(2026, 9, 27, tzinfo=timezone.utc))
    target = _target()
    payload = [{"revenue": 42, "credential_echo": "secret"}]
    meta = store.save(target, payload, token_fingerprint="deadbeef", quota_profile="frugal", token="secret")
    data, read_meta = store.read(target)
    assert data == [{"revenue": 42, "credential_echo": "***"}]
    assert read_meta == meta
    safe_payload = [{"revenue": 42, "credential_echo": "***"}]
    assert meta["content_sha256"] == hashlib.sha256(
        json.dumps(safe_payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    assert meta["bytes"] > 0 and meta["token_fingerprint"] == "deadbeef"
    assert "secret" not in (tmp_path / "archive" / "600887.SH" / "income" / "20260630.json").read_text()
    assert "TUSHARE_TOKEN" not in (tmp_path / "archive" / "manifest.jsonl").read_text(encoding="utf-8")


def test_missing_token_stops_before_adapter_call(tmp_path):
    adapter = FakeAdapter()
    batch = ArchiveBatch(ArchiveStore(tmp_path / "archive"), [_target()], "frugal", adapter)
    with pytest.raises(NoToken):
        batch.run()
    assert adapter.calls == []


def test_bulk_requires_confirmation_before_fetch(tmp_path):
    adapter = FakeAdapter()
    batch = ArchiveBatch(ArchiveStore(tmp_path / "archive"), [_target()], "bulk", adapter, token="secret")
    with pytest.raises(QuotaConfirmRequired):
        batch.run()
    assert adapter.calls == []


def test_successful_archive_is_deduplicated_on_new_batch(tmp_path):
    store = ArchiveStore(tmp_path / "archive")
    target = _target()
    first = FakeAdapter()
    result = ArchiveBatch(store, [target], "frugal", first, token="secret").run(batch_id="first")
    second = FakeAdapter()
    again = ArchiveBatch(store, [target], "frugal", second, token="secret").run(batch_id="second")
    assert result["status"] == "done"
    assert again["usage"]["archive_hits"] == 1
    assert second.calls == []


def test_interrupted_batch_resumes_only_unfinished_targets(tmp_path):
    store = ArchiveStore(tmp_path / "archive")
    targets = [_target("income"), _target("balancesheet"), _target("cashflow")]
    adapter = FakeAdapter(fail_at=2)
    with pytest.raises(KeyboardInterrupt):
        ArchiveBatch(store, targets, "frugal", adapter, token="secret").run(batch_id="resume-me")
    saved = store.load_batch("resume-me")
    assert saved["status"] == "paused"
    saved["owner_pid"] = 999999999
    store.append_batch(saved)
    resumed = FakeAdapter()
    result = ArchiveBatch(store, targets, "frugal", resumed, token="secret").run(batch_id="resume-me")
    assert resumed.calls == ["balancesheet", "cashflow"]
    assert result["status"] == "done"
    assert result["usage"]["new_requests"] == 4


def test_gap_classification_and_completeness_preserve_permission_reason():
    kind, excerpt = classify_result(error=RuntimeError("抱歉，您没有接口访问权限"))
    assert (kind, excerpt) == ("no_permission", "抱歉，您没有接口访问权限")
    kind, _ = classify_result(error=RuntimeError("请求频率超限 429"))
    assert kind == "rate_limited"
    report = completeness([
        {"dataset": "income", "result": "ok"},
        {"dataset": "yc_cb", "ticker": "600887.SH", "period": "20260630",
         "result": kind, "error_excerpt": "请求频率超限 429"},
        {"dataset": "holding", "result": "no_permission", "error_excerpt": excerpt},
    ])
    assert report["counts"]["complete"] == 1
    assert report["counts"]["total"] == 3
    assert {gap["dataset"] for gap in report["gaps"]} == {"yc_cb", "holding"}


def test_archive_plugin_shows_completeness_and_machine_readable_gaps(tmp_path):
    root = tmp_path / "archive"
    store = ArchiveStore(root)
    store.save(_target("income"), [{"value": 1}], result="ok")
    denied = _target("yc_cb")
    store.save(denied, {"error": "无权限"}, result="no_permission", error_excerpt="无权限")
    registry = build_registry()
    contribute(registry)
    context = RequestContext("GET", "/", config=SimpleNamespace(archive_root=root), registry=registry)
    table = registry.panel_spec("collect.archive").provider(context)
    assert table["rows"] == [{"ticker": "600887.SH", "period": "20260630",
                              "completeness": "1/2", "permissions": 1,
                              "rate_limited": 0, "errors": 0}]
    response = registry.routes()[0].handler(context, ticker="600887.SH")
    assert response["data"]["counts"]["no_permission"] == 1
    assert response["data"]["gaps"][0]["dataset"] == "yc_cb"
