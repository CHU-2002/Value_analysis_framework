# 覆盖需求：REQ-011.2（一次动作全量拉取与缺口补齐）—— AC-2.1 / AC-2.2 / AC-2.3
"""拉取编排的判据：按名单枚举目标、先预估后确认、去重/续跑/并发与四类计数、只补缺口。

本需求是 `REQ-009.4` 的**升级不是重写**（`docs/DATA_LAYER_PLAN.md` §7.1）：状态机与语义逐条
保留，所以这里的断言也逐条钉住「不得回退」的部分（`empty` 算完成、无权限不算、同批次不并发、
`KeyboardInterrupt` → `paused`）。

测试约定：

- 全部 `tmp_path`，**不联网**：真实 `TushareClient` 只换掉 `client.pro` 这一个远程对象，
  它是整个用例里唯一「会花钱」的东西，因此每个用例都断言它被真实调用的**次数**；
- **不 `time.sleep`**：`rate_limit_seconds=0`、`retry_delay=0`；
- `tests/conftest.py` 已把 `TURTLE_ARCHIVE_ROOT` 指向 `tmp_path`（防止写真实资产）。
"""

import functools
import json
import os
from collections import Counter

import pandas as pd
import pytest
from tushare_collector import TushareClient

from datalayer import endpoints, gaps, pull, registry
from datalayer.errors import BatchRunning, NoToken, QuotaConfirmRequired, UsageError
from datalayer.store import DataStore
from datalayer.universe import Universe
from webui.archive import quota as webui_quota

# 报告期型接口（可按期次逐期枚举）——`REQ-011` 的 `AC-4` 口径字段在注册表里声明。
REPORT_PERIOD_DATASETS = ("income", "balancesheet", "cashflow", "fina_indicator",
                          "fina_audit", "fina_mainbz")

# 带时间窗口的接口（`start_date` / `end_date` 由注册表的 `window` 声明算出来）。
WINDOW_APIS = ("daily", "weekly", "hk_daily", "yc_cb")


class _FakePro:
    """假的远程对象：记录每一次被真实调用的 ``(接口名, 入参)``，并按 ``responder`` 回答。

    它是「绝不重复花钱」的唯一判据——用例断言的是它的 ``calls``，不是实现里的计数器。
    """

    def __init__(self, responder=None):
        self.calls = []
        self.responder = responder

    def __getattr__(self, name):
        def call(**params):
            self.calls.append((name, params))
            return self.responder(name) if self.responder else pd.DataFrame({"value": [1]})
        return call


def _client(store, pro):
    """真实客户端的接线（`_access` / `MAX_RETRIES` / `_new_pro_api`）只把 `pro` 换成假的。"""

    client = TushareClient("test-token", store=store, rate_limit_seconds=0, retry_delay=0)
    client.pro = pro
    return client


def _batch(store, targets, pro, token="test-token"):
    client = _client(store, pro)
    return pull.PullBatch(store, targets, "frugal", client._access, token=token)


def _run(store, targets, pro, batch_id, **kwargs):
    return _batch(store, targets, pro).run(batch_id=batch_id, confirm=True, **kwargs)


@functools.lru_cache(maxsize=None)
def _targets(tickers, periods, profile):
    """`pull.targets_for` 是纯函数（不联网、不读仓），但内部逐个接口都会重扫代码；

    同一组合在多个用例里重复枚举一次要 1 秒以上，这里按组合缓存（返回值只读）。
    """

    return tuple(pull.targets_for(list(tickers), list(periods), profile))


def _targets_by_dataset(*datasets):
    """从 `frugal` 目标集合里按接口名挑目标（互不相同的接口 → 可分别安排结局）。"""

    pool = _targets(("600887.SH",), ("20260630",), "frugal")
    return [next(target for target in pool if target["dataset"] == name) for name in datasets]


def _sample_targets():
    return _targets_by_dataset("stock_basic", "daily", "income")


