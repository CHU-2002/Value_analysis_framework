# 覆盖需求：REQ-011.1
"""REQ-011.1「自选股清单与统一原始仓」—— AC-1.1 ~ AC-1.4。

对应父需求 `REQ-011` 的 `AC-1` / `AC-3` / `AC-4` / `AC-8`，设计见 `docs/DATA_LAYER_PLAN.md`
§4（存储模型）、§5（标的宇宙）、§6.3（DataFrame 往返契约）、§9（迁移）、§13（安全）。

约定：仓与旧存档全部在 `tmp_path` 下现造（`tests/conftest.py` 的 autouse fixture 已把
`TURTLE_ARCHIVE_ROOT` 指到 tmp_path）；时间一律注入固定 clock（不 `sleep`）；不联网、
不用真实 token；不读真实 `output/` 与真实 `~/turtle_archive`。

**独立复核提出的三处已在同一交付里修掉**（回归断言就在本文件里）：

1. `legacy.import_collector_cache` 曾用 `path.stem.partition("_")` 拆「数据集_标的」，而
   `stock_basic` / `hk_basic` / `us_basic` 的名字自带下划线 → 条目静默落进「跳过」分支。
   现在按**已知数据集前缀**匹配，由
   `test_collector_cache_entries_import_once_and_idempotently` 钉住。
2. `us_daily_all.parquet` 曾因造出 `ticker=""` 而被 `_normalize` 拒绝（真实导入必崩）。
   现在空 ticker 是**合法**的「全市场快照」（`us_daily` 的调用点本来就不传 ts_code），
   同一条用例覆盖。
3. `datalayer/config.py::default_periods` 曾自带 `f"{year}1231"` 字面量；现在走
   `periods.make_period` + `period_to_end_date`，期次换算仍然只有一处权威。
"""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

import datalayer
from datalayer import PERIOD_TYPES, RESULT_KINDS, SHAPES, endpoints, legacy, pull, registry
from datalayer.access import DataAccess, DataUnavailable
from datalayer.config import default_periods
from datalayer.dataframe_codec import decode_frame, encode_frame
from datalayer.errors import UniverseError
from datalayer.registry import PERIOD_REPORT, SNAPSHOT, TIMESERIES, describe, period_of
from datalayer.security import token_fingerprint
from datalayer.store import DataStore, param_key
from datalayer.universe import Universe
from periods import end_date_to_period_type, is_end_date, make_period, period_to_end_date

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
DATALAYER_DIR = SCRIPTS_DIR / "datalayer"
COLLECTOR_MODULE = SCRIPTS_DIR / "tushare_collector.py"

# 固定时钟：所有时间断言都不依赖真实时间（也不 `sleep`）。
FIXED_NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _clock():
    return FIXED_NOW


def _frame(value=1.5, period="20260630"):
    return pd.DataFrame({"end_date": [period], "revenue": [value]})


def _params(ticker="600887.SH", period="20260630", **extra):
    return {"ts_code": ticker, "period": period, **extra}


@pytest.fixture
def store(tmp_path):
    """仓根在 tmp_path 下、clock 固定的 `DataStore`。"""

    instance = DataStore(tmp_path / "archive", clock=_clock)
    try:
        yield instance
    finally:
        instance.close()


# --------------------------------------------------------------------------- AC-1.1


def test_universe_lifecycle_and_derived_market_and_tier(store):
    """AC-1.1：清单可读可改（增删改查），市场与档位由标的/参数推导。"""

    universe = Universe(store)
    created = universe.add("600887.sh", "伊利股份")
    assert created["created"] is True
    entry = created["entry"]
    assert (entry["ticker"], entry["market"], entry["tier"], entry["enabled"]) == \
        ("600887.SH", "SH", "bulk", True)

    universe.add("00700.HK", "腾讯控股", tier="frugal", note="港股")
    universe.add("AAPL", "苹果")  # 无后缀的字母代码 → US
    assert universe.tickers() == ["00700.HK", "600887.SH", "AAPL"]
    assert universe.tiers() == {"frugal": ["00700.HK"], "bulk": ["600887.SH", "AAPL"]}

    updated = universe.update("600887.SH", display_name="伊利", note="核心持仓")
    assert updated["display_name"] == "伊利"
    assert universe.get("600887.SH")["note"] == "核心持仓"

    assert universe.set_enabled("600887.SH", False)["enabled"] is False
    assert universe.tickers() == ["00700.HK", "AAPL"]  # 停用的不进目标集合
    assert len(universe.entries()) == 3  # 但清单里仍然查得到
    assert [item["ticker"] for item in universe.entries(tier="frugal")] == ["00700.HK"]

    assert universe.remove("00700.HK") is True
    assert universe.remove("00700.HK") is False
    assert universe.get("00700.HK") is None
    assert len(universe.entries()) == 2


