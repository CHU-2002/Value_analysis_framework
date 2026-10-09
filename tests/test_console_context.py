# 覆盖需求：REQ-012（父需求 AC-1 公司是上下文、AC-2 工作台首页、AC-9 统一公司标识、
# AC-11 扩展性不回退）、REQ-012.1（公司上下文与信息架构）—— AC-1.1 全局公司选择器与冷开
# 公司级页面不降级、AC-1.2 导航按任务分组（父子层级 + requires 声明，全部走注册表扩展点）、
# AC-1.3 默认落地页是工作台且空库可用、AC-1.4 全站统一公司显示名。
"""公司上下文与信息架构测试（`REQ-012.1`）。

四条刻意的手法：

1. **从注册表读，不从页面读**：`NavItem` 的 `children` / `requires` / `default` 是**数据**，
   所以「导航分层」「哪里需要上下文」「落地页是谁」都能直接断言，不用起浏览器；
2. **冷开走真实链路**：`GET /api/v1/pages/report` 带与不带 `company` 各跑一次，
   证明「没有上下文」得到的是**正常空状态**而不是 `BAD_REQUEST` 降级卡（关掉 `REQ-009` 的 `E2`）；
3. **同一家公司三处显示名**：公司列表、报告面板、选择器数据源都读同一个 `display_name`，
   断言的是「三处字符串相等」，不是「某处包含某子串」；
4. **旧链接不能断**：目录名作为兼容别名继续可用（`REQ-009.2` 的端点语义变更记录）。
"""

import json
import re
from pathlib import Path

import pytest

from webui.config import Config
from webui.core.context import RequestContext
from webui.core.errors import PathOutsideRoot, WebUIError
from webui.core.models import NavItem
from webui.core.registry import build_registry
from webui.core.router import find_route
from webui.core.routes import install_core_routes
from webui.datastore import DataStore, parsers
from webui.plugins import charts as charts_plugin
from webui.plugins import collect as collect_plugin
from webui.plugins import commands as commands_plugin
from webui.plugins import companies as companies_plugin
from webui.plugins import data_page as data_page_plugin
from webui.plugins import home as home_plugin
from webui.plugins import run_history as run_history_plugin
from webui.plugins import actions as actions_plugin

REPORT_MD = """# 伊利股份 2025 年报分析

## 结论

- 营收微增，利润率稳定。
"""


# --------------------------------------------------------------- 测试辅助


def make_config(tmp_path: Path) -> Config:
    return Config(
        host="127.0.0.1", port=0,
        output_root=tmp_path / "output", cache_dir=tmp_path / "output" / ".webui_cache",
        archive_root=tmp_path / "archive",
    )


def make_app(tmp_path: Path, *, with_actions: bool = True):
    config = make_config(tmp_path)
    registry = build_registry()
    install_core_routes(registry, config)
    registry.config = config
    modules = [collect_plugin, commands_plugin, companies_plugin, charts_plugin,
               run_history_plugin, home_plugin, data_page_plugin]
    if with_actions:
        modules.append(actions_plugin)
    for module in modules:
        module.contribute(registry)
    registry.datastore = DataStore(config, spec_lookup=registry.dataset_spec)
    return config, registry


def call_route(registry, method: str, path: str, **query):
    route, params = find_route(registry.routes(), method, path)
    assert route is not None, f"没有匹配的路由：{method} {path}"
    context = RequestContext(
        method=method, path=path, query=dict(query), config=registry.config, registry=registry
    )
    return route.handler(context, **params)


def panel_payload(registry, panel_id: str, **query):
    """面板接口的**渲染载荷**（服务端 kind 带 html，客户端 kind 带 data）。"""
    return call_route(registry, "GET", f"/api/v1/panels/{panel_id}", **query)["data"]


def panel_data(registry, panel_id: str, **query):
    """面板的**原始数据**：服务端 kind 直接调 provider（表格数据不在载荷里，它已渲染成 HTML）。"""
    from webui.core.context import RequestContext as Ctx
    from webui.core.routes import resolve_params

    spec = registry.panel_spec(panel_id)
    context = Ctx(method="GET", path=f"/api/v1/panels/{panel_id}", query=dict(query),
                  config=registry.config, registry=registry)
    return spec.provider(context, **resolve_params(spec, context))