def _write_result(store, target, result, frame=None):
    """把一个目标手工写成「仓里已有记录」，用于构造部分完备的仓（不花钱）。"""

    store.write_frame(ticker=target["ticker"], dataset=target["dataset"],
                      period=target["period"], params=target["params"], frame=frame,
                      result=result,
                      error_excerpt="无权限" if result == "no_permission" else None)


# --------------------------------------------------------------------------- AC-2.1


def test_bulk_target_count_is_deterministic_for_fixed_list_and_periods():
    """AC-2.1：给定清单 + 档位 + 期次范围 → 目标条数与分组条数是确定值（纯计算）。"""

    one_period = _targets(("600887.SH",), ("20251231",), "bulk")
    two_periods = _targets(("600887.SH",), ("20251231", "20260630"), "bulk")
    assert pull.estimate(one_period, "bulk")["total"] == 17
    assert pull.estimate(two_periods, "bulk")["total"] == 25
    # 预估按数据集分组：报告期接口按期次逐期展开，非报告期接口只有一条 latest。
    by_dataset = pull.estimate(two_periods, "bulk")["by_dataset"]
    assert by_dataset["income"] == 4 and by_dataset["balancesheet"] == 4
    assert by_dataset["daily"] == 1 and by_dataset["yc_cb"] == 1


def test_target_count_tracks_profile_tier_and_list_size():
    """AC-2.1：{档位, 清单} 变化时目标条数随之变化（档位是选择器，市场要过滤）。"""

    periods = ("20251231", "20260630")
    frugal = _targets(("600887.SH",), periods, "frugal")
    bulk = _targets(("600887.SH",), periods, "bulk")
    assert len(frugal) == 8 and len(bulk) == 25 and len(frugal) < len(bulk)
    # 清单多一条 A 股标的：条数按标的数线性放大。
    two = _targets(("600887.SH", "000001.SZ"), periods, "bulk")
    assert len(two) == 2 * len(bulk) == 50
    # 市场过滤：港股标的只拿港股 + 通用接口，A 股专有的 stock_basic 与美股接口被排除。
    hk = _targets(("00700.HK",), periods, "bulk")
    assert len(hk) == 34
    assert not any(target["dataset"].startswith("us_") for target in hk)
    assert not any(target["dataset"] == "stock_basic" for target in hk)


def test_report_period_targets_double_when_period_range_grows():
    """AC-2.1：期次范围从 1 期变 2 期 → 报告期接口的目标条数翻倍；frugal 只取最新一期。"""

    def report_period_count(profile, periods):
        return sum(1 for target in _targets(("600887.SH",), tuple(periods), profile)
                   if target["dataset"] in REPORT_PERIOD_DATASETS)

    assert report_period_count("bulk", ["20251231"]) == 8
    assert report_period_count("bulk", ["20251231", "20260630"]) == 16
    # frugal 沿用 REQ-009.4 的口径（低配额档案不拉历史），不随期次范围放大。
    assert report_period_count("frugal", ["20251231"]) == 6
    assert report_period_count("frugal", ["20251231", "20260630"]) == 6


def test_scanned_endpoints_covered_by_bulk_profile_and_quota_table():
    """AC-2.1：接口清单从代码扫出来（D3）——档位覆盖扫描集合，两份档位表不漂移。"""

    scanned = set(endpoints.scan_safe_calls())
    assert scanned, "扫描不许退化成空集：那等于宣布没有接口要拉"
    assert not (scanned - set(registry.PROFILES["bulk"]))
    assert endpoints.missing_declarations() == []
    assert endpoints.param_conflicts() == []
    # registry 与 webui/archive/quota.py 是两份手写档位表：取值集合必须一致（顺序不是契约）。
    assert set(registry.PROFILES["bulk"]) == set(webui_quota.PROFILES["bulk"])
    assert set(registry.PROFILES["frugal"]) == set(webui_quota.PROFILES["frugal"])
    assert set(registry.PROFILES["frugal"]) < set(registry.PROFILES["bulk"])


