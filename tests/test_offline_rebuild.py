"""REQ-011.3 离线重建派生产物的测试。

覆盖需求：REQ-011.3（离线重建派生产物）
- AC-3.1：禁网（socket stub 为抛错）下从仓重建 `data_pack_market.md`，既有下游解析契约不变（对应父需求 AC-5）
- AC-3.2：重建与拉取是两个不同动作，CLI 上可分辨（对应 AC-5）
- AC-3.3：图表所需序列可直接从仓取结构化数据，不依赖 Markdown 小节标题与列名（对应 AC-4/AC-5，供 REQ-012.3）
"""

import json
import re
import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from datalayer import cli, rebuild  # noqa: E402
from datalayer.access import MODE_OFFLINE  # noqa: E402
from datalayer.store import DataStore  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mock_tushare_responses"

# 用**既有 fixture** 当远程响应：真实接口字段名与形状都在里面，比手搓 DataFrame 更能暴露契约问题。
MOCK_FILES = {
    "stock_basic": "stock_basic.json",
    "daily_basic": "daily_basic.json",
    "income": "income.json",
    "balancesheet": "balancesheet.json",
    "cashflow": "cashflow.json",
    "fina_indicator": "fina_indicator.json",
    "fina_audit": "fina_audit.json",
    "fina_mainbz": "fina_mainbz.json",
    "dividend": "dividend.json",
    "top10_holders": "top10_holders.json",
    "pledge_stat": "pledge_stat.json",
    "repurchase": "repurchase.json",
    "weekly": "weekly.json",
    "yc_cb": "yc_cb.json",
}

SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)
FOOTER_RE = re.compile(r"^\*共 \d+/\d+ 个数据板块成功获取\*$", re.MULTILINE)


def _load_fixture(name):
    path = FIXTURES / MOCK_FILES[name]
    payload = json.loads(path.read_text(encoding="utf-8"))
    return pd.DataFrame(payload if isinstance(payload, list) else [payload])


class FakePro:
    """假的 Tushare 客户端：按接口名返回既有 fixture，并记录被调用的次数。"""

    def __init__(self, fixtures=None, errors=None):
        self.fixtures = MOCK_FILES if fixtures is None else fixtures
        self.errors = errors or {}
        self.calls = []

    def __getattr__(self, name):
        api = name[:-4] if name.endswith("_vip") else name

        def call(**kwargs):
            self.calls.append((api, kwargs))
            if api in self.errors:
                raise self.errors[api]
            if api in self.fixtures:
                return _load_fixture(api)
            return pd.DataFrame()

        return call


def _seed_store(store, token="tok"):
    """跑一遍**联网**装配路径把仓填满（与生产里 `--pull` 的落盘形态一致）。"""

    from tushare_collector import TushareClient

    pro = FakePro()
    with patch("tushare_collector.ts") as mock_ts:
        mock_ts.pro_api.return_value = pro
        # rate_limit_seconds=0：限流节奏是真实联网才需要的，测试里不该等它。
        client = TushareClient(token, store=store, rate_limit_seconds=0)
        client.pro = pro
        markdown = client.assemble_data_pack("600887.SH")
    return markdown, pro


def _offline_client(store):
    from tushare_collector import TushareClient

    return TushareClient("", store=store, mode=MODE_OFFLINE, rate_limit_seconds=0)


@pytest.fixture
def store(tmp_path):
    return DataStore(tmp_path / "archive")


@pytest.fixture
def seeded(store):
    markdown, pro = _seed_store(store)
    return {"store": store, "live": markdown, "pro": pro}


# --------------------------------------------------------------------- AC-3.1 禁网重建


def test_rebuild_offline_does_not_open_a_socket(seeded, tmp_path):
    """AC-3.1：禁网（socket 一律抛错）也能从仓重建出数据包。"""

    class NoNetwork(socket.socket):
        def __init__(self, *args, **kwargs):
            raise AssertionError("离线重建不允许建立任何 socket")

    out = tmp_path / "out" / "data_pack_market.md"
    with patch("socket.socket", NoNetwork):
        report = rebuild.rebuild(seeded["store"], "600887.SH", out_path=out)

    assert report["out_path"] == str(out)
    assert out.is_file()
    assert report["missing"] == []
    assert report["archived_hits"] > 0


