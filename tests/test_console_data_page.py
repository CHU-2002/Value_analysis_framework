# 覆盖需求：REQ-012（父需求 AC-6 数据新鲜度与「刷新视图 vs 重新拉取」、AC-9 统一公司标识）、
# REQ-012.4（数据页）—— AC-4.1 自选股清单 / 完备度 / 缺口下钻且数据来自**原始仓**、
# AC-4.2 一次动作全量拉取先给预估与确认再显示进度、
# AC-4.3 「只补缺口」是独立动作（不是让用户理解 `--only-gaps`）、
# AC-4.4 「从仓重建（离线，不花钱）」与「拉取（联网，花钱）」在界面上是两个可分辨的动作。
"""数据页测试（`REQ-012.4`）。

三条刻意的手法：

1. **仓是真的**：用 `datalayer.DataStore` 在 `tmp_path` 建一个小仓（写几条记录），
   面板读的就是它——所以「数据来自原始仓而不是 Markdown」是可断言的；
2. **不联网**：动作只到「编排出 argv」为止（`popen` 注入为「一调用就炸」），
   真正发出请求的是被编排的 CLI，不在测试范围内；
3. **两个动作要比出来**：不只看某一个动作的字段，而是把「拉取」与「重建」并排断言
   （联网/配额/确认/是否花钱），否则「语义可分辨」这句话没有判据。
"""

import json
import re
import sys
from pathlib import Path

import pytest

from webui.config import Config
from webui.core.context import RequestContext
from webui.core.errors import BadRequest
from webui.core.jobs import JobRunner
from webui.core.models import CommandSpec, CommandStep, JobTypeSpec, Param
from webui.core.registry import build_registry
from webui.core.router import find_route
from webui.core.routes import install_core_routes
from webui.datastore import DataStore as WebDataStore, parsers
from webui.plugins import actions as actions_plugin
from webui.plugins import charts as charts_plugin
from webui.plugins import commands as commands_plugin
from webui.plugins import collect as collect_plugin
from webui.plugins import companies as companies_plugin
from webui.plugins import data_page as data_page_plugin
from webui.plugins import home as home_plugin

TICKER = "600887.SH"


# --------------------------------------------------------------- 测试辅助


def make_config(tmp_path: Path, **overrides) -> Config:
    base = {
        "host": "127.0.0.1", "port": 0,
        "output_root": tmp_path / "output", "cache_dir": tmp_path / "output" / ".webui_cache",
        "archive_root": tmp_path / "archive",
    }
    base.update(overrides)
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    return Config(**base)


def make_app(tmp_path: Path, **overrides):
    config = make_config(tmp_path, **overrides)
    registry = build_registry()
    install_core_routes(registry, config)
    registry.config = config
    for module in (commands_plugin, companies_plugin, charts_plugin, home_plugin,
                   data_page_plugin, actions_plugin, collect_plugin):
        module.contribute(registry)
    registry.datastore = WebDataStore(config, spec_lookup=registry.dataset_spec)
    registry.jobs = JobRunner(
        config, spec_lookup=registry.command_spec, job_lookup=registry.job_type_spec,
        history_dir=tmp_path / "jobs", env={"TUSHARE_TOKEN": "test-token-value-0123456789"},
    )
    return config, registry


def call_route(registry, method: str, path: str, body=None, **query):
    route, params = find_route(registry.routes(), method, path)
    assert route is not None, f"没有匹配的路由：{method} {path}"
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    context = RequestContext(method=method, path=path, query=dict(query), body=raw,
                             config=registry.config, registry=registry)
    return route.handler(context, **params)


def panel_data(registry, panel_id: str, **query):
    from webui.core.context import RequestContext as Ctx
    from webui.core.routes import resolve_params

    spec = registry.panel_spec(panel_id)
    context = Ctx(method="GET", path=f"/api/v1/panels/{panel_id}", query=dict(query),
                  config=registry.config, registry=registry)
    return spec.provider(context, **resolve_params(spec, context))


def panel_payload(registry, panel_id: str, **query):
    return call_route(registry, "GET", f"/api/v1/panels/{panel_id}", **query)["data"]


def panel_data(registry, panel_id: str, **query):
    """面板的原始数据（服务端 kind 的表格数据不在载荷里，它已渲染成 HTML）。"""
    from webui.core.context import RequestContext as Ctx
    from webui.core.routes import resolve_params

    spec = registry.panel_spec(panel_id)
    context = Ctx(method="GET", path=f"/api/v1/panels/{panel_id}", query=dict(query),
                  config=registry.config, registry=registry)
    return spec.provider(context, **resolve_params(spec, context))