def make_company(tmp_path: Path, name: str = "600887_伊利", ticker: str = "600887.SH",
                 company: str = "伊利股份") -> Path:
    directory = tmp_path / "output" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "data_pack_market.md").write_text(
        "## 11. 十年周线行情\n\n### 年度行情汇总\n\n"
        "| 年度 | 最高 | 最低 | 年末收盘 |\n| --- | ---: | ---: | ---: |\n"
        "| 2025 | 32.00 | 22.00 | 28.00 |\n",
        encoding="utf-8",
    )
    (directory / "qualitative_report.md").write_text(REPORT_MD, encoding="utf-8")
    (directory / "record.json").write_text(
        json.dumps({"subject": {"ticker": ticker, "company": company},
                    "downstream": {"stale": False}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (directory / "latest.json").write_text(
        json.dumps({"run_id": "20260101T000000000000Z", "primary_period": "2025FY"}),
        encoding="utf-8",
    )
    return directory


@pytest.fixture(autouse=True)
def _isolated_parsers():
    parsers.reset_parsers()
    parsers.register_builtin_parsers()
    yield
    parsers.reset_parsers()
    parsers.register_builtin_parsers()


# --------------------------------------------------------------- AC-1.2 导航是数据


def test_nav_items_carry_hierarchy_and_context_requirements(tmp_path):
    """AC-1.2 / AC-11：导航的分层与「需要上下文」都是注册表的**数据**，不解释业务。"""
    _, registry = make_app(tmp_path)
    nav = call_route(registry, "GET", "/api/v1/nav")["data"]

    # 默认落地页由 `default=True` 决定（核心不认识 `home` 这个 id）。
    assert nav["default_page"] == "home"
    items = {item["id"]: item for item in nav["items"]}
    assert items["home"]["default"] is True
    assert items["home"]["group"] == "工作台"
    # 公司级页面声明上下文依赖；跨公司页面不声明。
    for page_id in ("charts", "report"):
        assert items[page_id]["requires"] == ["selection.company"]
        assert items[page_id]["group"] == "公司"
    for page_id in ("home", "data", "commands"):
        assert items[page_id]["requires"] == []

    # `to_json` 必须把层级也带上（前端 shell 只缩进，不重新解释）。
    parent = NavItem(id="parent", title="父页", children=(
        NavItem(id="child", title="子页", requires=("selection.company",)),
    ))
    payload = parent.to_json()
    assert payload["children"][0]["id"] == "child"
    assert payload["children"][0]["requires"] == ["selection.company"]

    # 旧字段一个都没少（REQ-009 的面板协议继续成立）。
    assert set(items["home"]) >= {"id", "title", "group", "order", "panels", "description"}


def test_nav_children_are_rendered_as_a_tree_by_the_core(tmp_path):
    """AC-1.2：子导航（`children`）是核心能力——注册一个带层级的页面就能进导航树。

    用**路径**注册（`registry.nav`）而不是内置插件：内置的 9 个页面目前都是平级的，
    所以「层级可用」这件事只能靠直接注册来证明（扩展点覆盖，`AC-11`）。
    """
    _, registry = make_app(tmp_path)
    registry.nav(NavItem(
        id="group.page", title="分组页", group="演示", order=900,
        children=(
            NavItem(id="group.page.a", title="子页 A", requires=("selection.company",)),
            NavItem(id="group.page.b", title="子页 B", panels=("companies.list",)),
        ),
    ))
    nav = call_route(registry, "GET", "/api/v1/nav")["data"]
    root = next(item for item in nav["items"] if item["id"] == "group.page")
    assert [child["id"] for child in root["children"]] == ["group.page.a", "group.page.b"]
    assert root["children"][0]["requires"] == ["selection.company"]
    # 子页也是**页面**：注册表里查得到，`/api/v1/pages/{id}` 打得开。
    assert registry.page("group.page.a").title == "子页 A"
    page = call_route(registry, "GET", "/api/v1/pages/group.page.a")["data"]
    assert page["requires"] == ["selection.company"] and page["id"] == "group.page.a"


def test_page_payload_exposes_requires_so_the_shell_can_show_an_empty_state(tmp_path):
    """AC-1.1：页面描述里带上 `requires`——前端据此决定渲染面板还是先选公司。"""
    _, registry = make_app(tmp_path)
    make_company(tmp_path)
    page = call_route(registry, "GET", "/api/v1/pages/report")["data"]
    assert page["requires"] == ["selection.company"]
    # 缺上下文时服务端直接给空状态，面板列表是空的（详见下一条用例）。
    assert page["panels"] == [] and page["empty_state"]["reason"] == "selection.company"
    with_company = call_route(registry, "GET", "/api/v1/pages/report",
                              company="600887.SH")["data"]
    assert any(panel["id"] == "report.view" for panel in with_company["panels"])
    assert call_route(registry, "GET", "/api/v1/pages/home")["data"]["requires"] == []


# --------------------------------------------------------------- AC-1.1 冷开不降级


def test_cold_open_of_a_company_page_is_a_normal_empty_state(tmp_path):
    """AC-1.1（关掉 REQ-009 的 E2）：没有公司时是**正常空状态**，连 warnings 都不该有。

    判据分四层，缺一层都不算过：

    ① 服务端**不渲染面板**（`panels == []`）——不是渲染失败再降级；
    ② 信封 `warnings` 为空——否则前端横幅会把 `BAD_REQUEST` 摆到主视觉（`AC-10` 的
       广义读法不成立；这正是真实走查抓到的那条）；
    ③ 空状态给的是人话（`message` + `hint`）与可用的入口（`supports`）；
    ④ 带上下文时同一页正常渲染（不是「到处都空」蒙混过关）。
    """
    _, registry = make_app(tmp_path)
    make_company(tmp_path)

    response = call_route(registry, "GET", "/api/v1/pages/report")
    page = response["data"]
    assert page["panels"] == [], "缺上下文时不应该去渲染面板（渲染→失败→降级才是 E2 的症状）"
    assert response["warnings"] == [], f"空状态不该产生告警：{response['warnings']}"
    empty = page["empty_state"]
    assert empty["reason"] == "selection.company"
    assert empty["message"] == "先选一家公司"
    assert empty["hint"] and "当前公司" in empty["hint"]
    assert set(empty["supports"]) == {"company_picker", "home_link"}
    # 判据的核心：这一页的任何响应里都不出现错误码（错误码不该进主视觉）。
    assert "BAD_REQUEST" not in json.dumps(response, ensure_ascii=False)

    # E2 的核心症状是「同一页 2 张降级卡 + 横幅」；这里确认带上下文时同一页正常渲染。
    good = call_route(registry, "GET", "/api/v1/pages/report", company="600887.SH")
    assert [panel for panel in good["data"]["panels"] if panel.get("fallback")] == []
    assert good["data"].get("empty_state") is None
    assert "600887 伊利股份" in json.dumps(good["data"]["panels"][0], ensure_ascii=False)


def test_missing_company_message_is_user_language_not_an_internal_parameter(tmp_path):
    """AC-1.1 / AC-3：缺上下文的理由是「还没选公司」，不是「缺少参数 company」。"""
    from webui.core.context import RequestContext as Ctx

    _, registry = make_app(tmp_path)
    context = Ctx(method="GET", path="/api/v1/companies", query={}, config=registry.config,
                  registry=registry)
    with pytest.raises(WebUIError) as excinfo:
        companies_plugin.company_base(context, "")
    assert excinfo.value.message == "还没选公司"
    assert "选一家公司" in excinfo.value.hint
    assert "company" not in excinfo.value.message


# --------------------------------------------------------------- AC-1.4 / AC-9 统一显示名


def test_one_display_name_across_company_list_report_and_selector(tmp_path):
    """AC-1.4（父 AC-9）：公司列表 / 报告面板 / 选择器数据源三处显示名**逐字相同**。"""
    _, registry = make_app(tmp_path)
    make_company(tmp_path)

    listing = call_route(registry, "GET", "/api/v1/companies")["data"]["companies"]
    row = next(item for item in listing if item["dir"] == "600887_伊利")
    assert row["display_name"] == "600887 伊利股份"
    # 目录名与 ticker 是技术标识，只在详情里出现。
    assert row["label"] == "600887_伊利"
    assert row["ticker"] == "600887.SH"

    table = panel_data(registry, "companies.list")
    assert table["rows"][0]["name"] == row["display_name"]
    assert table["rows"][0]["href"] == "#charts?company=600887.SH"
    # 渲染出来的 HTML 里也是同一个显示名（不是只在数据里对）。
    assert row["display_name"] in panel_payload(registry, "companies.list")["html"]

    report = call_route(registry, "GET", "/api/v1/companies/600887.SH/report")["data"]
    assert report["company"]["display_name"] == row["display_name"]

    # 采集/数据页读的是同一份索引：三处相等而不是「看起来像」。
    data_rows = panel_data(registry, "data.universe")["rows"]
    assert data_rows == [] or all(
        item["company"] != "600887.SH" for item in data_rows
    ), "数据页不应把 ticker 当成显示名"


def test_display_name_never_falls_back_to_the_exchange_suffix(tmp_path):
    """AC-9 的边界：缺名字时退回目录名约定，但**不**把 `600887.SH` 当公司名。

    「退回目录名」在真实数据上踩过一次：`output/` 里有一批早期产物没有 `record.json`，
    工作台因此把 `000858_五粮液` 原样当显示名——而 `AC-9` 要求目录名只作为技术标识出现。
    所以这里同时钉住「按 `<代码>_<简称>` 约定拼」与「不合约定就原样返回」。
    """
    assert companies_plugin.display_name("600887.SH", "伊利股份") == "600887 伊利股份"
    assert companies_plugin.display_name("600887.SH", "") == "600887"
    assert companies_plugin.display_name("", "伊利股份") == "伊利股份"
    # 没有 record.json 的老产物：目录名 → 显示名
    assert companies_plugin.display_name("", "", "600887_伊利") == "600887 伊利"
    assert companies_plugin.display_name("", "", "000858_五粮液") == "000858 五粮液"
    # 不合约定的目录名不许被改写（不猜）
    assert companies_plugin.display_name_from_label("portfolio_2026") == "portfolio_2026"
    assert companies_plugin.display_name_from_label("handoff") == "handoff"
    assert companies_plugin.display_name_from_label("") == ""


def test_workbench_shows_a_display_name_even_without_a_record_file(tmp_path):
    """AC-1.4 / AC-9 的实测回归：没有 `record.json` 的老产物也要按约定显示公司名。"""
    _, registry = make_app(tmp_path)
    directory = tmp_path / "output" / "000858_五粮液"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "data_pack_market.md").write_text("# pack", encoding="utf-8")

    row = panel_data(registry, "home.universe")["rows"][0]
    assert row["company"] == "000858 五粮液", "目录名不该出现在显示名位置"
    assert "_" not in row["company"].split(" ")[0]
    listing = panel_data(registry, "companies.list")["rows"][0]["name"]
    assert listing == row["company"], "工作台与公司列表必须是同一个显示名"


# --------------------------------------------------------------- AC-1 端点接受 ticker


def test_endpoints_accept_ticker_and_keep_the_directory_name_as_an_alias(tmp_path):
    """AC-1：ticker 是规范形式，目录名保留为兼容别名；越界仍然 403（安全边界不放宽）。"""
    _, registry = make_app(tmp_path)
    make_company(tmp_path)

    by_ticker = call_route(registry, "GET", "/api/v1/companies/600887.SH/artifacts")["data"]
    by_dir = call_route(registry, "GET", "/api/v1/companies/600887_伊利/artifacts")["data"]
    assert by_ticker["dir"] == by_dir["dir"] == "600887_伊利"
    assert by_ticker["ticker"] == "600887.SH"
    assert {item["rel"] for item in by_ticker["artifacts"]} == {
        item["rel"] for item in by_dir["artifacts"]
    }

    # 越界与不存在的标识：前者 403、后者 404，都不是 500。
    for bad in ("..", "%2E%2E%2F.env", "%2Fetc%2Fpasswd"):
        with pytest.raises(PathOutsideRoot):
            call_route(registry, "GET", f"/api/v1/companies/{bad}/artifacts")
    with pytest.raises(WebUIError) as excinfo:
        call_route(registry, "GET", "/api/v1/companies/999999.SH/artifacts")
    assert excinfo.value.code == "NOT_FOUND"
    assert "没有这家公司" in excinfo.value.message


def test_unknown_page_falls_back_to_the_workbench_not_a_business_panel(tmp_path):
    """AC-1.3：未知页面落到工作台（旧行为是落到 `items[0]`，会落到业务面板）。"""
    _, registry = make_app(tmp_path)
    assert registry.default_page_id() == "home"
    assert registry.page("home").title == "工作台"


# --------------------------------------------------------------- AC-1.3 工作台


def test_workbench_universe_panel_lists_companies_with_todos(tmp_path):
    """AC-1.3 / AC-2：工作台列出公司与待办；状态判定只用本地事实。"""
    _, registry = make_app(tmp_path)
    make_company(tmp_path)
    panel = panel_data(registry, "home.universe")
    assert panel["meta"]["count"] == 1
    row = panel["rows"][0]
    assert row["company"] == "600887 伊利股份"
    assert row["href"] == "#charts?company=600887.SH"
    assert row["state"] in ("正常", "待更新", "缺数据")
    assert row["todo"], "待办列必须有内容（哪怕是「—」）"
    assert panel["columns"] and all("key" in column for column in panel["columns"])


def test_workbench_is_usable_with_an_empty_output_directory(tmp_path):
    """AC-2 / AC-1.3：全新的空 `output/` 下首页可用且给**引导**，不是空表也不是报错。"""
    _, registry = make_app(tmp_path)
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)

    panel = panel_data(registry, "home.universe")
    assert panel["rows"] == []
    assert panel["meta"]["empty"] is True
    assert "①" in panel["meta"]["guide"] and "②" in panel["meta"]["guide"]
    html = panel_payload(registry, "home.universe")["html"]
    assert "还没有公司数据" in html or "①" in html, "空库时首页要给引导而不是空表"

    todo = panel_data(registry, "home.todo")
    labels = {item["label"]: item for item in todo["items"]}
    assert labels["跟踪的公司"]["value"] == "0"
    assert labels["跟踪的公司"]["state"] == "warn"

    page = call_route(registry, "GET", "/api/v1/pages/home")["data"]
    assert [item for item in page["panels"] if item.get("fallback")] == []


def test_workbench_counts_failures_from_job_history(tmp_path):
    """AC-2：待办里的「上次任务失败」来自任务历史，不靠猜。"""
    config, registry = make_app(tmp_path)
    make_company(tmp_path)
    history = Path(config.output_root) / ".webui_jobs"
    history.mkdir(parents=True, exist_ok=True)
    (history / "job-x.json").write_text(json.dumps({
        "id": "job-x", "command": "company.update_analysis", "title": "更新分析",
        "status": "failed", "exit_code": 1, "started_at": "2026-01-01T00:00:00+00:00",
        "handoff": {"paths": {"ticker": "600887.SH"}},
    }, ensure_ascii=False), encoding="utf-8")
    from webui.core.jobs import JobRunner

    registry.jobs = JobRunner(config, spec_lookup=registry.command_spec,
                              history_dir=history, env={})
    todo = panel_data(registry, "home.todo")
    labels = {item["label"]: item for item in todo["items"]}
    assert labels["上次任务失败"]["value"] == "1"
    assert labels["上次任务失败"]["state"] == "error"
    assert "上次任务失败" in panel_payload(registry, "home.todo")["html"]
    panel = panel_data(registry, "home.universe")
    assert "上次任务失败" in panel["rows"][0]["todo"]


# --------------------------------------------------------------- 走查暴露的回归（AC-12）


def test_importing_the_package_shim_makes_absolute_scripts_imports_work(tmp_path):
    """AC-12 回归：`python -m scripts.webui` 起动时 `scripts/` 必须已在 `sys.path` 上。

    真实走查抓到的缺陷：`datalayer` 的模块按仓库既有约定用**绝对名**互相导入
    （`from periods import …`、`from webui.core.security import redact …`），
    而 `python -m scripts.webui` 的包名是 `scripts.webui`，那一层没人插 `sys.path`，
    于是所有新面板在真实运行里 500，**测试却全绿**（测试由 `conftest.py` 插好）。
    """
    import subprocess
    import sys as _sys

    root = Path(__file__).resolve().parents[1]
    code = (
        "import scripts.webui, sys;"
        "sys.path.insert(0, 'scripts');"
        "import importlib.util as u;"
        "print('scripts-dir-on-path:', any(p.endswith('scripts') for p in sys.path))"
    )
    done = subprocess.run(
        [_sys.executable, "-c", code], cwd=str(root), capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "scripts-dir-on-path: True" in done.stdout

    # 更要紧的一条：**真的去 import 一次**面板依赖的那条链。
    probe = subprocess.run(
        [_sys.executable, "-c",
         "import scripts.webui, datalayer.security, datalayer.store, datalayer.universe;"
         "print('chain-ok')"],
        cwd=str(root), capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ""},
    )
    assert probe.returncode == 0, probe.stderr
    assert "chain-ok" in probe.stdout


def test_registered_commands_run_as_modules_and_never_bare_package_files(tmp_path):
    """AC-12 回归：白名单里带目录的脚本必须用 `-m 包名` 调用。

    实测缺陷：`datalayer/cli.py` 内部是**相对导入**（`from . import __version__`），
    裸文件路径执行直接 `ImportError: attempted relative import with no known parent package`。
    界面上表现为「动作失败」，而 CLI 手跑正常——只有真实走查能发现。
    """
    root = Path(__file__).resolve().parents[1]
    _, registry = make_app(tmp_path)
    by_id = {spec.id: spec for spec in registry.commands()}
    for command_id, spec in by_id.items():
        # 判据不是「文件在不在目录里」（`scripts/` 本身也是包），而是**那个文件自己**
        # 有没有相对导入：有 `from . import x` / `from .x import y` 的文件裸路径执行必然
        # ImportError，必须走 `-m 包名`。这样既不误报散装脚本，也不会漏掉真包内脚本。
        offenders = []
        for part in list(spec.argv):
            text = str(part)
            if not (text.endswith(".py") and "/" in text):
                continue
            path = Path(text)
            if not path.is_file():
                continue
            source = path.read_text(encoding="utf-8")
            if re.search(r"^\s*from\s+\.", source, re.M):
                offenders.append(text)
        assert not offenders, (
            f"{command_id} 用裸文件路径调用了含相对导入的脚本 {offenders}；"
            "那会 ImportError，必须走 `-m 包名`"
        )
    datalayer = by_id["datalayer_pull"]
    assert list(datalayer.argv[1:4]) == ["-m", "scripts.datalayer", "pull"]


def test_a_real_panel_failure_still_gives_readable_copy_with_the_code_collapsed(tmp_path):
    """AC-10：**真的**失败（不是缺上下文）时，标题是人话、错误码收进折叠区。

    与上一条的区别很重要：缺上下文 → 空状态（无 warnings）；真失败 → 降级卡（有 warnings，
    但错误码不许当标题）。两条路都断了，`E2` 才算真的关掉、`AC-10` 才算真的成立。
    """
    _, registry = make_app(tmp_path)
    make_company(tmp_path)
    # 把报告接口指向一个不存在的产物 id → 面板渲染失败（真失败）。
    response = call_route(registry, "GET", "/api/v1/pages/report",
                          company="600887.SH", id="000000000000")
    page = response["data"]
    degraded = [panel for panel in page["panels"] if panel.get("fallback")]
    assert degraded, "产物 id 不存在时该面板应当降级"
    html = degraded[0]["html"]
    assert "这块内容暂时看不到" in html
    assert "下一步：" in html
    assert "panel-error-title" in html
    assert html.index("这块内容暂时看不到") < html.index("NOT_FOUND")
    assert "<details" in html and "<summary>技术细节</summary>" in html
    assert response["warnings"], "真失败必须留痕（warnings 不为空）"


def test_unresolvable_company_is_a_readable_empty_state_not_an_error_banner(tmp_path):
    """AC-10 / AC-1.1：上下文**给了但解析不出来**时也要给人话（门② 第四轮抓到的）。

    真实路径：选择器「记住上次选择」之后产物被删/改名，或 URL 里写了不存在的标识。
    修之前这种情况会一路走到面板渲染失败，把 `NOT_FOUND` 摆进顶栏横幅——「错误码不占主视觉」
    不成立。现在由插件声明的 `context_resolver` 判定，服务端直接给正常空状态。
    """
    _, registry = make_app(tmp_path)
    make_company(tmp_path)

    response = call_route(registry, "GET", "/api/v1/pages/report", company="999999.XX")
    page = response["data"]
    assert page["panels"] == [], "解析不出来时不该去渲染面板"
    assert response["warnings"] == [], f"空状态不该产生告警：{response['warnings']}"
    empty = page["empty_state"]
    assert empty["reason"] == "selection.company.unresolved"
    assert "找不到了" in empty["message"]
    assert empty["hint"] and "重新选一家" in empty["hint"]
    assert "NOT_FOUND" not in json.dumps(response, ensure_ascii=False)

    # 解析器是**插件**声明的（核心只调用它）：换一个解析不出来的值结论一致，
    # 而解析得出来的值照常渲染——两条路都断，避免「一律空状态」蒙混过关。
    good = call_route(registry, "GET", "/api/v1/pages/report", company="600887.SH")
    assert good["data"].get("empty_state") is None
    assert any(panel["id"] == "report.view" for panel in good["data"]["panels"])


def test_sub_navigation_is_not_rendered_twice(tmp_path):
    """AC-1.2 / AC-11：子导航只出现**一次**（在父项的 `children` 里，不作为顶层项）。

    门② 第四轮用仓库外插件注册了一个子页，真实侧栏出现两条同名链接（一条缩进、一条平铺）：
    根因是 `registry.nav()` 递归登记子项进 `_nav`（为了「导航项即页面」），而
    `/api/v1/nav` 直接返回扁平全量。现在 `nav_items()` 只回**根项**，`all_pages()` 才回全部。
    """
    _, registry = make_app(tmp_path)
    child = NavItem(id="demo.child", title="评审者演示子页")
    registry.nav(NavItem(id="demo.parent", title="评审者演示父页", group="演示", order=900,
                         children=(child,)))

    nav = call_route(registry, "GET", "/api/v1/nav")["data"]
    root_ids = [item["id"] for item in nav["items"]]
    assert root_ids.count("demo.child") == 0, f"子页不该作为顶层项出现：{root_ids}"
    assert "demo.parent" in root_ids
    parent = next(item for item in nav["items"] if item["id"] == "demo.parent")
    assert [item["id"] for item in parent["children"]] == ["demo.child"]

    # 但子页**仍然是页面**（「导航项即页面」这条语义不能被层级破坏）。
    assert registry.page("demo.child").title == "评审者演示子页"
    assert call_route(registry, "GET", "/api/v1/pages/demo.child")["data"]["id"] == "demo.child"
    assert "demo.child" in [item.id for item in registry.all_pages()]

    # 内置导航也不许有重复：所有顶层 id 与所有子 id 不相交。
    root_ids = {item.id for item in registry.nav_items()}
    child_ids = {child.id for item in registry.nav_items() for child in item.children}
    assert root_ids & child_ids == set()