def test_rebuild_offline_client_has_no_remote_client(store):
    """AC-3.1：离线模式的客户端根本不建远程连接（不是「忘了拉」）。"""

    client = _offline_client(store)
    assert client.pro is None
    assert client._access.mode == MODE_OFFLINE


def test_rebuild_reads_the_store_and_never_writes_new_records(seeded):
    """AC-3.1/AC-5：重建是只读动作——仓内记录数与审计清单都不变。"""

    before = seeded["store"].stats()["records"]
    manifest_before = (seeded["store"].root / "manifest.jsonl").read_text(encoding="utf-8")
    rebuild.rebuild(seeded["store"], "600887.SH", out_path=seeded["store"].root / "pack.md")

    assert seeded["store"].stats()["records"] == before
    assert (seeded["store"].root / "manifest.jsonl").read_text(encoding="utf-8") == manifest_before


def test_rebuilt_sections_match_the_online_path(seeded):
    """AC-3.1：换数据源不换模具——小节集合与联网产出完全一致。"""

    report = rebuild.rebuild(seeded["store"], "600887.SH",
                             out_path=seeded["store"].root / "pack.md")
    rebuilt = Path(report["out_path"]).read_text(encoding="utf-8")

    assert SECTION_RE.findall(rebuilt) == SECTION_RE.findall(seeded["live"])
    assert SECTION_RE.findall(rebuilt)  # 不是两个空列表在互相确认


def test_rebuilt_pack_keeps_the_downstream_parsing_contract(seeded):
    """AC-3.1：既有下游（`results/prepare.py` 走的那条）按小节/表头解析仍然成功。"""

    from split_data_pack import check_d6_trigger, find_section, parse_sections

    report = rebuild.rebuild(seeded["store"], "600887.SH",
                             out_path=seeded["store"].root / "pack.md")
    sections = parse_sections(Path(report["out_path"]).read_text(encoding="utf-8"))

    assert "1. 基本信息" in sections
    assert "3. 合并利润表" in sections
    assert "4. 合并资产负债表" in sections
    assert "12. 关键财务指标" in sections
    income = find_section(sections, "3.")
    assert "| 项目 (百万元) |" in income
    assert "营业收入" in income
    # `check_d6_trigger` 是 prepare 路径上真正读小节的函数：不能因为重建就抛错。
    assert isinstance(check_d6_trigger(sections)["triggered"], bool)


def test_rebuild_output_matches_the_online_section_headers(seeded):
    """AC-3.1：联网与重建两条路径的表头（`## N. 标题`）逐条相同。"""

    report = rebuild.rebuild(seeded["store"], "600887.SH",
                             out_path=seeded["store"].root / "pack.md")
    rebuilt_sections = SECTION_RE.findall(Path(report["out_path"]).read_text(encoding="utf-8"))
    live_sections = SECTION_RE.findall(seeded["live"])
    assert [line.split(" ", 1)[1] for line in rebuilt_sections] == \
        [line.split(" ", 1)[1] for line in live_sections]


# --------------------------------------------------------------------- 缺口与完备度口径


def test_missing_record_becomes_a_visible_gap(store, tmp_path):
    """AC-3.1/§8.2：仓里没有的目标渲染成「数据缺失」，并列出缺口与补齐命令。"""

    report = rebuild.rebuild(store, "600887.SH", out_path=tmp_path / "pack.md")
    text = Path(report["out_path"]).read_text(encoding="utf-8")

    assert report["missing"], "空仓重建应当有缺口"
    assert "数据缺失" in text
    assert "重建缺口" in text
    assert "--only-gaps" in text