def test_params_for_cover_every_scanned_endpoint_field_union():
    """AC-2.1：拉取参数覆盖代码请求——扫描到的每个接口，字段并集都被目标参数覆盖。"""

    scanned = endpoints.scan_safe_calls()
    assert scanned
    for api in scanned:
        params = pull.params_for(api, {})
        assert endpoints.fields_covered(api, params.get("fields")), api
        if endpoints.union_fields(api):
            assert params["fields"] == endpoints.union_fields(api), api


@pytest.mark.parametrize("api", WINDOW_APIS)
def test_time_window_endpoints_carry_start_and_end_dates(api):
    """AC-2.1：时间窗口型接口的目标参数带 start_date / end_date，字段仍是代码请求的并集。"""

    ticker = "00700.HK" if api == "hk_daily" else "600887.SH"
    target = next(item for item in _targets((ticker,), ("20260630",), "bulk")
                  if item["dataset"] == api)
    assert {"start_date", "end_date", "fields"} <= set(target["params"])
    assert target["params"]["fields"] == endpoints.union_fields(api)
    if api == "yc_cb":
        # 市场级接口把 ts_code 钉成字面量：目标标的必须照抄调用点，否则记录挂错标的、
        # 读取路径永远不命中（`pull.targets_for` 的注释）。
        assert target["ticker"] == "1001.CB" == target["params"]["ts_code"]


def test_plan_rejects_empty_universe_and_tickers_outside_list(tmp_path):
    """AC-2.1：清单为空、或 `--tickers` 给了清单外的标的 → 直接报错（不联网、不起批次）。"""

    store = DataStore(tmp_path / "store")
    universe = Universe(store)
    with pytest.raises(UsageError):
        pull.plan(universe, periods=["20260630"], store=store)
    universe.add("600887.SH", "伊利股份", tier="frugal")
    with pytest.raises(UsageError) as excinfo:
        pull.plan(universe, periods=["20260630"], store=store, tickers=["600000.SH"])
    assert "600000.SH" in str(excinfo.value)
    assert store.list_batches() == []


def test_unconfirmed_pull_requires_confirmation_without_remote_calls(tmp_path):
    """AC-2.1：先预估后确认——未确认（缺 `--yes`）→ QuotaConfirmRequired 且零远程调用。"""

    store = DataStore(tmp_path / "store")
    targets = _sample_targets()
    pro = _FakePro()
    batch = _batch(store, targets, pro)
    with pytest.raises(QuotaConfirmRequired) as excinfo:
        batch.run(batch_id="NOCONFIRM", confirm=False)
    assert str(len(targets)) in str(excinfo.value)   # 预估条数出现在确认提示里
    assert pro.calls == []
    assert store.load_batch("NOCONFIRM") is None


def test_pull_without_token_stops_before_any_remote_request(tmp_path):
    """AC-2.1（沿用 REQ-009.4 的 AC-4.1 语义）：没有 token → NoToken，且零次远程请求。"""

    store = DataStore(tmp_path / "store")
    targets = _sample_targets()
    pro = _FakePro()
    batch = _batch(store, targets, pro, token="")
    with pytest.raises(NoToken):
        batch.run(batch_id="NOTOKEN", confirm=True)
    assert pro.calls == []
    assert store.load_batch("NOTOKEN") is None


# --------------------------------------------------------------------------- AC-2.2


def test_first_batch_calls_each_target_once_and_reports_new_requests(tmp_path):
    """AC-2.2：第一次批次——每个目标恰好一次远程调用，新增请求数 = 目标数，状态 done。"""

    store = DataStore(tmp_path / "store")
    targets = _sample_targets()
    pro = _FakePro()
    batch = _run(store, targets, pro, "FIRST")
    assert [name for name, _ in pro.calls] == [target["dataset"] for target in targets]
    assert len(pro.calls) == len(targets)
    assert batch["usage"]["new_requests"] == len(targets)
    assert batch["usage"]["archive_hits"] == 0
    assert batch["status"] == "done"
    assert batch["progress"] == {"completed": len(targets), "total": len(targets)}
    assert all(store.result_of(target) == "ok" for target in targets)