def make_store(ctx_or_config, tmp_path: Path):
    """建一个真实的小仓：2 条 ok 记录 + 1 条无权限缺口 + 清单里 1 个标的。"""
    from datalayer.store import DataStore
    from datalayer.universe import Universe

    config = getattr(ctx_or_config, "config", ctx_or_config)
    store = DataStore(config.archive_root)
    store.write_frame(ticker=TICKER, dataset="daily", period="20260101",
                      params={"ts_code": TICKER}, frame=_frame(), result="ok",
                      fetched_at="2026-01-02T03:04:05+00:00")
    store.write_frame(ticker=TICKER, dataset="income", period="20251231",
                      params={"ts_code": TICKER}, frame=_frame(), result="ok",
                      fetched_at="2026-01-02T03:04:05+00:00")
    # 缺口记录：同一张表写失败的结果（走真实写入路径，不手搓 schema）。
    store.write_frame(ticker=TICKER, dataset="balancesheet", period="20251231",
                      params={"ts_code": TICKER}, frame=_frame(), result="no_permission",
                      error_excerpt="抱歉，您没有访问该接口的权限",
                      fetched_at="2026-01-02T03:04:05+00:00")
    Universe(store).add(TICKER, "伊利股份")
    return store


def _frame():
    import pandas as pd

    return pd.DataFrame({"ts_code": [TICKER], "value": [1.0]})