def test_universe_add_requires_display_name(store):
    """AC-1.1 失败路径：缺显示名必须报错，且 `UniverseError` 是 `ValueError` 子类。"""

    universe = Universe(store)
    assert issubclass(UniverseError, ValueError)
    with pytest.raises(UniverseError) as failure:
        universe.add("600887.SH", "   ")
    assert isinstance(failure.value, ValueError)
    with pytest.raises(UniverseError):
        universe.add("600887.SH", None)
    assert universe.entries() == []


def test_universe_add_rejects_unknown_market_and_tier(store):
    """AC-1.1 失败路径：未知市场后缀 / 未知档位 / 非法代码都不得写进清单。"""

    universe = Universe(store)
    with pytest.raises(UniverseError):
        universe.add("600887.XX", "未知后缀")
    with pytest.raises(UniverseError):
        universe.add("600887.SH", "未知档位", tier="golden")
    with pytest.raises(UniverseError):
        universe.add("600;887", "非法代码")
    assert universe.entries() == []


def test_universe_update_rejects_unknown_field_and_absent_ticker(store):
    """AC-1.1 失败路径：改不存在的标的或用清单不认的字段，都必须报错且不改动清单。"""

    universe = Universe(store)
    universe.add("600887.SH", "伊利股份")
    with pytest.raises(UniverseError):
        universe.update("600887.SH", bogus=1)
    with pytest.raises(UniverseError):
        universe.update("999999.SZ", display_name="不存在的标的")
    assert universe.get("600887.SH")["display_name"] == "伊利股份"
    assert len(universe.entries()) == 1


def test_universe_disabled_ticker_drops_out_of_tickers_and_targets(store):
    """AC-1.1：停用的标的不进 `tickers()`，也不进按清单枚举的目标集合。"""

    universe = Universe(store)
    universe.add("600887.SH", "伊利股份")
    universe.add("00700.HK", "腾讯控股", tier="frugal")
    universe.set_enabled("600887.SH", False)

    assert universe.tickers() == ["00700.HK"]
    assert universe.tiers() == {"frugal": ["00700.HK"]}

    plan = pull.plan(universe, periods=["20251231"], profile="bulk")
    called = {str(target["params"].get("ts_code")) for target in plan["targets"]}
    assert "600887.SH" not in called  # 停用标的不会出现在任何一次调用里
    assert "00700.HK" in called  # 启用标的仍然在
    assert plan["estimate"]["total"] == len(plan["targets"]) > 0


def test_adding_ticker_changes_planned_total_without_code_change(store):
    """AC-1.1：目标总条数由清单推导——加一个标的就翻倍，不改任何代码常量。"""

    universe = Universe(store)
    universe.add("600887.SH", "伊利股份")
    before = pull.plan(universe, periods=["20251231", "20250630"], profile="bulk")
    universe.add("000858.SZ", "五粮液")
    after = pull.plan(universe, periods=["20251231", "20250630"], profile="bulk")

    assert before["estimate"]["total"] > 0
    assert after["estimate"]["total"] == 2 * before["estimate"]["total"]
    assert after["estimate"]["total"] == len(after["targets"])
    before_calls = {str(target["params"].get("ts_code")) for target in before["targets"]}
    after_calls = {str(target["params"].get("ts_code")) for target in after["targets"]}
    assert "000858.SZ" not in before_calls
    assert "000858.SZ" in after_calls and before_calls <= after_calls