def test_archive_dedup_and_resume_never_recharge_remote(tmp_path):
    """AC-2.2：已有记录默认跳过并计「命中存档」；同一批次续跑零远程调用。"""

    store = DataStore(tmp_path / "store")
    targets = _sample_targets()
    first = _run(store, targets, _FakePro(), "DEDUP")
    assert first["usage"]["new_requests"] == len(targets)

    # 换一个批次 id 重跑同一目标集合：全部命中存档，零远程调用。
    pro_fresh = _FakePro()
    fresh = _run(store, targets, pro_fresh, "DEDUP2")
    assert pro_fresh.calls == []
    assert fresh["usage"] == {"new_requests": 0, "archive_hits": len(targets),
                              "failures": 0, "no_permission": 0}
    assert fresh["status"] == "done"

    # 同一个批次 id 续跑（未加 force）：同样零远程调用。续跑路径把「本批次已完成」直接跳过，
    # 不重复记账——所以「新增请求数」不增长、`archive_hits` 也不计（它没为这批再花钱）。
    pro_resume = _FakePro()
    resumed = _run(store, targets, pro_resume, "DEDUP")
    assert pro_resume.calls == []
    assert resumed["usage"]["new_requests"] == len(targets)
    assert resumed["usage"]["archive_hits"] == 0
    assert len(resumed["completed"]) == len(targets)


def test_force_refetches_every_target(tmp_path):
    """AC-2.2：`force=True` 才重拉——每个目标都重新调用远程一次。"""

    store = DataStore(tmp_path / "store")
    targets = _sample_targets()
    _run(store, targets, _FakePro(), "FORCE")
    pro = _FakePro()
    forced = _run(store, targets, pro, "FORCE", force=True)
    assert [name for name, _ in pro.calls] == [target["dataset"] for target in targets]
    assert len(pro.calls) == len(targets)
    assert forced["status"] == "done"
    assert all(store.result_of(target) == "ok" for target in targets)


def test_keyboard_interrupt_pauses_batch_then_resume_pulls_only_remaining(tmp_path):
    """AC-2.2：`KeyboardInterrupt` → 批次落盘 `paused` 并重新抛出；重启只补未完成目标。"""

    store = DataStore(tmp_path / "store")
    targets = _sample_targets()
    seen = []

    def responder(name):
        seen.append(name)
        if len(seen) == len(targets):
            raise KeyboardInterrupt()
        return pd.DataFrame({"value": [1]})

    with pytest.raises(KeyboardInterrupt):
        _run(store, targets, _FakePro(responder), "RESUME")
    paused = store.load_batch("RESUME")
    assert paused["status"] == "paused"
    assert paused["owner_pid"] is None
    assert paused["progress"] == {"completed": len(targets) - 1, "total": len(targets)}
    assert not (store.batches_dir / "RESUME.lock").exists()

    pro = _FakePro()
    resumed = _run(store, targets, pro, "RESUME")
    assert [name for name, _ in pro.calls] == [targets[-1]["dataset"]]
    assert resumed["status"] == "done"
    assert resumed["progress"] == {"completed": len(targets), "total": len(targets)}
    assert len(resumed["completed"]) == len(targets)


def test_resume_keeps_batch_own_targets_and_ignores_added_ones(tmp_path):
    """AC-2.2：批次的目标集合由批次自己保存——续跑时传入更多目标不会扩大本批次。"""

    store = DataStore(tmp_path / "store")
    targets = _sample_targets()
    _run(store, targets, _FakePro(), "SCOPE")
    more = _targets_by_dataset("stock_basic", "daily", "income", "balancesheet", "cashflow")
    assert len(more) > len(targets)
    pro = _FakePro()
    resumed = _run(store, more, pro, "SCOPE")
    assert pro.calls == []
    assert resumed["targets"] == targets
    assert resumed["progress"] == {"completed": len(targets), "total": len(targets)}