def test_completeness_counts_store_results_and_excludes_no_permission(store, tmp_path):
    """AC-5/§8.2：完备度按仓的实际情况算，**无权限不算成功**。"""

    store.write_frame(ticker="600887.SH", dataset="income", period="20251231",
                      params={"ts_code": "600887.SH", "period": "20251231"}, frame=pd.DataFrame(),
                      result="no_permission", error_excerpt="没有权限")
    store.write_frame(ticker="600887.SH", dataset="fina_audit", period="20251231",
                      params={"ts_code": "600887.SH", "period": "20251231"},
                      frame=_load_fixture("fina_audit"), result="ok")

    report = rebuild.rebuild(store, "600887.SH", out_path=tmp_path / "pack.md")
    text = Path(report["out_path"]).read_text(encoding="utf-8")

    assert report["counts"]["no_permission"] == 1
    assert report["complete"] == 1
    assert report["records"] == 2
    assert "*共 1/2 个数据板块成功获取*" in text
    assert "无权限 1" in text
    # 完备度行仍是既有格式（下游与既有测试都在看这一行）。
    assert FOOTER_RE.search(text)


def test_apply_store_facts_appends_footer_when_absent(store):
    """没有完备度行时也要补上（否则重建产物会丢「数据截至」与缺口说明）。"""

    text = rebuild.apply_store_facts("# 数据包 — 600887.SH\n", store, "600887.SH", [])
    assert FOOTER_RE.search(text)
    assert "数据截至" in text


def test_rebuild_reports_data_as_of_from_the_store(store, tmp_path):
    """§8.3：「报表生成时间」与「数据截至」不再混为一谈。"""

    store.write_frame(ticker="600887.SH", dataset="fina_audit", period="20251231",
                      params={"ts_code": "600887.SH", "period": "20251231"},
                      frame=_load_fixture("fina_audit"), result="ok",
                      fetched_at="2026-09-28T00:00:00+00:00")

    report = rebuild.rebuild(store, "600887.SH", out_path=tmp_path / "pack.md")
    assert report["data_as_of"] == "2026-09-28T00:00:00+00:00"
    assert "2026-09-28T00:00:00+00:00" in Path(report["out_path"]).read_text(encoding="utf-8")


def test_rebuild_period_records_compose_into_a_full_history_frame(store):
    """AC-3.1：按期次各存一条时，读取路径把它们组合成调用点要的整段历史。"""

    from datalayer.access import DataAccess

    for period in ("20241231", "20251231"):
        store.write_frame(
            ticker="600887.SH", dataset="income", period=period,
            params={"ts_code": "600887.SH", "period": period, "report_type": "1"},
            frame=pd.DataFrame({"ts_code": ["600887.SH"], "end_date": [period],
                                "report_type": ["1"], "revenue": [100.0]}),
            result="ok")

    access = DataAccess(store, mode="offline")
    frame = access.call("income", ts_code="600887.SH", report_type="1",
                        fields="ts_code,end_date,revenue")

    assert list(frame.columns) == ["ts_code", "end_date", "revenue"]
    assert list(frame["end_date"]) == ["20251231", "20241231"]  # 新期在前
    assert access.archived_hits == 1


def test_rebuild_does_not_serve_a_narrower_window(store):
    """AC-3.1：仓里的窗口更窄时判为未命中（缺口可见），不能拿更短的历史冒充。"""

    from datalayer.access import DataAccess, DataMissing

    store.write_frame(ticker="600887.SH", dataset="daily", period="latest",
                      params={"ts_code": "600887.SH", "start_date": "20260101",
                              "end_date": "20260201", "fields": "ts_code,trade_date,close"},
                      frame=pd.DataFrame({"ts_code": ["600887.SH"], "trade_date": ["20260102"],
                                          "close": [1.0]}),
                      result="ok")

    access = DataAccess(store, mode="offline")
    with pytest.raises(DataMissing):
        access.call("daily", ts_code="600887.SH", start_date="20250101", end_date="20260101",
                    fields="ts_code,trade_date,close")


# --------------------------------------------------------------------- AC-3.2 CLI 动作分离


def test_cli_exposes_rebuild_and_pull_as_two_actions():
    """AC-3.2：花费（pull）与不花费（rebuild）在 CLI 上是两个动作。"""

    parser = cli.build_parser()
    actions = set()
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            try:
                actions |= set(action.choices)
            except TypeError:
                continue
    assert {"pull", "rebuild", "gaps", "universe", "import-legacy", "check"} <= actions