def test_suggest_from_output_only_suggests_and_never_writes(store, tmp_path):
    """AC-1.1：`suggest_from_output` 只给建议，不自动写入清单（清单是使用者的资产）。"""

    output_root = tmp_path / "output"
    (output_root / "600887_伊利").mkdir(parents=True)
    (output_root / "600887_伊利" / "record.json").write_text(
        json.dumps({"subject": {"ticker": "600887.SH"}}), encoding="utf-8")
    (output_root / "000858_五粮液").mkdir()  # 无 record.json，目录名推不出市场 → 不入选

    universe = Universe(store)
    suggestions = universe.suggest_from_output(output_root)
    assert [item["ticker"] for item in suggestions] == ["600887.SH"]
    assert (suggestions[0]["display_name"], suggestions[0]["market"]) == ("伊利", "SH")
    assert universe.entries() == []  # 建议不落盘


# --------------------------------------------------------------------------- AC-1.2


def test_same_key_write_is_unique_and_replace_refreshes_content_sha(store):
    """AC-1.2：同一 (标的, 数据集, 期次, 参数) 只有一条；覆盖写刷新内容摘要与内容。"""

    params = _params()
    first = store.write_frame(ticker="600887.SH", dataset="income", period="20260630",
                              params=params, frame=_frame(1.5), result="ok")
    assert first.created is True

    second = store.write_frame(ticker="600887.SH", dataset="income", period="20260630",
                               params=params, frame=_frame(2.5), result="ok")
    assert second.created is False
    assert len(store.records()) == 1
    stored = store.records()[0]
    assert stored["content_sha256"] == second.record["content_sha256"]
    assert stored["content_sha256"] != first.record["content_sha256"]
    kept = store.find("600887.SH", "income", "20260630", params=params)
    assert json.loads(kept["rows_json"])[0]["revenue"] == 2.5

    variant = store.write_frame(ticker="600887.SH", dataset="income", period="20260630",
                                params=_params(report_type="6"), frame=_frame(1.5),
                                result="ok")
    assert variant.created is True
    assert len(store.records()) == 2  # 参数不同 = 不同的唯一键


def test_save_raw_skip_keeps_existing_content(store):
    """AC-1.2：`on_conflict="skip"` 必须报 `skipped=True`，且一个字节都不覆盖。"""

    params = _params()
    first = store.write_frame(ticker="600887.SH", dataset="income", period="20260630",
                              params=params, frame=_frame(1.5), result="ok")
    skipped = store.write_frame(ticker="600887.SH", dataset="income", period="20260630",
                                params=params, frame=_frame(9.9), result="ok",
                                on_conflict="skip")

    assert (skipped.skipped, skipped.created) == (True, False)
    assert skipped.record["rows_json"] == first.record["rows_json"]
    assert len(store.records()) == 1
    kept = store.find("600887.SH", "income", "20260630", params=params)
    assert json.loads(kept["rows_json"])[0]["revenue"] == 1.5


def test_record_carries_every_field_required_by_ac3(store):
    """AC-1.2：一条记录至少带 `AC-3` 列出的全部字段，且取值落在声明的枚举里。"""

    record = store.write_frame(
        ticker="600887.SH", dataset="income", period="20260630", params=_params(),
        frame=_frame(), result="ok", token_fingerprint=token_fingerprint("fake-token"),
        tier_label="年包", quota_profile="frugal", batch_id="b-1").record

    for field in ("fetched_at", "result", "error_excerpt", "content_sha256", "bytes",
                  "token_fingerprint", "tier_label", "framework_version", "shape",
                  "period_type", "cumulative", "quota_profile", "batch_id"):
        assert field in record, field
    assert record["fetched_at"] == FIXED_NOW.isoformat()
    assert record["framework_version"] == datalayer.__version__
    assert record["token_fingerprint"] == token_fingerprint("fake-token")
    assert record["result"] in RESULT_KINDS
    assert record["shape"] in SHAPES
    assert record["period_type"] in PERIOD_TYPES
    assert record["cumulative"] is True  # 利润表是累计口径
    assert record["bytes"] > 0 and len(record["content_sha256"]) == 64
    assert isinstance(store.frame(record), pd.DataFrame)