def test_same_batch_rejects_concurrent_run_while_owner_pid_alive(tmp_path):
    """AC-2.2：同批次不允许并发——`running` 且 `owner_pid` 是活进程时再次 run → BatchRunning。"""

    store = DataStore(tmp_path / "store")
    targets = _sample_targets()
    store.append_batch({
        "batch_id": "CONC", "profile": "frugal", "targets": targets, "status": "running",
        "completed": {}, "created_at": "2026-01-01T00:00:00+00:00", "estimate": len(targets),
        "progress": {"completed": 0, "total": len(targets)},
        "usage": {"new_requests": 0, "archive_hits": 0}, "owner_pid": os.getpid(),
    })
    pro = _FakePro()
    with pytest.raises(BatchRunning):
        _run(store, targets, pro, "CONC")
    assert pro.calls == []
    assert not (store.batches_dir / "CONC.lock").exists()
    assert store.load_batch("CONC")["owner_pid"] == os.getpid()


def test_result_counts_split_no_permission_error_and_empty(tmp_path):
    """AC-2.2：四类计数——无权限 / 其它异常 / 真为空 / 成功各就各位，批次判 `partial`。"""

    store = DataStore(tmp_path / "store")
    targets = _sample_targets()

    def responder(name):
        if name == "stock_basic":
            return pd.DataFrame()                                   # 拉到了但确实为空
        if name == "daily":
            raise PermissionError("抱歉，您无权限访问该接口")          # 含「无权限」→ 永久错误
        raise ValueError("接口挂了")                                 # 其它异常 → 按上限重试

    pro = _FakePro(responder)
    client = _client(store, pro)
    batch = pull.PullBatch(store, targets, "frugal", client._access,
                           token="test-token").run(batch_id="COUNT", confirm=True)
    counts = batch["summary"]["counts"]
    assert counts["empty"] == 1 and counts["no_permission"] == 1 and counts["error"] == 1
    assert counts["total"] == len(targets) and counts["complete"] == 1
    assert batch["status"] == "partial"
    assert batch["usage"]["new_requests"] == len(targets)
    assert batch["usage"]["failures"] == 1 and batch["usage"]["no_permission"] == 1
    # 「绝不重复花钱」的直接判据：假对象被真实调用的次数。
    calls = Counter(name for name, _ in pro.calls)
    assert calls["stock_basic"] == 1               # 真空也是一次调用
    assert calls["daily"] == 1                     # 无权限：永久错误不重试
    assert calls["income"] == client.MAX_RETRIES   # 其它错误：按客户端重试上限重试
    # 每个目标的结局同时落进仓，与批次报告一致。
    assert store.result_of(targets[0]) == "empty"
    assert store.result_of(targets[1]) == "no_permission"
    assert store.result_of(targets[2]) == "error"


def test_batch_progress_double_written_to_catalog_and_compat_json(tmp_path):
    """AC-2.2（兼容期双写）：`batches/<id>.json` 与仓内批次快照同源，GUI 读路径不变。"""

    store = DataStore(tmp_path / "store")
    targets = _sample_targets()
    batch = _run(store, targets, _FakePro(), "DOUBLE")
    path = store.batches_dir / "DOUBLE.json"
    assert path.is_file()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    loaded = store.load_batch("DOUBLE")
    # GUI（`plugins/collect.py` 的三个面板）与 CLI 读的字段：两处必须一致。
    for key in ("batch_id", "profile", "status", "created_at", "heartbeat_at", "owner_pid",
                "estimate", "targets", "progress", "usage", "summary"):
        assert loaded[key] == on_disk[key], key
    assert loaded["batch_id"] == batch["batch_id"] == "DOUBLE"
    assert loaded["progress"] == {"completed": len(targets), "total": len(targets)}
    assert loaded["usage"]["new_requests"] == len(targets)
    # 逐目标结果**逐字段**同源（含 `params`）：兼容期双写不能只对齐 GUI 现在读的那几列，
    # 否则「切回旧路径」时才发现仓里的批次快照缺字段。
    assert loaded["completed"] == on_disk["completed"]