def test_cli_rebuild_writes_the_pack_without_a_token(store, tmp_path):
    """AC-3.2：`rebuild` 不需要 token（离线动作），并且真的写出产物。"""

    out = tmp_path / "pack.md"
    with patch("datalayer.security.resolve_token", return_value=""):
        code = cli.main(["rebuild", "--store", str(store.root), "--ticker", "600887.SH",
                         "--out", str(out)])
    assert code == 1  # 空仓有缺口 → 非零，但产物已写出
    assert out.is_file()


def test_cli_pull_requires_a_token_and_makes_no_request(tmp_path):
    """AC-3.2/AC-2：没配 token 时 `pull` 零请求退出（退出码 2）。"""

    with patch("datalayer.security.resolve_token", return_value=""):
        code = cli.main(["pull", "--store", str(tmp_path / "archive"), "--yes"])
    assert code == 2


def test_cli_rebuild_and_pull_are_distinguishable_by_the_store(tmp_path):
    """AC-3.2：`rebuild` 不往仓里写任何记录（只有 `pull` 会）。"""

    root = tmp_path / "archive"
    with patch("datalayer.security.resolve_token", return_value=""):
        cli.main(["rebuild", "--store", str(root), "--ticker", "600887.SH",
                  "--out", str(tmp_path / "pack.md")])
    assert not (root / "manifest.jsonl").exists() or \
        (root / "manifest.jsonl").read_text(encoding="utf-8") == ""


# --------------------------------------------------------------------- AC-3.3 结构化口径


def test_annual_and_quarter_series_come_from_structured_columns(store):
    """AC-3.3：图表序列按 `period_type` 分组取，不靠 Markdown 小节标题或列名猜口径。"""

    for period in ("20251231", "20260630", "20260331"):
        store.write_frame(
            ticker="600887.SH", dataset="income", period=period,
            params={"ts_code": "600887.SH", "period": period},
            frame=pd.DataFrame({"ts_code": ["600887.SH"], "end_date": [period],
                                "revenue": [1.0]}),
            result="ok")

    annual = store.records(dataset="income", period_type="annual")
    half = store.records(dataset="income", period_type="half")
    quarter = store.records(dataset="income", period_type="quarter")

    assert [record["period"] for record in annual] == ["20251231"]
    assert [record["period"] for record in half] == ["20260630"]
    assert [record["period"] for record in quarter] == ["20260331"]
    assert all(record["cumulative"] for record in annual + half + quarter)


def test_chart_series_frame_is_available_without_markdown(store):
    """AC-3.3：直接给 DataFrame，不必先渲染 Markdown 再解析回来。"""

    store.write_frame(ticker="600887.SH", dataset="income", period="20251231",
                      params={"ts_code": "600887.SH", "period": "20251231"},
                      frame=pd.DataFrame({"ts_code": ["600887.SH"], "end_date": ["20251231"],
                                          "revenue": [120140.0]}),
                      result="ok")
    record = store.records(dataset="income", period_type="annual")[0]

    frame = store.frame(record)
    assert list(frame["revenue"]) == [120140.0]
    # dtype 契约：写进 `columns_json` 的 dtype 与读回来的 dtype 一致（不靠 pandas 默认推断）。
    contract = {column["name"]: column["dtype"]
                for column in json.loads(record["columns_json"])}
    assert {name: str(frame[name].dtype) for name in contract} == contract


def test_rebuild_report_counts_every_ticker_record(store, tmp_path):
    """AC-3.3：重建报告给出的口径计数与仓内一致（供后续页面复用同一份事实）。"""

    store.write_frame(ticker="600887.SH", dataset="income", period="20251231",
                      params={"ts_code": "600887.SH", "period": "20251231"},
                      frame=pd.DataFrame(), result="rate_limited", error_excerpt="频率")
    report = rebuild.rebuild(store, "600887.SH", out_path=tmp_path / "pack.md")
    assert report["counts"]["rate_limited"] == 1
    assert report["complete"] == 0