def test_manifest_jsonl_is_append_only_and_line_parsable(store):
    """AC-1.2：`manifest.jsonl` 逐条追加、每行可 JSON 解析，历史行不被改写。"""

    store.write_frame(ticker="600887.SH", dataset="income", period="20251231",
                      params=_params(period="20251231"), frame=_frame(1.0, "20251231"),
                      result="ok")
    after_first = store.manifest_path.read_text(encoding="utf-8").splitlines()
    assert len(after_first) == 1

    store.write_frame(ticker="600887.SH", dataset="income", period="20250630",
                      params=_params(period="20250630"), frame=_frame(1.0, "20250630"),
                      result="ok")
    lines = store.manifest_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] == after_first[0]  # 只追加，不改写历史

    entries = [json.loads(line) for line in lines]
    assert {entry["period"] for entry in entries} == {"20251231", "20250630"}
    assert {entry["dataset"] for entry in entries} == {"income"}
    assert all(entry["content_sha256"] and entry["bytes"] > 0 for entry in entries)


def test_token_never_reaches_store_bytes_and_error_excerpt_is_redacted(store):
    """AC-1.2 / §13：凭据不入仓（参数、错误摘要、db 字节），只留指纹。"""

    token = "secret"
    saved = store.write_frame(
        ticker="600887.SH", dataset="income", period="20260630",
        params={**_params(), "token": token}, frame=_frame(), result="ok",
        token_fingerprint=token_fingerprint(token))
    assert "token" not in saved.record["params"]
    assert token not in saved.record["params_json"]
    assert token not in saved.record["param_key"]
    assert saved.record["token_fingerprint"] == token_fingerprint(token) != token

    class _FailingPro:
        def income(self, **kwargs):
            raise RuntimeError(f"token {token} rejected by income")

    class _Client:
        def __init__(self):
            self.pro = _FailingPro()
            self.MAX_RETRIES = 1

    access = DataAccess(store, client=_Client(), token=token, mode="online",
                        rate_limit_seconds=0, retry_delay=0)
    with pytest.raises(DataUnavailable):
        access.call("income", ts_code="000858.SZ", period="20260630")

    failures = store.records(result="error")
    assert len(failures) == 1
    assert token not in (failures[0]["error_excerpt"] or "")
    assert "***" in failures[0]["error_excerpt"]

    store.close()
    blobs = b"".join(path.read_bytes() for path in store.root.rglob("store.db*"))
    assert token.encode() not in blobs
    assert token not in store.manifest_path.read_text(encoding="utf-8")


def test_invalid_result_enum_or_shape_is_rejected_without_half_record(store):
    """AC-1.2 失败路径：非法枚举抛错且不留半条记录；声明的枚举都写得进去。"""

    base = {"ticker": "600887.SH", "dataset": "income", "period": "20260630",
            "params": _params()}
    with pytest.raises(ValueError):
        store.save_raw({**base, "result": "banana"})
    with pytest.raises(ValueError):
        store.save_raw({**base, "result": "ok", "shape": "blob"})
    assert store.records() == []  # 拒绝写入时不落半条记录

    periods = ["20250331", "20250630", "20250930", "20251231", "20241231"]
    for kind, period in zip(RESULT_KINDS, periods):
        store.write_frame(ticker="600887.SH", dataset="income", period=period,
                          params=_params(period=period), frame=_frame(period=period),
                          result=kind)
    assert {record["result"] for record in store.records()} == set(RESULT_KINDS)
    assert {record["shape"] for record in store.records()} == {PERIOD_REPORT}
    assert {record["period_type"] for record in store.records()} <= set(PERIOD_TYPES)


