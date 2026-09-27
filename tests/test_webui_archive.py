# 覆盖需求：REQ-009.4（手动触发采集与长期存档）—— AC-4.1~AC-4.7
import hashlib
import json
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from webui.archive import (
    ArchiveBatch, ArchiveStore, classify_result, completeness, estimate_calls,
    gap_targets, resolve_token, targets_for_profile, token_fingerprint,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
from webui.core.errors import NoToken, PathOutsideRoot, QuotaConfirmRequired
from webui.core.context import RequestContext
from webui.core.registry import build_registry
from webui.core.router import find_route
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

    # AC-4.1 的「不存在自动采集路径」判据（独立验收 S5）：采集只能由显式动作触发，
    # 框架里不许出现定时器 / 调度器 / 启动即采集。
    framework_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "scripts" / "webui").rglob("*.py"))
    )
    for banned in ("threading.Timer", "sched.scheduler", "apscheduler", "crontab", "schedule.every"):
        assert banned not in framework_source, f"框架里出现了自动触发路径：{banned}"


def test_bulk_requires_confirmation_before_fetch(tmp_path):
    adapter = FakeAdapter()
    batch = ArchiveBatch(ArchiveStore(tmp_path / "archive"), [_target()], "bulk", adapter, token="secret")
    with pytest.raises(QuotaConfirmRequired):
        batch.run()
    assert adapter.calls == []


def test_frugal_requires_confirmation_before_fetch(tmp_path):
    adapter = FakeAdapter()
    batch = ArchiveBatch(ArchiveStore(tmp_path / "archive"), [_target()], "frugal", adapter, token="secret")
    with pytest.raises(QuotaConfirmRequired, match="frugal"):
        batch.run()
    assert adapter.calls == []


def test_successful_archive_is_deduplicated_on_new_batch(tmp_path):
    store = ArchiveStore(tmp_path / "archive")
    target = _target()
    first = FakeAdapter()
    result = ArchiveBatch(store, [target], "frugal", first, token="secret").run(batch_id="first", confirm=True)
    second = FakeAdapter()
    again = ArchiveBatch(store, [target], "frugal", second, token="secret").run(batch_id="second", confirm=True)
    assert result["status"] == "done"
    assert again["usage"]["archive_hits"] == 1
    assert second.calls == []


def test_interrupted_batch_resumes_only_unfinished_targets(tmp_path):
    store = ArchiveStore(tmp_path / "archive")
    targets = [_target("income"), _target("balancesheet"), _target("cashflow")]
    adapter = FakeAdapter(fail_at=2)
    with pytest.raises(KeyboardInterrupt):
        ArchiveBatch(store, targets, "frugal", adapter, token="secret").run(
            batch_id="resume-me", confirm=True
        )
    saved = store.load_batch("resume-me")
    assert saved["status"] == "paused"
    saved["owner_pid"] = 999999999
    store.append_batch(saved)
    resumed = FakeAdapter()
    result = ArchiveBatch(store, targets, "frugal", resumed, token="secret").run(
        batch_id="resume-me", confirm=True
    )
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
    assert registry.has_panel("collect.batches")
    batches = registry.routes()[0].handler(context)
    assert batches["data"]["rows"] == []
    gaps_route = next(route for route in registry.routes() if route.template.endswith("/gaps"))
    response = gaps_route.handler(context, ticker="600887.SH")
    assert response["data"]["counts"]["no_permission"] == 1
    assert response["data"]["gaps"][0]["dataset"] == "yc_cb"

    # AC-4.6（独立验收缺口）：缺口原因必须**接到面板**上，不能只有一条没人引用的路由。
    gaps_panel = registry.panel_spec("collect.gaps").provider(context)
    assert gaps_panel["rows"] == [{"ticker": "600887.SH", "period": "20260630",
                                   "dataset": "yc_cb", "result": "no_permission",
                                   "reason": "无权限"}]
    assert gaps_panel["meta"] == {"complete": 1, "total": 2, "gaps": 1}

    # AC-4.6 另一半：只补缺口目标——已 ok/empty 的目标不再进批次。
    assert gap_targets([_target("income"), denied], store.result_of) == [denied]
    gap_batch = ArchiveBatch(store, gap_targets([_target("income"), denied], store.result_of),
                             "frugal", FakeAdapter(), token="secret").run(
        batch_id="gaps-only", confirm=True)
    assert gap_batch["progress"] == {"completed": 1, "total": 1}
    assert gap_batch["usage"]["new_requests"] == 1

    # AC-6（父需求独立验收 V1 抓到的真实越界读）：批次详情路由曾经能把编码斜杠
    # （`..%2f..%2f` 或 `%2f` 开头的绝对路径）解码进 `batch_id`，直接拼出存档根之外的
    # 任意 `*.json` 并原样回给 HTTP 面。这里**从路由匹配一路走到 handler**，因为漏洞的入口
    # 正是「先按 `([^/]+)` 匹配、之后才 unquote」这个顺序——只测 handler 会漏掉它。
    outside = tmp_path / "outside_archive.json"
    outside.write_text('{"marker": "read-from-outside-archive-root"}', encoding="utf-8")
    batches_dir = root / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    (batches_dir / "b-good.json").write_text('{"batch_id": "b-good"}', encoding="utf-8")
    detail = next(route for route in registry.routes() if route.template.endswith("/batches/{batch_id}"))

    assert detail.handler(context, batch_id="b-good")["data"] == {"batch_id": "b-good"}
    for attack in ("..%2f..%2foutside_archive", "%2fetc%2fpasswd"):
        route, params = find_route(registry.routes(), "GET", f"/api/v1/collect/batches/{attack}")
        assert route is not None, attack
        assert "/" in params["batch_id"], f"这条攻击串必须真的解码出斜杠才有意义：{attack}"
        with pytest.raises(PathOutsideRoot):
            route.handler(context, **params)
    # AC-6 第三款：指向存档根之外的符号链接同样拒绝（`safe_join` 按真实路径判定）。
    escape = batches_dir / "b-escape.json"
    try:
        escape.symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - 平台不支持符号链接
        pass
    else:
        with pytest.raises(PathOutsideRoot):
            detail.handler(context, batch_id="b-escape")