# --------------------------------------------------------------------------- AC-2.3


def test_only_gaps_plan_reports_requested_count_and_gap_total(tmp_path):
    """AC-2.3：`only_gaps` 的 total 只反映缺口，requested 仍是原目标数。"""

    store = DataStore(tmp_path / "store")
    universe = Universe(store)
    universe.add("600887.SH", "伊利股份", tier="frugal")
    targets = pull.plan(universe, periods=["20260630"], store=store)["targets"]
    assert len(targets) == 8
    _write_result(store, targets[0], "ok", frame=pd.DataFrame({"value": [1]}))
    _write_result(store, targets[1], "empty")

    partial = pull.plan(universe, periods=["20260630"], store=store, only_gaps=True)
    assert partial["estimate"]["requested"] == 8
    assert partial["estimate"]["total"] == 6
    assert partial["estimate"]["skipped_complete"] == 2
    assert partial["targets"] == gaps.gap_targets(targets, store.result_of)


def test_gap_pull_converges_completeness_to_all_targets(tmp_path):
    """AC-2.3：按缺口发起补齐批次后完备度收敛——缺口拉完，`only_gaps` 的 total 归零。"""

    store = DataStore(tmp_path / "store")
    universe = Universe(store)
    universe.add("600887.SH", "伊利股份", tier="frugal")
    targets = pull.plan(universe, periods=["20260630"], store=store)["targets"]
    for target in targets[:3]:
        _write_result(store, target, "ok", frame=pd.DataFrame({"value": [1]}))

    before = gaps.completeness_by_targets(targets, store.result_of)["counts"]
    assert before["complete"] == 3 and before["error"] == 5   # error = 「还没拉过」的缺口
    gap_plan = pull.plan(universe, periods=["20260630"], store=store, only_gaps=True)
    assert gap_plan["estimate"]["total"] == 5

    pro = _FakePro()
    batch = _run(store, gap_plan["targets"], pro, "GAPS")
    assert len(pro.calls) == 5            # --only-gaps 只对缺口花钱
    assert batch["status"] == "done"

    after = pull.plan(universe, periods=["20260630"], store=store, only_gaps=True)
    assert after["estimate"]["total"] == 0
    final = gaps.completeness_by_targets(targets, store.result_of)["counts"]
    assert final["complete"] == len(targets) == 8


def test_only_gaps_keeps_empty_as_done_but_no_permission_as_gap(tmp_path):
    """AC-2.3（不得回退 REQ-009.4 的 AC-4.6）：`ok` 与 `empty` 算完成，`no_permission` 不算。"""

    store = DataStore(tmp_path / "store")
    universe = Universe(store)
    universe.add("600887.SH", "伊利股份", tier="frugal")
    targets = pull.plan(universe, periods=["20260630"], store=store)["targets"]
    _write_result(store, targets[0], "ok", frame=pd.DataFrame({"value": [1]}))
    _write_result(store, targets[1], "empty")
    _write_result(store, targets[2], "no_permission")

    remaining = gaps.gap_targets(targets, store.result_of)
    assert targets[2] in remaining
    assert targets[0] not in remaining and targets[1] not in remaining
    counts = gaps.completeness_by_targets(targets, store.result_of)["counts"]
    assert counts["complete"] == 2            # empty 计入完成
    assert counts["no_permission"] == 1       # 无权限永远不算完成
    partial = pull.plan(universe, periods=["20260630"], store=store, only_gaps=True)
    assert partial["estimate"]["total"] == 6  # 8 - 2，无权限那条仍在缺口里
    assert partial["estimate"]["skipped_complete"] == 2
