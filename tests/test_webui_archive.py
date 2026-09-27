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
from webui.core.errors import NoToken, QuotaConfirmRequired
from webui.core.context import RequestContext
from webui.core.registry import build_registry
from webui.render import panels
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


def test_archive_visual_panels_make_completeness_legible(tmp_path):
    """采集存档必须能「一眼看懂」：概览卡 + 覆盖条 + 缺口原因条 + 批次进度条。

    这些面板是使用者在实机体验里要的呈现方式（表格数字看不懂）。断言的是**看图能得到的结论**：
    完备度数、缺口颜色分段、各类缺口的相对长度、批次完成比例。
    """
    root = tmp_path / "archive"
    store = ArchiveStore(root)
    for name in ("income", "balancesheet", "cashflow", "fina_indicator"):
        store.save(_target(name), [{"value": 1}], result="ok")
    store.save(_target("empty_one"), [], result="empty")
    store.save(_target("yc_cb"), {"error": "x"}, result="no_permission",
               error_excerpt="抱歉，您没有接口(yc_cb)访问权限")
    store.save(_target("hk_daily"), {"error": "x"}, result="rate_limited",
               error_excerpt="请求频率超限 429")
    # 另一个期次：数据更差，必须被排到覆盖图最前面
    store.save(_target("income", "20251231"), [{"value": 1}], result="ok")
    store.save(_target("yc_cb", "20251231"), {"error": "x"}, result="no_permission",
               error_excerpt="抱歉，您没有接口(yc_cb)访问权限")
    store.save(_target("hk_daily", "20251231"), {"error": "x"}, result="error",
               error_excerpt="socket timeout")

    registry = build_registry()
    contribute(registry)
    context = RequestContext("GET", "/", config=SimpleNamespace(archive_root=root), registry=registry)

    def html(panel_id):
        spec = registry.panel_spec(panel_id)
        return panels.render_panel(spec, spec.provider(context))["html"]

    health = html("collect.health")
    assert "6/10 · 60%" in health, health          # ok×5 + empty×1 / 共 10
    assert "4 个" in health and "state-error" in health
    assert "无权限 2 · 频率超限 1 · 其他错误 1" in health

    coverage = html("collect.coverage")
    assert 'bar-denied' in coverage and 'bar-limited' in coverage and 'bar-ok' in coverage
    assert "已获取 5" in coverage and "无权限 2" in coverage        # 图例按（状态, 名称）求和
    first = coverage.index("bar-row")
    worst = coverage.index("bar-label", first)
    assert "20251231" in coverage[worst:worst + 200], "缺口多的（标的, 期次）必须排在前面"
    assert "600887.SH · 20251231" in coverage and "1/3 · 33%" in coverage
    assert "5/7 · 71%" in coverage

    reasons = html("collect.gap_reasons")
    assert "无权限" in reasons and "频率超限" in reasons and "其他错误" in reasons
    assert reasons.count("width:100%") == 1, "最大的一类缺口占满格"
    assert reasons.count("width:50%") == 2, "其余各类与最大类共用同一把尺子"
    assert "yc_cb" in reasons and "socket timeout" not in reasons   # 原文摘要留给明细表

    class DeniedAdapter:
        def fetch(self, target):
            if target["dataset"] == "yc_cb":
                raise RuntimeError("抱歉，您没有接口(yc_cb)访问权限")
            return [{"value": 1}]

    batch = ArchiveBatch(
        store, [_target("income"), _target("yc_cb")], "frugal", DeniedAdapter(),
        token="secret").run(batch_id="viz-batch", confirm=True)
    assert batch["status"] == "partial"
    progress = html("collect.batch_progress")
    assert "viz-batch" in progress
    assert "partial · 新增 1 · 命中 1 · 无权限 1 · 失败 0" in progress
    assert "width:100%" in progress