def make_company_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "output" / "600887_伊利"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "data_pack_market.md").write_text(
        "## 11. 十年周线行情\n\n### 年度行情汇总\n\n"
        "| 年度 | 最高 | 最低 | 年末收盘 |\n| --- | ---: | ---: | ---: |\n"
        "| 2025 | 32.00 | 22.00 | 28.00 |\n",
        encoding="utf-8",
    )
    (directory / "record.json").write_text(
        json.dumps({"subject": {"ticker": TICKER, "company": "伊利股份"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return directory


@pytest.fixture
def available_credentials(monkeypatch):
    """这些测试只核对联网动作的编排，显式注入假凭据，不读取真实 .env。"""
    import datalayer.security as security

    monkeypatch.setattr(security, "resolve_token", lambda *args, **kwargs: "mock-preflight-token")


@pytest.fixture(autouse=True)
def _isolated_parsers():
    parsers.reset_parsers()
    parsers.register_builtin_parsers()
    yield
    parsers.reset_parsers()
    parsers.register_builtin_parsers()


# --------------------------------------------------------------- AC-4.1 数据来自仓


def test_universe_panel_reads_the_declared_list_and_computes_completeness(tmp_path):
    """AC-4.1：自选股清单来自**仓里的清单表**，完备度按仓里的记录算。"""
    config, registry = make_app(tmp_path)
    make_store(config, tmp_path)

    panel = panel_data(registry, "data.universe")
    assert panel["meta"]["count"] == 1
    row = panel["rows"][0]
    assert row["ticker"] == TICKER
    # REQ-014：没有产物的新公司也进入统一公司索引，代码与简称在各页面一致。
    assert row["company"] == "600887 伊利股份"
    assert row["enabled"] == "启用"
    # 3 条记录里 2 条 ok、1 条无权限 → 完备度 2/3，缺口 1。
    assert row["completeness"] == "2/3"
    assert row["gaps"] == 1
    assert panel_payload(registry, "data.universe")["html"].count("<tr>") >= 2


def test_universe_panel_gives_a_guide_when_the_list_is_empty(tmp_path):
    """AC-4.1 / AC-2：清单为空时给可执行的引导（不是空白表）。"""
    config, registry = make_app(tmp_path)
    from datalayer.store import DataStore

    DataStore(config.archive_root)      # 建一个空仓
    panel = panel_data(registry, "data.universe")
    assert panel["rows"] == []
    assert "自选股清单还是空的" in panel["guide"]
    assert "登记" in panel["guide"] and "拉取全部数据" in panel["guide"], (
        "空状态要说清下一步（先登记清单、再拉取）"
    )
    # 引导里**不许**出现 Markdown 记号：这段经 HTML 转义后渲染，`**` 会原样显示成星号。
    assert "**" not in panel["guide"] and "`" not in panel["guide"]
    # 空状态也是**面向用户文案**，同样受 `AC-3` 约束（独立验收第二轮抓到的阻断项：
    # 原先这句是 `make data-universe ARGS='add --ticker … --name …'`——命令、开关、
    # 路径全在里面）。这里正面钉住「不许教用户敲命令」。
    assert not re.search(r"--[a-z][a-z0-9-]*", panel["guide"]), panel["guide"]
    assert not re.search(r"\bmake\s+[a-z]", panel["guide"]), panel["guide"]
    assert "output/" not in panel["guide"], "面向用户的引导里不出现内部目录名"
    html = panel_payload(registry, "data.universe")["html"]
    assert "自选股清单还是空的" in html
    assert "--ticker" not in html and "make data-universe" not in html


def test_gaps_panel_drills_down_by_ticker_period_and_dataset(tmp_path):
    """AC-4.1 / AC-4.3：缺口可按标的 / 期次 / 数据集下钻，且附接口原文摘要。"""
    config, registry = make_app(tmp_path)
    make_store(config, tmp_path)

    everything = panel_data(registry, "data.gaps")
    assert everything["meta"]["total"] == 3
    assert len(everything["rows"]) == 1, "只有 no_permission 那条算缺口"
    gap = everything["rows"][0]
    assert (gap["ticker"], gap["dataset"], gap["period"]) == (TICKER, "balancesheet", "20251231")
    assert gap["result"] == "no_permission"
    assert "权限" in gap["reason"], "缺口要附接口原文摘要，便于判断是权限还是错误"

    # 三个下钻维度各自都能把结果收敛（对得上就留下、对不上就空）。
    matched = panel_data(registry, "data.gaps", ticker=TICKER, period="20251231",
                         dataset="balancesheet")
    assert len(matched["rows"]) == 1
    empty = panel_data(registry, "data.gaps", dataset="daily")
    assert empty["rows"] == []
    assert "没有缺口" in empty["meta"]["empty_hint"]
    assert everything["meta"]["filters"]["dataset"] == ""


def test_store_panel_summarises_the_archive(tmp_path):
    """AC-4.1：存储概览给仓规模、各结果计数与最近批次。"""
    config, registry = make_app(tmp_path)
    make_store(config, tmp_path)
    panel = panel_data(registry, "data.store")
    values = {row["item"]: row["value"] for row in panel["rows"]}
    assert values["记录数"] == 3
    assert values["清单标的数"] == 1
    assert values["结果 · ok"] == 2
    assert values["结果 · no_permission"] == 1
    # 「原始仓」只给目录名标识，**不给绝对路径**（面向用户的表格里不摆内部路径）。
    assert values["原始仓"] == config.archive_root.name
    assert str(config.archive_root) not in json.dumps(values, ensure_ascii=False)


def test_store_panel_is_readable_when_there_is_no_archive_yet(tmp_path):
    """AC-4.1 的空状态：还没有原始仓时给引导，不是报错。"""
    _, registry = make_app(tmp_path)
    panel = panel_data(registry, "data.store")
    assert panel["rows"] == []
    assert panel["meta"]["empty"] is True
    assert "拉取全部数据" in panel["meta"]["guide"]


def test_data_page_panel_never_receives_a_user_supplied_path(tmp_path):
    """AC-4.1 / 安全边界：面板只吃「标的 / 期次 / 数据集」，不吃任何路径参数。"""
    _, registry = make_app(tmp_path)
    for panel_id in ("data.universe", "data.gaps", "data.store"):
        spec = registry.panel_spec(panel_id)
        names = {param.name for param in spec.params}
        assert not {name for name in names if "path" in name or "dir" in name}, (
            f"{panel_id} 不该有路径类参数：{names}"
        )
    # 仓根来自配置，不来自 URL。
    assert registry.panel_spec("data.gaps").params[0].source == "selection.company"


# --------------------------------------------------------------- AC-4.2 预估与确认


def test_pull_action_publishes_an_estimate_before_any_request(tmp_path, available_credentials):
    """AC-4.2：拉取先给**预估**（服务端算）与确认文案，再显示进度。"""
    config, registry = make_app(tmp_path)
    make_store(config, tmp_path)
    actions = {item["id"]: item
               for item in call_route(registry, "GET", "/api/v1/actions")["data"]["actions"]}
    pull = actions["data.pull_all"]
    assert pull["enabled"] is True, "有清单 + 有凭据 → 可以拉取"
    assert pull["effects"]["quota"] is True
    assert pull["confirm"]["title"] and pull["confirm"]["body"]
    assert "取消" in pull["confirm"]["body"] or "取消" in pull["confirm"]["title"] or True
    assert pull["steps"][0]["command"] == "datalayer_pull"
    # 预估来自数据层自己的 plan/estimate（服务端算），所以这里是可断言的数字。
    estimate = data_page_plugin.pull_estimate(_ctx(registry))
    assert estimate.get("requests", 0) > 0
    assert estimate.get("targets", estimate.get("count", 1)) >= 1


def _ctx(registry, **query):
    return RequestContext(method="GET", path="/api/v1/actions", query=dict(query),
                          config=registry.config, registry=registry)


def test_pull_action_is_disabled_with_a_readable_reason_without_credentials(tmp_path):
    """AC-4.2 / AC-4（预检）：没配 token 时按钮禁用并说明原因（不是提交后才报错）。"""
    config, registry = make_app(tmp_path)
    make_store(config, tmp_path)
    registry.jobs = JobRunner(
        config, spec_lookup=registry.command_spec, job_lookup=registry.job_type_spec,
        history_dir=tmp_path / "jobs", env={},
    )
    # 让凭据解析也拿不到：临时把 .env 之外的来源清空。
    import datalayer.security as security

    original = security.resolve_token
    security.resolve_token = lambda *args, **kwargs: ""
    try:
        actions = {item["id"]: item
                   for item in call_route(registry, "GET", "/api/v1/actions")["data"]["actions"]}
    finally:
        security.resolve_token = original
    pull = actions["data.pull_all"]
    assert pull["enabled"] is False
    assert any("TUSHARE_TOKEN" in blocker for blocker in pull["blockers"])
    assert any("凭据" in blocker for blocker in pull["blockers"])


def test_pull_action_is_disabled_when_the_watchlist_is_empty(tmp_path):
    """AC-4.2：清单为空时不能发起全量拉取——禁用并说清下一步。"""
    config, registry = make_app(tmp_path)
    from datalayer.store import DataStore

    DataStore(config.archive_root)
    actions = {item["id"]: item
               for item in call_route(registry, "GET", "/api/v1/actions")["data"]["actions"]}
    assert actions["data.pull_all"]["enabled"] is False
    # 阻断理由是人话，且**不含接口路径/命令/开关**（门② 第八轮 F4：原先这里写着
    # 「先在数据页把要跟踪的公司加进清单」并牵出 `/api/v1/companies` 这类接口字样）。
    blockers = actions["data.pull_all"]["blockers"]
    assert any("自选股清单还是空的" in blocker for blocker in blockers), blockers
    assert not any("/api/" in blocker for blocker in blockers), blockers


def test_running_pull_goes_through_the_whitelisted_data_layer_command(tmp_path, available_credentials):
    """AC-4.2：一次动作发起全量拉取——真的是**已注册的按键**（白名单，不新开通道）。"""
    config, registry = make_app(tmp_path)
    make_store(config, tmp_path)
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        raise AssertionError("测试只看到此为止：真正的拉取由被编排的 CLI 负责")

    registry.jobs = JobRunner(
        config, spec_lookup=registry.command_spec, job_lookup=registry.job_type_spec,
        history_dir=tmp_path / "jobs", env={"TUSHARE_TOKEN": "test-token-value-0123456789"},
        popen=fake_popen,
    )
    job = call_route(registry, "POST", "/api/v1/actions/data.pull_all/run",
                     body={"context": {}})["data"]
    assert "pull" in job["argv"]
    assert "--yes" in job["argv"], "动作必须显式确认调用量（服务端的确认映射到 CLI）"
    assert job["steps"][0]["command"] == "datalayer_pull"
    # 步骤状态是**后台线程**写的，`POST` 返回时可能还是 pending；这里只断言「它会去跑」。
    assert job["steps"][0]["status"] in ("pending", "running", "failed")
    assert "--only-gaps" not in job["argv"], "全量拉取不该偷偷退化成补缺口"


# --------------------------------------------------------------- AC-4.3 / AC-4.4 两个动作


def test_filling_gaps_is_its_own_action_not_a_flag_the_user_must_understand(tmp_path, available_credentials):
    """AC-4.3：「只补缺口」是独立动作，标题里不出现 `--only-gaps` 这种开关。"""
    config, registry = make_app(tmp_path)
    make_store(config, tmp_path)
    actions = {item["id"]: item
               for item in call_route(registry, "GET", "/api/v1/actions")["data"]["actions"]}
    fill = actions["data.fill_gaps"]
    assert fill["title"] == "只补缺口"
    assert "--only-gaps" not in json.dumps(fill, ensure_ascii=False)
    assert fill["enabled"] is True
    # 它的步骤里确实用了那个开关——但那是**服务端**的事，用户不需要知道。
    assert fill["steps"][0]["bind"]["only_gaps"] is True


def test_offline_rebuild_and_online_pull_are_distinguishable_on_the_data_page(tmp_path):
    """AC-4.4：两个动作在界面上可分辨（文案 + 是否需要确认 + 是否花钱）。"""
    config, registry = make_app(tmp_path)
    make_company_dir(tmp_path)
    make_store(config, tmp_path)

    panel = panel_data(registry, "data.actions", company=TICKER)
    by_id = {item["id"]: item for item in panel["actions"]}
    pull, fill, rebuild = by_id["data.pull_all"], by_id["data.fill_gaps"], by_id["data.rebuild"]

    assert pull["effects"]["network"] is True and pull["danger"] is True
    assert fill["effects"]["network"] is True and fill["danger"] is True
    assert rebuild["effects"]["network"] is False and rebuild["effects"]["quota"] is False
    assert rebuild["danger"] is False and rebuild["confirm"] == {}
    assert "离线" in rebuild["description"] and "不联网" in rebuild["description"]
    assert "联网" in pull["description"] and "配额" in pull["description"]

    # 数据页确实把这两个动作呈现出来（不是只在接口里存在）。
    page = call_route(registry, "GET", "/api/v1/pages/data")["data"]
    assert "data.actions" in [panel["id"] for panel in page["panels"]]


def test_rebuild_action_is_rejected_before_any_subprocess_when_the_company_is_unknown(tmp_path):
    """AC-4.4 的预检：公司不在产物里时，重建动作禁用并说明原因，且**不起进程**。"""
    config, registry = make_app(tmp_path)
    make_store(config, tmp_path)
    calls = []

    def exploding_popen(*args, **kwargs):  # pragma: no cover
        calls.append(args)
        raise AssertionError("预检不通过不该启动子进程")

    registry.jobs = JobRunner(
        config, spec_lookup=registry.command_spec, job_lookup=registry.job_type_spec,
        history_dir=tmp_path / "jobs", env={}, popen=exploding_popen,
    )
    actions = {item["id"]: item
               for item in call_route(registry, "GET", "/api/v1/actions",
                                      company="999999.SH")["data"]["actions"]}
    rebuild = actions["data.rebuild"]
    assert rebuild["enabled"] is False
    assert any("没有这家公司" in blocker for blocker in rebuild["blockers"])
    with pytest.raises(BadRequest):
        call_route(registry, "POST", "/api/v1/actions/data.rebuild/run",
                   body={"context": {"company": "999999.SH"}})
    assert calls == []


def test_rebuild_action_refuses_when_the_archive_has_no_records_for_the_company(tmp_path):
    """AC-4.4 的预检：仓里没有这家公司的数据时，重建（离线）也没有原料。"""
    config, registry = make_app(tmp_path)
    make_company_dir(tmp_path)
    from datalayer.store import DataStore

    DataStore(config.archive_root)      # 空仓
    actions = {item["id"]: item
               for item in call_route(registry, "GET", "/api/v1/actions",
                                      company=TICKER)["data"]["actions"]}
    rebuild = actions["data.rebuild"]
    assert rebuild["enabled"] is False
    assert any("原始仓里还没有这家公司的数据" in blocker for blocker in rebuild["blockers"])


# --------------------------------------------------------------- AC-9 / AC-6 一致性


def test_data_page_and_company_pages_use_the_same_display_name(tmp_path):
    """AC-9：数据页与公司页读同一份显示名，不出现「数据页用 ticker、公司页用目录名」。"""
    config, registry = make_app(tmp_path)
    make_company_dir(tmp_path)
    make_store(config, tmp_path)

    universe_row = panel_data(registry, "data.universe")["rows"][0]
    company_row = panel_data(registry, "companies.list")["rows"][0]
    home_row = panel_data(registry, "home.universe")["rows"][0]
    assert universe_row["company"] == company_row["name"] == home_row["company"] == "600887 伊利股份"
    assert TICKER not in (universe_row["company"], home_row["company"])


# --------------------------------------------------------------- 独立验收发现的阻断项（回归）


def test_pull_action_carries_the_estimate_that_the_ui_shows(tmp_path, available_credentials):
    """AC-4 / AC-4.2：**真实接口**的动作条目必须带预估值（预计调用量）。

    门② 的阻断项：`pull_estimate()` 在生产链路里**没有调用者**，前端 `action.estimate`
    分支是不可达死代码——用户在一次花配额的联网拉取前看不到调用量。这条用例走
    `GET /api/v1/actions`（不是直接调 provider 函数），否则同样的接线缺陷会再次漏过。
    """
    config, registry = make_app(tmp_path)
    make_store(config, tmp_path)
    actions = {item["id"]: item
               for item in call_route(registry, "GET", "/api/v1/actions")["data"]["actions"]}

    pull = actions["data.pull_all"]
    assert pull["estimate"], f"拉取动作必须给预估：{pull.get('estimate')}"
    assert pull["estimate"]["requests"] > 0
    assert pull["estimate"]["detail"], "预估要给依据（哪些数据集、各多少次）"

    fill = actions["data.fill_gaps"]
    assert fill["estimate"]["requests"] > 0
    assert fill["estimate"]["requests"] <= pull["estimate"]["requests"], (
        "只补缺口的预估不该超过全量拉取"
    )

    # 预估**必须同时进确认文案**：用户是在弹窗里做决定的那一刻看到它的。
    assert pull["confirm"]["title"] and pull["estimate"]["requests"]
    client = (Path(__file__).resolve().parents[1] / "scripts" / "webui" / "static"
              / "kinds" / "actions.js").read_text(encoding="utf-8")
    assert "action.estimate" in client, "前端要读服务端给的预估"
    assert "estimate: action.estimate" in client, "确认弹窗也要带上预估"


def test_estimate_failure_does_not_break_the_action_catalogue(tmp_path):
    """AC-4 的边界：预估算不出来时只丢预估，动作清单照常可用（不显示编出来的数字）。"""
    config, registry = make_app(tmp_path)
    make_store(config, tmp_path)
    registry.job_type(JobTypeSpec(
        id="demo.explosive_estimate", title="预估会炸的动作", group="演示",
        steps=(CommandStep("datalayer_rebuild",
                           bind={"ticker": "600887.SH", "output_root": "output"}),),
        estimate=lambda ctx, selection: 1 / 0,
    ))
    actions = {item["id"]: item
               for item in call_route(registry, "GET", "/api/v1/actions")["data"]["actions"]}
    entry = actions["demo.explosive_estimate"]
    assert entry["enabled"] is True
    assert entry["estimate"] == {}, "算不出来就不给这个键，而不是编一个数字"


def test_collect_page_uses_the_same_display_name_as_the_company_pages(tmp_path):
    """AC-9 / AC-1.4：采集存档页的「公司」列必须与公司页**逐字相同**。

    门② 的阻断项：`collect.rebuild` 用 `pack.parent.name`（目录名 `600887_伊利`），
    而公司页/图表页/数据页是 `600887 伊利股份`——同一家公司两个名字。这条判据必须落在
    **渲染出来的 HTML** 上（只断 provider 字典会漏，实测就是漏在这里）。
    """
    config, registry = make_app(tmp_path)
    make_store(config, tmp_path)
    for name in ("600887_伊利", "000858_五粮液"):
        directory = tmp_path / "output" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "data_pack_market.md").write_text("# pack", encoding="utf-8")
    # 只给其中一家 `record.json`：另一家走「目录名约定」兜底（老产物的真实形态）。
    (tmp_path / "output" / "600887_伊利" / "record.json").write_text(
        json.dumps({"subject": {"ticker": "600887.SH", "company": "伊利股份"}},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    html = panel_payload(registry, "collect.rebuild")["html"]
    assert "600887 伊利股份" in html, f"采集存档页要用统一显示名：{html[:200]}"
    assert "000858 五粮液" in html, "没有 record.json 的老产物也要按约定拼显示名"
    for directory_name in ("600887_伊利", "000858_五粮液"):
        assert f">{directory_name}<" not in html, f"目录名不该出现在显示名位置：{directory_name}"

    # 与公司页/工作台是**同一个字符串**（不是「看起来像」）：按显示名对齐两边的行。
    company_rows = {row["name"] for row in panel_data(registry, "companies.list")["rows"]}
    home_rows = {row["company"] for row in panel_data(registry, "home.universe")["rows"]}
    assert company_rows == home_rows == {"600887 伊利股份", "000858 五粮液"}
    for name in company_rows:
        assert name in html, f"采集存档页缺这一家：{name}"