def test_wipe_requires_confirmation_and_leaves_audit_trace(store):
    """AC-1.2 / AC-3 资产语义：删除必须二次确认；确认后清空并在清单里留痕。"""

    store.write_frame(ticker="600887.SH", dataset="income", period="20260630",
                      params=_params(), frame=_frame(), result="ok")
    with pytest.raises(ValueError):
        store.wipe("wipe")
    assert len(store.records()) == 1  # 没确认就不许删

    entry = store.wipe("WIPE")
    assert entry["records"] == 1
    assert entry["at"] == FIXED_NOW.isoformat()
    assert store.records() == [] and store.stats()["records"] == 0

    audit = [json.loads(line) for line in
             store.manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(item.get("schema") == "datalayer.wipe" and item["records"] == 1
               for item in audit)
    assert any(item.get("schema") == "datalayer.raw_record" for item in audit)


# --------------------------------------------------------------------------- AC-1.3


def test_describe_derives_structured_period_semantics():
    """AC-1.3：口径字段结构化——年度/半年/单季/时点与累计语义可断言。"""

    assert describe("income", "20260630") == (PERIOD_REPORT, "half", True)
    assert describe("cashflow", "20251231") == (PERIOD_REPORT, "annual", True)
    assert describe("balancesheet", "20260630") == (PERIOD_REPORT, "half", False)
    assert describe("balancesheet", "20250331") == (PERIOD_REPORT, "quarter", False)
    assert describe("daily", "latest") == (TIMESERIES, "series", False)
    assert describe("stock_basic", "latest") == (SNAPSHOT, "point", False)


def test_records_filter_splits_annual_and_quarter_series(store):
    """AC-1.3：给定记录集能按口径筛出「仅年度」与「仅单季」两组序列。"""

    for period in ("20251231", "20250630", "20250331", "20250930"):
        store.write_frame(ticker="600887.SH", dataset="income", period=period,
                          params=_params(period=period), frame=_frame(period=period),
                          result="ok")

    annual = store.records(period_type="annual")
    quarter = store.records(period_type="quarter")
    half = store.records(period_type="half")
    assert {record["period"] for record in annual} == {"20251231"}
    assert {record["period"] for record in quarter} == {"20250331", "20250930"}
    assert {record["period"] for record in half} == {"20250630"}
    assert [record["cumulative"] for record in annual + quarter] == [True] * 3
    assert not ({record["period"] for record in annual}
                & {record["period"] for record in quarter})


def test_period_of_enumerated_datasets_and_latest():
    """AC-1.3：只有按期次枚举的接口用参数里的期次；其余接口恒为 `latest`。"""

    assert period_of("income", {"period": "20260630"}) == "20260630"
    assert period_of("income", {}) == "latest"
    assert period_of("daily", {"period": "20260630"}) == "latest"
    assert period_of("stock_basic", {"ts_code": "600887.SH"}) == "latest"


# 「月日 → 项目期次/口径」的换算字面量：只允许出现在 `scripts/periods.py`。
_MONTH_DAY_LITERALS = ("0331", "0630", "0930", "1231")
_END_DATE_PATTERN = r"\d{4}\d{2}\d{2}"


def _period_conversion_offenders(path):
    """扫出 datalayer 里**自成一套**的期次换算：映射表 / 月日比较 / 后缀探测 / 期次正则。"""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and str(key.value) in _MONTH_DAY_LITERALS:
                    offenders.append((path.name, node.lineno, "月日映射表"))
        if isinstance(node, ast.Compare):
            for item in (node.left, *node.comparators):
                if isinstance(item, ast.Constant) and str(item.value) in _MONTH_DAY_LITERALS:
                    offenders.append((path.name, node.lineno, "月日比较"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("endswith", "startswith"):
            for argument in node.args:
                if isinstance(argument, ast.Constant) and any(
                        literal in str(argument.value) for literal in _MONTH_DAY_LITERALS):
                    offenders.append((path.name, node.lineno, "月日后缀探测"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "re":
            pattern = "".join(str(argument.value) for argument in node.args
                              if isinstance(argument, ast.Constant))
            if _END_DATE_PATTERN in pattern or any(
                    literal in pattern for literal in _MONTH_DAY_LITERALS):
                offenders.append((path.name, node.lineno, "期次正则"))
    return offenders


def test_datalayer_has_no_second_period_conversion_authority():
    """AC-1.3 失败路径：`datalayer` 不得自带第二套期次正则/月份映射，换算只来自 periods。"""

    assert registry.end_date_to_period_type is end_date_to_period_type

    offenders = []
    for path in sorted(DATALAYER_DIR.glob("*.py")):
        offenders.extend(_period_conversion_offenders(path))
    assert offenders == [], f"datalayer 里出现了第二套期次换算：{offenders}"

    # 行为锚点：默认期次范围必须仍是 `periods.py` 认得出的年报期末（构造不许漂移）。
    today = date(2026, 9, 28)
    derived = default_periods(today=today, years=3)
    assert derived == [period_to_end_date(make_period(year, "年报"))
                       for year in (2026, 2025, 2024)]
    assert all(is_end_date(period) and end_date_to_period_type(period) == "annual"
               for period in derived)


# --------------------------------------------------------------------------- AC-1.4


def _write_legacy_archive(root, *, payload, ticker="600887.SH", dataset="income",
                          period="20260630"):
    """按 `scripts/webui/archive/store.py::save` 的格式造一份旧存档（含 manifest.jsonl）。"""

    data_path = root / ticker / dataset / f"{period}.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    entry = {
        "schema": "webui.archive.record", "schema_version": "1.0", "dataset": dataset,
        "ticker": ticker, "period": period,
        "api": {"name": dataset, "params": _params(ticker, period)},
        "fetched_at": "2026-09-01T00:00:00+00:00", "token_fingerprint": "deadbeef",
        "tier_label": "年包", "quota_profile": "frugal", "framework_version": "1.0.0",
        "result": "ok", "error_excerpt": None,
        "content_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
        "bytes": data_path.stat().st_size,
        "file": data_path.relative_to(root).as_posix(),
    }
    with (root / "manifest.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return data_path


def test_legacy_import_is_idempotent(store, tmp_path):
    """AC-1.4：第一次有导入计数，第二次全部跳过；迁移报告落盘留证。"""

    archive = tmp_path / "old_archive"
    _write_legacy_archive(archive, payload=[
        {"ts_code": "600887.SH", "end_date": "20260630", "revenue": 1.5}])

    first = legacy.import_all(store, archive_root=archive, cache_dir=tmp_path / "no_cache")
    assert first["imported"] > 0
    assert first["conflicts"] == 0
    imported = store.records()[0]
    assert imported["fetched_at"] == "2026-09-01T00:00:00+00:00"
    assert imported["tier_label"] == "年包"
    after_first = len(store.records())

    second = legacy.import_all(store, archive_root=archive, cache_dir=tmp_path / "no_cache")
    assert second["imported"] == 0
    assert second["skipped"] > 0
    assert len(store.records()) == after_first
    assert json.loads(store.meta_get("migration_last_report"))["imported"] == 0


def test_legacy_conflict_is_reported_then_force_overwrites(store, tmp_path):
    """AC-1.4：同键不同内容记为冲突且不覆盖；显式 force 才覆盖（覆盖后仍是一条）。"""

    archive = tmp_path / "old_archive"
    data_path = _write_legacy_archive(archive, payload=[
        {"ts_code": "600887.SH", "end_date": "20260630", "revenue": 1.5}])
    legacy.import_archive(store, archive)
    before = store.records()[0]["content_sha256"]

    data_path.write_text(json.dumps(
        [{"ts_code": "600887.SH", "end_date": "20260630", "revenue": 9.9}]),
        encoding="utf-8")
    report = legacy.import_archive(store, archive)
    assert report["conflicts"] == 1 and report["imported"] == 0
    assert store.records()[0]["content_sha256"] == before
    rows = json.loads(store.records(include_rows=True)[0]["rows_json"])
    assert rows[0]["revenue"] == 1.5  # 冲突不覆盖

    forced = legacy.import_archive(store, archive, force=True)
    assert forced["imported"] == 1
    assert len(store.records()) == 1
    assert store.records()[0]["content_sha256"] != before
    rows = json.loads(store.records(include_rows=True)[0]["rows_json"])
    assert rows[0]["revenue"] == 9.9


def test_legacy_dry_run_leaves_store_untouched(store, tmp_path):
    """AC-1.4：`dry_run` 只报账不落仓；`us_daily_all.parquet` 被认成 `us_daily` 快照。"""

    archive = tmp_path / "old_archive"
    _write_legacy_archive(archive, payload=[
        {"ts_code": "600887.SH", "end_date": "20260630", "revenue": 1.5}])
    cache = tmp_path / "collector_cache"
    cache.mkdir()
    (cache / "stock_basic_600887.SH.json").write_text(
        json.dumps([{"ts_code": "600887.SH", "name": "伊利股份"}]), encoding="utf-8")
    pd.DataFrame({"ts_code": ["AAPL", "MSFT"], "close": [1.0, 2.0]}).to_parquet(
        cache / "us_daily_all.parquet")

    report = legacy.import_all(store, archive_root=archive, cache_dir=cache, dry_run=True)
    assert report["imported"] > 0
    assert store.records() == [] and store.stats()["records"] == 0
    assert store.meta_get("migration_last_report") is None  # 试跑不写迁移台账

    parquet = [item for item in report["details"]
               if item.get("origin") == "us_daily_all.parquet"]
    assert parquet, report["details"]
    assert parquet[0]["dataset"] == "us_daily"
    assert parquet[0]["action"] == "would-import"


# ------------------------------------------------------- 额外：往返契约 / 扫描 / 计数

_CODEC_FRAMES = [
    pytest.param(pd.DataFrame({"end_date": ["20260630"], "revenue": [1.5], "count": [3]}),
                 id="整数列与浮点列"),
    pytest.param(pd.DataFrame({"ts_code": ["600887.SH", "000858.SZ"],
                               "name": ["伊利", "五粮液"]}), id="字符串列"),
    pytest.param(pd.DataFrame({"revenue": pd.Series([], dtype="float64")}), id="空表"),
    pytest.param(pd.DataFrame({"revenue": [1.0, float("nan")], "ts_code": ["A", "B"]}),
                 id="含 NaN 的列"),
]


@pytest.mark.parametrize("frame", _CODEC_FRAMES)
def test_frame_codec_roundtrip_preserves_contract(frame):
    """§6.3 / AC-1.2：仓内 JSON 行往返后 dtype 契约不变（含空表与 NaN 列）。"""

    encoded = encode_frame(frame)
    decoded = decode_frame(encoded["columns_json"], encoded["rows_json"])
    assert list(decoded.columns) == list(frame.columns)
    assert_frame_equal(decoded, frame)
    assert encoded["content_sha256"] == hashlib.sha256(
        encoded["rows_json"].encode("utf-8")).hexdigest()
    assert encoded["bytes"] == len(encoded["rows_json"].encode("utf-8"))


def test_param_key_is_recomputable_from_params_json(store):
    """§4.3 / AC-1.2：`param_key` 可由 `params_json` 复算，归一化改动不会让旧记录失联。"""

    params = {"ts_code": "600887.SH", "period": "20260630", "fields": "end_date,revenue",
              "start_date": "20240101", "token": "secret"}
    record = store.write_frame(ticker="600887.SH", dataset="income", period="20260630",
                               params=params, frame=_frame(), result="ok").record

    assert record["param_key"] == param_key(params)
    assert record["param_key"] == param_key(json.loads(record["params_json"]))
    assert store.find("600887.SH", "income", "20260630",
                      param_digest=record["param_key"]) is not None


def test_stats_and_export_reflect_store_contents(store, tmp_path):
    """§4.5 / AC-1.2：`stats()` 的计数与 `export()` 出来的可读 JSON 树一致。"""

    store.write_frame(ticker="600887.SH", dataset="income", period="20260630",
                      params=_params(), frame=_frame(), result="ok")
    store.write_frame(ticker="600887.SH", dataset="daily", period="latest",
                      params={"ts_code": "600887.SH", "start_date": "20260101",
                              "end_date": "20261231"},
                      frame=pd.DataFrame({"trade_date": ["20260630"], "close": [10.5]}),
                      result="error", error_excerpt="boom")

    stats = store.stats()
    assert stats["records"] == 2
    assert stats["by_result"] == {"error": 1, "ok": 1}
    assert stats["by_dataset"] == {"daily": 1, "income": 1}
    assert stats["by_period_type"] == {"half": 1, "series": 1}
    assert stats["bytes"] > 0

    out = tmp_path / "export"
    assert store.export(out) == 2
    entries = [json.loads(line) for line in
               (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    assert len(entries) == 2
    for entry in entries:
        payload = json.loads((out / entry["file"]).read_text(encoding="utf-8"))
        assert isinstance(payload, list)


def test_scanned_endpoints_are_declared_and_covered_by_bulk_profile():
    """§5.2 / AC-1.1：扫到的接口必须全部在注册表声明，并被 `bulk` 档位覆盖。"""

    scanned = set(endpoints.scan_safe_calls())
    assert scanned, "扫描结果为空，门禁会退化成恒真"
    assert endpoints.missing_declarations() == []
    assert scanned <= set(registry.PROFILES["bulk"])


def _is_self_pro_attribute(node):
    return (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
            and node.value.attr == "pro" and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self")


def test_collector_pro_attribute_access_is_confined_to_access_helpers():
    """§6.1 / AC-1.2：`tushare_collector.py` 的 `self.pro.` 只出现在门面建设与修钩处。"""

    tree = ast.parse(COLLECTOR_MODULE.read_text(encoding="utf-8"),
                     filename=str(COLLECTOR_MODULE))
    sites = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        lines = [sub.lineno for sub in ast.walk(node) if _is_self_pro_attribute(sub)]
        if lines:
            sites[node.name] = sorted(lines)

    assert sites, "没有扫到任何 self.pro. 访问，断言会退化成恒真"
    assert set(sites) <= {"_new_pro_api", "_apply_broker_hacks"}, sites


def test_collector_cache_entries_import_once_and_idempotently(store, tmp_path):
    """AC-1.4：`output/.collector_cache/` 的既有条目可一次性幂等导入。

    数据集名自带下划线（`stock_basic`），所以文件名必须按**已知数据集前缀**解析——
    按第一个下划线切会把条目静默漏导，导入报告还显示成「幂等跳过」；
    美股全市场快照没有单一标的（空 ticker），也必须能落仓。
    """

    cache = tmp_path / "collector_cache"
    cache.mkdir()
    (cache / "stock_basic_600887.SH.json").write_text(
        json.dumps([{"ts_code": "600887.SH", "name": "伊利股份"}]), encoding="utf-8")
    pd.DataFrame([{"ts_code": "AAPL", "trade_date": "20241231", "close": 254.49}]).to_parquet(
        cache / "us_daily_all.parquet", index=False)

    first = legacy.import_collector_cache(store, cache)
    assert (first["imported"], first["skipped"], first["conflicts"]) == (2, 0, 0)

    basic = store.find("600887.SH", "stock_basic", "latest", params={"ts_code": "600887.SH"})
    assert basic is not None and basic["result"] == "ok"
    snapshot = store.find("", "us_daily", "latest",
                          params={"scope": "all_market", "limit": 6000})
    assert snapshot is not None
    assert json.loads(snapshot["rows_json"])[0]["ts_code"] == "AAPL"

    second = legacy.import_collector_cache(store, cache)
    assert (second["imported"], second["skipped"]) == (0, 2)
    assert len(store.records()) == 2


def test_refresh_without_a_tier_label_keeps_the_recorded_one(store):
    """AC-1.2/AC-3（独立复核 N10）：档位标签是**人的记录**，刷新不该把它抹成空。

    带 `--tier-label 年包` 拉过一次之后，某次不带标签的 `--force` 曾把仓里的
    `tier_label` 覆盖成 `''`——资产里的账号档位信息就这么静默丢了。
    """

    from datalayer.access import DataAccess

    class _Pro:
        def __getattr__(self, name):
            return lambda **kwargs: pd.DataFrame({"ts_code": ["600887.SH"], "name": ["伊利"]})

    access = DataAccess(store, client=type("C", (), {"pro": _Pro(), "token": "tok",
                                                    "_vip_mode": False, "MAX_RETRIES": 1,
                                                    "RETRY_DELAY": 0})(),
                        token="tok", mode="online", tier_label="年包", rate_limit_seconds=0)
    params = {"ts_code": "600887.SH", "fields": "ts_code,name"}
    access.call("stock_basic", **params)
    assert store.find("600887.SH", "stock_basic", "latest",
                      params=params)["tier_label"] == "年包"

    refreshing = DataAccess(store, client=access.client, token="tok", mode="refresh",
                            tier_label="", rate_limit_seconds=0)
    refreshing.call("stock_basic", **params)

    record = store.find("600887.SH", "stock_basic", "latest", params=params)
    assert record["tier_label"] == "年包", "不带标签的刷新不能抹掉已有标签"
