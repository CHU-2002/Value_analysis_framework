# 覆盖需求：REQ-012（父需求 AC-3 意图化动作与文案禁则、AC-4 预检与危险确认、AC-5 任务中心、
# AC-11 扩展性不回退）、REQ-012.2（任务式动作层）—— AC-2.1 意图命名 + 参数预填 + 文案禁则、
# AC-2.2 执行前预检与危险动作显式确认、AC-2.3 任务中心（进度/取消/重试/折叠日志/失败原因/产物）、
# AC-2.4 多步编排与人机交接（含服务端产物校验）。
"""动作层测试（`REQ-012.2`）。

四条刻意的手法：

1. **不跑真实业务脚本**：编排用的假按键是 `sys.executable -c`（打印/退出码/写文件），
   不起网络、不碰仓库；动作声明由测试自己注册，证明「动作只编排既有按键」；
2. **拒绝与预检在起进程之前**：给 `JobRunner` 注入「一被调用就失败」的 `popen`，
   预检不通过却起了进程的话测试立刻炸（判据不是「返回 4xx」而是「没起进程」）；
3. **交接靠产物说话**：`continue` 在产物不存在/不新鲜时**必须**停在原地，
   这是「没跑就点继续」的唯一防线，所以正反两个方向都要断言；
4. **文案禁则扫响应**：`AC-3` 的「界面不出现内部参数名/CLI 开关/绝对路径」被写成
   一条扫描 `/api/v1/nav`、`/api/v1/pages/*`、`/api/v1/actions` 的断言——
   可判定，不靠评审人眼。
"""

import json
import re
import sys
import time
from pathlib import Path

import pytest

from webui.config import Config
from webui.core.context import RequestContext
from webui.core.errors import BadRequest, InvalidParam, TooManyJobs, UnknownCommand
from webui.core.jobs import AWAITING, FAILED, FINISHED, Job, JobRunner
from webui.core.models import (
    CommandSpec,
    CommandStep,
    HumanStep,
    JobTypeSpec,
    NavItem,
    PanelSpec,
    Param,
)
from webui.core.registry import build_registry
from webui.core.router import find_route
from webui.core.routes import install_core_routes
from webui.datastore import DataStore, parsers
from webui.plugins import commands as commands_plugin

#: 面向用户文本里的禁则（`AC-3` / `AC-2.1`）：内部参数名、CLI 开关、绝对路径、解释器调用。
FORBIDDEN = (
    r"company_dir", r"output_dir", r"run_dir", r"\binput\b", r"\bforce\b",
    r"--only-gaps", r"--yes", r"/Users/", r"\.venv", r"\bpython\b",
    # 独立验收第二轮抓到的漏网：空状态引导里写着 `make data-universe ARGS='add --ticker …'`。
    # 补两条**零误报**的形状判据（比逐个列开关名更稳，新加开关也不必改测试）：
    #   `--<短横线开关>`：面向用户的文案里不该出现任何命令行开关；
    #   `make <目标>`：不该教用户去敲 make 目标。
    r"--[a-z][a-z0-9-]*", r"\bmake\s+[a-z][a-z0-9-]*",
    # 接口路径也不该进用户文案（门② delta 复核抓到：`companies.py` 的 hint 里写着
    # `/api/v1/companies`，它会经 `actions.py` 拼进**阻断理由**摆到界面上）。
    r"/api/",
)
# 说明：`latest` 刻意**不在**禁则里——它既是内部参数名（`--latest`），也是产物字段与
# 普通英文词的正当字样（`latest.json`、`latest_run`、`defaults to latest fiscal year`）。
# 禁则要能一眼判定、零误报，宁可少一条也不要把正常数据判成违规
# （`AC-3` 的原意是「界面**要求用户输入**内部参数」，不是「文档里不许出现这个词」）。
_TEXT_KEYS = (
    "title", "description", "label", "hint", "message", "note", "guide", "help",
    # `empty_hint` 是空状态文案的另一个常用键（门② 第二轮点名过它不在表里——
    # 那时它只被 `meta` 承载、又被 `meta` 的豁免一起漏掉，两处一起补）。
    "empty_hint", "summary", "blockers", "confirm_label", "body",
)
#: `meta` 里允许携带面向用户的文案（空状态引导就常放在 `meta.guide`）：
#: 它是**数据来源说明**的容器，所以按同一个禁则扫——但只扫文本键，不扫指纹/路径这类值。
_META_SCAN = True
#: **技术视图**：按键目录（`kind=form` 的 `commands.catalog`）的参数名与开关由各脚本
#: 的 argparse 扫描得出，属 `REQ-009.1` 的 `AC-1.2` 要求明示的内容；owner 2026-10-09
#: 裁定它不受 `AC-3` 的文案禁则约束（见 `REQ-012` 的「## 备注」）。
TECHNICAL_VIEW_PANELS = ("commands.catalog",)


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


ECHO = CommandSpec(id="step_echo", argv=(sys.executable, "-c", "print('hello')"), title="回显")
FAIL = CommandSpec(id="step_fail", argv=(sys.executable, "-c", "import sys; sys.exit(4)"),
                   title="失败")
#: 写文件的演示步骤：路径从**环境变量**读（`build_argv` 会把参数拼成 `--out 路径`，
#: 而子进程的 cwd 是仓库根——用环境变量既不受 cwd 影响，也不依赖 argv 位置）。
_WRITE_SOURCE = (
    "import os,pathlib;"
    "p=pathlib.Path(os.environ['DEMO_WRITE']);"
    "p.parent.mkdir(parents=True, exist_ok=True);"
    "p.write_text('x')"
)
WRITE = CommandSpec(
    id="step_write",
    argv=(sys.executable, "-c", _WRITE_SOURCE),
    title="写产物",
    description="把 DEMO_WRITE 环境变量指向的文件写出来（测试用）。",
    params=(Param("out", required=True),),
)
NOPE = CommandSpec(
    id="step_nope",
    argv=(sys.executable, "-c", "print('boom'); import sys; sys.exit(4)"),
    title="报错的步骤",
)
TOKEN_ECHO = CommandSpec(
    id="step_token", argv=(sys.executable, "-c", "print('token-is-secret-value')"), title="凭据",
)


def _probe_frame():
    """扫描夹具用的一行数据（只为让原始仓非空，内容不重要）。"""
    import pandas as pd

    return pd.DataFrame({"ts_code": ["600887.SH"], "trade_date": ["20260101"], "close": [1.0]})


def make_app(tmp_path: Path, *, max_jobs: int = 4, max_queued: int = 4, env=None, **overrides):
    config = make_config(tmp_path, max_concurrent_jobs=max_jobs, max_queued_jobs=max_queued,
                         **overrides)
    registry = build_registry()
    install_core_routes(registry, config)
    registry.config = config
    commands_plugin.contribute(registry)
    for spec in (ECHO, FAIL, WRITE, NOPE, TOKEN_ECHO):
        registry.command(spec)
    registry.datastore = DataStore(config, spec_lookup=registry.dataset_spec)
    runner_env = dict(env or {})
    runner_env.setdefault("DEMO_WRITE", str(tmp_path / "output" / "unset.txt"))
    registry.jobs = JobRunner(
        config, spec_lookup=registry.command_spec, job_lookup=registry.job_type_spec,
        history_dir=tmp_path / "jobs", env=runner_env,
    )
    return config, registry


def with_write_env(registry, tmp_path: Path, target: Path):
    """让 `step_write` 这个演示步骤写到 `target`（子进程只能通过环境变量拿到路径）。"""
    registry.jobs._env["DEMO_WRITE"] = str(target)
    return target


def call_route(registry, method: str, path: str, body=None, **query):
    route, params = find_route(registry.routes(), method, path)
    assert route is not None, f"没有匹配的路由：{method} {path}"
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    context = RequestContext(
        method=method, path=path, query=dict(query), body=raw,
        config=registry.config, registry=registry,
    )
    return route.handler(context, **params)


#: 「还没结束」的状态：`awaiting_agent` 也算——它是一次**合法的暂停**，
#: 收到「继续/放弃」后线程还要跑完剩下的步骤才收口。等它时不能提前返回。
_PENDING = ("running", "queued", "awaiting_agent")


def wait_history_finished(registry, job_id: str, timeout: float = 5.0) -> dict:
    """等这个任务的**历史文件里出现终态**（不是「文件存在」就算数）。

    门② 第八轮把上一版等待点证伪了：历史文件在 `submit()` 阶段就已落盘（`status=running`），
    所以 `path.exists()` **立即返回**，根本没等到最后一次写入——CI 上偶发的
    `assert 0 >= 1` 依然能发生。正确判据是「读得出来、且已经是终态」。

    与之配套的产品侧修法是 `JobRunner._write_history()` 改成**原子写**
    （临时文件 + `os.replace`），否则读者会遇到「存在但为空/半截」的文件，
    而 `_load_history()` 会静默跳过它（那条路径现在也会重读一次并留痕迹）。
    """
    path = Path(registry.jobs.history_dir) / f"{job_id}.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8") or "{}")
            except (OSError, json.JSONDecodeError):
                payload = {}
            if payload.get("status") not in (None, "running", "queued", "awaiting_agent"):
                return payload
        time.sleep(0.02)
    raise AssertionError(f"任务 {job_id} 的历史文件里始终没有终态：{path}")


def wait_job(registry, job_id: str, timeout: float = 15.0, until=None) -> dict:
    """等到任务收口（或满足 `until(job)`）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = registry.jobs.get(job_id)
        if until is not None and until(job):
            return job
        if until is None and job["status"] not in _PENDING:
            return job
        time.sleep(0.03)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内没有结束：{registry.jobs.get(job_id)}")


def wait_status(registry, job_id: str, status, timeout: float = 10.0) -> dict:
    """等到任务进入某个状态（可以是多个候选，例如「开始跑了」= running 或 awaiting_agent）。

    为什么允许候选集合：`POST` 返回时任务已经在另一个线程里跑了，**返回的状态本身是竞态**
    （交接步可能在响应组装完之前就进 `awaiting_agent`）。判据要写成「它已经开始」，
    而不是「它此刻恰好是 running」。
    """
    wanted = (status,) if isinstance(status, str) else tuple(status)
    return wait_job(registry, job_id, timeout=timeout,
                    until=lambda job: job["status"] in wanted)


def register_demo_action(registry, *, steps=None, preflight=None, danger=False,
                         requires=(), effects=None) -> JobTypeSpec:
    spec = JobTypeSpec(
        id="demo.chain",
        title="演示动作",
        description="测试用的动作声明。",
        group="演示",
        requires=requires,
        steps=steps if steps is not None else (CommandStep("step_echo"),),
        effects=effects if effects is not None else {"network": False, "quota": False},
        danger=danger,
        preflight=preflight,
        confirm={"title": "确认执行？", "body": "会发生些什么。", "confirm_label": "执行"} if danger else {},
    )
    registry.job_type(spec)
    return spec


@pytest.fixture(autouse=True)
def _isolated_parsers():
    parsers.reset_parsers()
    parsers.register_builtin_parsers()
    yield
    parsers.reset_parsers()
    parsers.register_builtin_parsers()


# --------------------------------------------------------------- AC-2.1 参数不手敲


def test_action_steps_are_composed_from_existing_commands_only(tmp_path):
    """AC-2.1 / 反模式：动作只能编排**已注册**的按键；引用不存在的按键在起进程前失败。"""
    _, registry = make_app(tmp_path)
    register_demo_action(registry, steps=(CommandStep("does_not_exist"),))
    with pytest.raises(UnknownCommand) as excinfo:
        call_route(registry, "POST", "/api/v1/actions/demo.chain/run",
                   body={"context": {"company": "600887_伊利"}})
    assert "does_not_exist" in excinfo.value.message
    assert "已注册" in excinfo.value.hint


def test_action_parameters_come_from_context_not_from_the_user(tmp_path):
    """AC-2.1：`company` / `ticker` / `run_dir` 由上下文与捕获值填充，用户不填路径。"""
    _, registry = make_app(tmp_path)
    resolved = {}

    def record_popen(argv, **kwargs):
        resolved["argv"] = argv
        raise AssertionError("这一步只是为了看参数，不该真的跑")

    registry.jobs = JobRunner(
        registry.config, spec_lookup=registry.command_spec,
        job_lookup=registry.job_type_spec, history_dir=tmp_path / "jobs2", env={},
        popen=record_popen,
    )
    register_demo_action(registry, steps=(
        CommandStep("step_write", bind={"out": "{company_dir}/inputs/data.json"}),
    ))
    context = {"company": "600887_伊利",
               "company_dir": str(tmp_path / "output" / "600887_伊利"),
               "ticker": "600887.SH"}
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run",
                     body={"context": context})["data"]
    assert job["steps"][0]["argv"][-1].endswith("600887_伊利/inputs/data.json")
    # 完整命令行仍然可在详情里查到（审计价值不删，AC-2.1 的例外）。
    assert any(part.endswith("data.json") for part in job["argv"])


def test_unresolvable_placeholder_is_rejected_before_any_subprocess(tmp_path):
    """AC-2.1 的边界：动作里写了一个解析不出来的占位符 → 422，**不猜也不起进程**。"""
    _, registry = make_app(tmp_path)
    calls = []

    def exploding_popen(*args, **kwargs):  # pragma: no cover
        calls.append(args)
        raise AssertionError("拒绝路径不该启动子进程")

    registry.jobs = JobRunner(
        registry.config, spec_lookup=registry.command_spec,
        job_lookup=registry.job_type_spec, history_dir=tmp_path / "jobs3", env={},
        popen=exploding_popen,
    )
    register_demo_action(registry, steps=(
        CommandStep("step_write", bind={"out": "{unknown_placeholder}/x"}),
    ))
    with pytest.raises(InvalidParam) as excinfo:
        registry.jobs.submit_action("demo.chain", {}, context={"company": "600887_伊利"})
    assert "unknown_placeholder" in excinfo.value.message
    assert calls == []


def test_action_wording_never_leaks_internal_parameters_or_cli_flags(tmp_path):
    """AC-2.1（父 AC-3）：扫 `/api/v1/nav`、`/api/v1/pages/*`、`/api/v1/actions` 的面向用户文本。

    判据是**可断言的禁则**：不出现 `company_dir` / `output_dir` / `run_dir` / `--only-gaps`
    这类内部名字，也不出现绝对路径与解释器调用。`details.argv` 是明确的例外（折叠在
    「技术细节」里的真实命令行），所以扫描时跳过以 `argv` 结尾的键。
    """
    from webui.plugins import actions as actions_plugin
    from webui.plugins import charts as charts_plugin
    from webui.plugins import collect as collect_plugin
    from webui.plugins import companies as companies_plugin
    from webui.plugins import data_page as data_page_plugin
    from webui.plugins import home as home_plugin
    from webui.plugins import run_history as run_history_plugin

    config, registry = make_app(tmp_path)
    for module in (actions_plugin, home_plugin, data_page_plugin, companies_plugin,
                   charts_plugin, run_history_plugin, collect_plugin):
        module.contribute(registry)
    # **要有数据**：空表渲染不出行，行值里的违规就扫不到（门② 第四轮正是用
    # 「把违规放回行值」证明扫描仍绿——那时是空仓，行不存在）。造一家公司 + 一份数据包。
    company = tmp_path / "output" / "600887_伊利"
    company.mkdir(parents=True, exist_ok=True)
    (company / "data_pack_market.md").write_text("# pack\n", encoding="utf-8")
    (company / "record.json").write_text(
        '{"subject": {"ticker": "600887.SH", "company": "伊利股份"}}', encoding="utf-8"
    )
    # **还要有仓与清单**：`estimate` 是从数据层的 `plan()` 算出来的，空仓/空清单时它恒为
    # `{}`，字段压根不在被扫的载荷里——门② 第七轮往 `estimate.detail` 注入 `--zz-inject`
    # 时用例仍然判绿，而那条文案真能上屏。所以这里建最小原始仓 + 一条清单，并**带上下文**
    # 取动作清单，让 `estimate` 真的有内容可扫。
    from datalayer.store import DataStore as _Store
    from datalayer.universe import Universe as _Universe

    store = _Store(config.archive_root)
    store.write_frame(ticker="600887.SH", dataset="daily", period="20260101",
                      params={"ts_code": "600887.SH"}, frame=_probe_frame(),
                      result="ok", fetched_at="2026-01-02T03:04:05+00:00")
    _Universe(store).add("600887.SH", "伊利股份")

    # **扫所有注册过的页面**，不手写清单：门② 第三轮用注入证明过手写清单有盲区
    # （往 `collect` 页的面板描述里注入 `--foo`，用例照样绿，因为清单里没有 collect）。
    payloads = [call_route(registry, "GET", "/api/v1/nav")]
    page_ids = [item.id for item in registry.all_pages()]   # 含子页（`nav_items()` 只回根项）
    assert "collect" in page_ids, "collect 页必须参与扫描（它是被漏掉过的那一页）"
    for page_id in page_ids:
        payloads.append(call_route(registry, "GET", f"/api/v1/pages/{page_id}"))
    # 带上下文取：`data.pull_all` / `data.fill_gaps` 的预估需要「这次要拉哪家」。
    payloads.append(call_route(registry, "GET", "/api/v1/actions", company="600887.SH"))
    # **再取一次「公司解析不出来」的载荷**：那条路径的 `blockers` 由
    # `f"{exc.message}：{exc.hint}"` 拼出来（`actions.py`），是接口路径最可能溜进用户文案的地方
    # ——门② 的 delta 复核就是这么抓到 `companies.py` 的 hint 里写着 `/api/v1/companies` 的，
    # 而**只看「公司存在」那一份载荷的判据抓不到**（它压根不渲染那条 blocker）。
    payloads.append(call_route(registry, "GET", "/api/v1/actions", company="999999.XX"))

    offenders = []

    def walk(node, key="", panel_id=""):
        if key.endswith("argv") or key.endswith("href") or key.endswith("endpoint"):
            return
        if isinstance(node, dict):
            # 记住当前面板：**技术视图**（各脚本 argparse 扫出来的按键目录）不看禁则——
            # owner 2026-10-09 的裁决把它排除在 `AC-3` 的适用范围之外（它的字段名与开关
            # 就是 `REQ-009.1` 的 `AC-1.2` 要求明示的那张表，删掉它会回退已验收能力）。
            current = node.get("id", panel_id) if node.get("kind") else panel_id
            for name, value in node.items():
                walk(value, name, current)
        elif isinstance(node, list):
            for value in node:
                walk(value, key, panel_id)
        elif isinstance(node, list) and key == "blockers":
            for value in node:
                walk(value, key, panel_id)
        elif isinstance(node, str) and key in _TEXT_KEYS:
            if panel_id in TECHNICAL_VIEW_PANELS:
                return
            for pattern in FORBIDDEN:
                if re.search(pattern, node):
                    offenders.append((key, pattern, node[:80]))

    for payload in payloads:
        walk(payload)
    assert offenders == [], f"面向用户的文案里出现了内部参数名/开关/路径：{offenders}"

    # **表格行值**也要扫（门② 第四轮：把上一轮的违规原样放回行值 `rows[].rebuild`，
    # 键名扫描不判红——因为行值不是「文案字段」，但它**渲染出来就是用户看到的文本**）。
    # 判据放在**渲染后的 HTML** 上，这样键名怎么组织都逃不掉。
    row_offenders = []
    for page_id in page_ids:
        page = call_route(registry, "GET", f"/api/v1/pages/{page_id}")["data"]
        for panel in page["panels"]:
            html = panel.get("html") or ""
            for pattern in FORBIDDEN:
                matched = re.search(pattern, html)
                if matched:
                    row_offenders.append((page_id, panel["id"], pattern, matched.group(0)))
    assert row_offenders == [], (
        f"渲染出来的页面里出现了命令/开关/内部参数名：{row_offenders}"
    )

    # **客户端渲染的声明**也要看：`kind=actions` 的面板在服务端只有占位符 HTML，
    # 它的文案（`effects.writes` / `estimate.detail` / `confirm.body`…）由前端渲染，
    # 上面那条「渲染 HTML」的判据够不到——门② 第五轮往 `effects.writes` 注入 `--foo`
    # 时扫描仍绿。这里直接扫**声明**本身。
    declaration_offenders = []
    actions_payload = call_route(registry, "GET", "/api/v1/actions", company="600887.SH")["data"]
    assert any(item.get("estimate") for item in actions_payload["actions"]), (
        "夹具必须让 estimate 有内容，否则 estimate.detail 不在扫描范围内（判据形同不存在）"
    )
    for action in actions_payload["actions"]:
        blob = json.dumps(
            {key: value for key, value in action.items()
             if key not in ("steps", "handler", "id")},
            ensure_ascii=False,
        )
        for pattern in FORBIDDEN:
            matched = re.search(pattern, blob)
            if matched:
                declaration_offenders.append((action["id"], pattern, matched.group(0)))
    # 说明：按键的 `param.help` 只渲染在 `commands.catalog`（owner 裁决豁免的技术视图）里，
    # 所以不在这里单独扫——面板级那条判据已经按裁决跳过了那一页。
    assert declaration_offenders == [], (
        f"动作/按键的声明里出现了命令/开关：{declaration_offenders}"
    )

    # 动作标题是「做什么」而不是命令名。
    actions = call_route(registry, "GET", "/api/v1/actions")["data"]["actions"]
    titles = {action["id"]: action["title"] for action in actions}
    assert "更新这家公司的分析" in titles.values()
    assert all(not re.search(r"[a-z_]+\.[a-z_]+", action["title"]) for action in actions)


def test_action_requires_company_is_reported_as_a_blocker_not_an_error(tmp_path):
    """AC-2.2：缺上下文是**禁用 + 理由**（预检），不是提交之后才报错。"""
    from webui.plugins import actions as actions_plugin

    _, registry = make_app(tmp_path)
    actions_plugin.contribute(registry)
    payload = call_route(registry, "GET", "/api/v1/actions")["data"]
    entry = next(item for item in payload["actions"] if item["id"] == "company.update_analysis")
    assert entry["enabled"] is False
    assert any("还没选公司" in blocker for blocker in entry["blockers"])
    assert entry["requires"] == ["selection.company"]


def test_server_rejects_an_action_that_is_not_enabled(tmp_path):
    """AC-2.2 的边界：前端的禁用只是展示，服务端**再判一次**且不起进程。"""
    from webui.plugins import actions as actions_plugin

    _, registry = make_app(tmp_path)
    actions_plugin.contribute(registry)
    calls = []

    def exploding_popen(*args, **kwargs):  # pragma: no cover
        calls.append(args)
        raise AssertionError("预检不通过不该启动子进程")

    registry.jobs = JobRunner(
        registry.config, spec_lookup=registry.command_spec,
        job_lookup=registry.job_type_spec, history_dir=tmp_path / "jobs4", env={},
        popen=exploding_popen,
    )
    with pytest.raises(BadRequest):
        call_route(registry, "POST", "/api/v1/actions/company.update_analysis/run",
                   body={"context": {}})
    assert calls == []


# --------------------------------------------------------------- AC-2.2 预检与确认


def test_preflight_blockers_are_user_language_and_come_from_the_server(tmp_path):
    """AC-2.2：预检原因由服务端算（前端不复算），且是人话。"""
    _, registry = make_app(tmp_path)

    def preflight(ctx, selection):
        return {"blockers": ["上游产物缺失：先跑一次取数。"]}

    register_demo_action(registry, preflight=preflight, requires=())
    entry = call_route(registry, "GET", "/api/v1/actions")["data"]["actions"][0]
    assert entry["enabled"] is False
    assert entry["blockers"] == ["上游产物缺失：先跑一次取数。"]
    assert "company_dir" not in json.dumps(entry, ensure_ascii=False)


def test_preflight_exception_becomes_a_blocker_not_a_500(tmp_path):
    """AC-2.2：预检自己炸了也不能把页面打成 500——降级成「这个动作暂时不可用 + 原因」。"""
    from webui.core.errors import BadRequest as BadReq

    _, registry = make_app(tmp_path)

    def preflight(ctx, selection):
        raise BadReq("仓不可用", hint="检查配置。")

    register_demo_action(registry, preflight=preflight)
    entry = call_route(registry, "GET", "/api/v1/actions")["data"]["actions"][0]
    assert entry["enabled"] is False
    assert any("仓不可用" in blocker for blocker in entry["blockers"])


def test_dangerous_action_carries_server_side_confirmation_copy(tmp_path):
    """AC-2.2 / AC-4：危险动作的确认文案由服务端给（前端只弹层），并说明会发生什么。"""
    _, registry = make_app(tmp_path)
    register_demo_action(
        registry, danger=True,
        effects={"network": True, "quota": True, "writes": ("原始仓记录",)},
    )
    entry = call_route(registry, "GET", "/api/v1/actions")["data"]["actions"][0]
    assert entry["danger"] is True
    assert entry["confirm"]["title"] and entry["confirm"]["confirm_label"]
    assert entry["effects"]["network"] is True and entry["effects"]["quota"] is True
    assert entry["effects"]["writes"] == ["原始仓记录"]


def test_offline_and_online_actions_are_distinguishable(tmp_path):
    """AC-3.3 / AC-4.4：「刷新/重建（离线，不花钱）」与「拉取（联网，花钱）」语义不同。"""
    from webui.plugins import actions as actions_plugin

    _, registry = make_app(tmp_path)
    actions_plugin.contribute(registry)
    actions = {item["id"]: item for item in call_route(registry, "GET", "/api/v1/actions")["data"]["actions"]}

    pull = actions["data.pull_all"]
    assert pull["effects"]["network"] is True and pull["effects"]["quota"] is True
    assert pull["danger"] is True and pull["confirm"]["title"]

    rebuild = actions["data.rebuild"]
    assert rebuild["effects"]["network"] is False and rebuild["effects"]["quota"] is False
    assert rebuild["danger"] is False and rebuild["confirm"] == {}

    fill = actions["data.fill_gaps"]
    assert fill["effects"]["network"] is True
    assert fill["title"] != pull["title"], "「拉取全部」与「只补缺口」必须是两个可分辨的动作"


# --------------------------------------------------------------- AC-2.3 任务中心


def test_multi_step_action_records_progress_and_stops_at_the_first_failure(tmp_path):
    """AC-2.3 / AC-2.4：多步在一个任务里顺序执行；某步失败**不继续**后续步。"""
    _, registry = make_app(tmp_path)
    marker = (tmp_path / "should-not-exist.txt").resolve()
    with_write_env(registry, tmp_path, marker)
    register_demo_action(registry, steps=(
        CommandStep("step_echo"),
        CommandStep("step_fail"),
        CommandStep("step_write", bind={"out": str(marker)}),
    ))
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run",
                     body={"context": {}})["data"]
    final = wait_job(registry, job["id"])
    assert final["status"] == "failed"
    assert final["progress"]["total"] == 3
    assert final["progress"]["completed"] == 2
    statuses = [step["status"] for step in final["steps"]]
    assert statuses == ["done", "failed", "pending"]
    assert not marker.exists(), "失败的步骤后面的步骤不许执行"
    assert "第 2 步" in final["error"]


def test_failed_job_keeps_both_a_readable_reason_and_the_raw_log(tmp_path):
    """AC-2.3（父 AC-5）：失败展示里**同时**有可读原因与可展开的原始日志，两者都不丢。"""
    _, registry = make_app(tmp_path)
    register_demo_action(registry, steps=(CommandStep("step_nope"),))
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run",
                     body={"context": {}})["data"]
    final = wait_job(registry, job["id"])
    summary = final["failure_summary"]
    assert summary["what"] and summary["how"], "失败必须给「发生了什么 + 怎么办」"
    assert "怎么办" or summary["how"]
    raw = "\n".join(final["log"]) + "\n".join(
        line for step in final["steps"] for line in step["log"]
    )
    assert "boom" in raw, "原始日志一个字都不能删"

    # REQ-015 T11: mentioning token configuration earlier must not obscure
    # the terminal model-service failure or modify the original evidence.
    quota_log = ["TUSHARE_TOKEN is loaded from environment", "You've hit your usage limit. Try again at 9:16PM"]
    failed = Job("quota", "demo", "额度", [], {}, status=FAILED, exit_code=1)
    failed.log.extend(quota_log)
    reason = registry.jobs._summarise(failed)
    assert "额度已用尽" in reason["what"] and "手动" in reason["how"]
    assert list(failed.log) == quota_log
    failed.log.clear()
    failed.log.append("TUSHARE_TOKEN is loaded from environment")
    assert "数据源凭据" not in registry.jobs._summarise(failed)["what"]
    failed.log.clear()
    failed.log.append("NO_TOKEN: 未配置 Tushare token")
    assert "数据源凭据" in registry.jobs._summarise(failed)["what"]


def test_retry_reexecutes_the_action_with_the_same_context(tmp_path):
    """AC-2.3：失败可重试；重试是**再提交一次**（历史记录不被改写）。"""
    _, registry = make_app(tmp_path)
    company_dir = tmp_path / "output" / "600887_伊利"
    with_write_env(registry, tmp_path, company_dir / "out.txt")
    register_demo_action(registry, steps=(
        CommandStep("step_write", bind={"out": "{company_dir}/out.txt"}),
    ))
    company_dir.mkdir(parents=True, exist_ok=True)
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run",
                     body={"context": {"company": "600887_伊利",
                                       "company_dir": str(company_dir)}})["data"]
    first = wait_job(registry, job["id"])
    assert first["status"] == FINISHED
    retried = call_route(registry, "POST", f"/api/v1/jobs/{job['id']}/retry")["data"]
    assert retried["id"] != job["id"]
    second = wait_job(registry, retried["id"])
    assert second["status"] == FINISHED
    assert second["argv"] == first["argv"], "重试必须用同一份已解析参数"
    # 历史记录保留两条，原记录状态不被改写。
    assert registry.jobs.get(job["id"])["status"] == FINISHED


def test_action_outputs_are_listed_with_existence(tmp_path):
    """AC-2.3：任务产出链接到对应产物（声明 → 实际路径 + 是否已生成）。"""
    _, registry = make_app(tmp_path)
    company_dir = tmp_path / "output" / "600887_伊利"
    company_dir.mkdir(parents=True, exist_ok=True)
    target = company_dir / "data_pack_market.md"
    target.write_text("# pack", encoding="utf-8")
    register_demo_action(registry, steps=(
        CommandStep("step_echo", writes=("{company_dir}/data_pack_market.md",
                                         "{company_dir}/never_written.md")),
    ))
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run",
                     body={"context": {"company": "600887_伊利",
                                       "company_dir": str(company_dir)}})["data"]
    final = wait_job(registry, job["id"])
    writes = {item["path"]: item["exists"] for item in final["outputs"]["writes"]}
    assert writes[str(target)] is True
    assert writes[str(company_dir / "never_written.md")] is False


def test_queue_holds_jobs_beyond_the_concurrency_limit_and_lists_them(tmp_path):
    """AC-2.3：并发满了**排队**（不再直接 429），任务中心能列出队列；队列满才拒绝。"""
    config, registry = make_app(tmp_path, max_jobs=1, max_queued=1)
    sleep_cmd = CommandSpec(
        id="step_sleep", argv=(sys.executable, "-c", "import time; time.sleep(5)"), title="长睡",
    )
    registry.command(sleep_cmd)
    register_demo_action(registry, steps=(CommandStep("step_sleep"),))

    first = call_route(registry, "POST", "/api/v1/actions/demo.chain/run", body={"context": {}})["data"]
    second = call_route(registry, "POST", "/api/v1/actions/demo.chain/run", body={"context": {}})["data"]
    assert first["status"] == "running"
    assert second["status"] == "queued"
    listing = call_route(registry, "GET", "/api/v1/jobs")["data"]
    assert [item["id"] for item in listing["queue"]] == [second["id"]]

    with pytest.raises(TooManyJobs):
        call_route(registry, "POST", "/api/v1/actions/demo.chain/run", body={"context": {}})

    # 取消排队中的任务：从队列里消失，且不产生任何子进程结果。
    cancelled = call_route(registry, "POST", f"/api/v1/jobs/{second['id']}/cancel")["data"]
    assert cancelled["status"] == "cancelled"
    assert call_route(registry, "GET", "/api/v1/jobs")["data"]["queue"] == []
    registry.jobs.cancel(first["id"])


def test_cancelling_a_running_action_marks_it_cancelled(tmp_path):
    """AC-2.3：进行中的任务可取消（终止子进程），已完成步骤的产物保留。"""
    _, registry = make_app(tmp_path)
    marker = (tmp_path / "output" / "600887_伊利" / "done.txt").resolve()
    with_write_env(registry, tmp_path, marker)
    sleep_cmd = CommandSpec(
        id="step_sleep", argv=(sys.executable, "-c", "import time; time.sleep(5)"), title="长睡",
    )
    registry.command(sleep_cmd)
    register_demo_action(registry, steps=(
        CommandStep("step_write", bind={"out": str(marker)}),
        CommandStep("step_sleep"),
    ))
    marker.parent.mkdir(parents=True, exist_ok=True)
    assert marker.is_absolute()
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run", body={"context": {}})["data"]
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.03)
    call_route(registry, "POST", f"/api/v1/jobs/{job['id']}/cancel")
    final = wait_job(registry, job["id"], timeout=10)
    assert final["status"] == "cancelled"
    assert marker.exists(), "已完成的步骤产物保留"


def test_job_payload_keeps_secrets_out_of_logs_and_history(tmp_path):
    """AC-2.3 的延续（REQ-009.1 的 AC-1.4）：动作任务的日志与历史同样脱敏。"""
    token = "abcdef0123456789abcdef0123456789"
    _, registry = make_app(tmp_path, env={"TUSHARE_TOKEN": token})
    registry.jobs = JobRunner(
        registry.config, spec_lookup=registry.command_spec,
        job_lookup=registry.job_type_spec, history_dir=tmp_path / "jobs", env={"TUSHARE_TOKEN": token},
    )
    registry.command(CommandSpec(
        id="step_leak", argv=(sys.executable, "-c", f"print('{token}')"), title="打印凭据",
    ))
    register_demo_action(registry, steps=(CommandStep("step_leak"),))
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run", body={"context": {}})["data"]
    final = wait_job(registry, job["id"])
    dumped = json.dumps(final, ensure_ascii=False)
    assert token not in dumped and "***" in dumped
    for path in (tmp_path / "jobs").glob("*.json"):
        assert token not in path.read_text(encoding="utf-8")


# --------------------------------------------------------------- AC-2.4 人机交接


def _handoff_action(registry, expects, *, expect_kind: str = "file"):
    return register_demo_action(registry, steps=(
        HumanStep(title="在 agent CLI 里跑模块分析", slash="/update-analysis 600887.SH",
                  hint="跑完回来点继续。", expects=expects, expect_kind=expect_kind),
        CommandStep("step_echo"),
    ))


def test_human_step_parks_the_job_without_taking_a_concurrency_slot(tmp_path):
    """AC-2.4：交接步不占并发槽（`awaiting_agent` 可以同时来很多个）。"""
    _, registry = make_app(tmp_path, max_jobs=1)
    expects = (str(tmp_path / "output" / "600887_伊利" / "latest.json"),)
    _handoff_action(registry, expects)
    first = call_route(registry, "POST", "/api/v1/actions/demo.chain/run",
                       body={"context": {}})["data"]
    wait_status(registry, first["id"], AWAITING)
    second = call_route(registry, "POST", "/api/v1/actions/demo.chain/run",
                        body={"context": {}})["data"]
    # 第二个任务必须**立刻开始**（running / awaiting_agent 都算开始，都不是 queued）：
    # 这就是「交接不占并发槽」的判据。`POST` 的返回状态本身是竞态——交接步可能在响应
    # 组装完之前就进 `awaiting_agent`，所以判据写成「它已经开始」而不是「此刻恰好 running」。
    started = wait_status(registry, second["id"], ("running", AWAITING), timeout=5)
    assert started["status"] != "queued", "交接中的任务不该占住并发名额"
    assert registry.jobs.queue() == []
    registry.jobs.abandon_action(first["id"])
    registry.jobs.abandon_action(second["id"])


def test_continue_without_the_declared_artifact_stays_on_the_handoff_step(tmp_path):
    """AC-2.4：产物不存在就点「继续」→ **停在原地**并说明缺什么（防「没跑就点」）。"""
    _, registry = make_app(tmp_path)
    expects = (str(tmp_path / "output" / "600887_伊利" / "latest.json"),)
    _handoff_action(registry, expects)
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run", body={"context": {}})["data"]
    wait_status(registry, job["id"], AWAITING)
    call_route(registry, "POST", f"/api/v1/jobs/{job['id']}/continue")
    time.sleep(0.3)
    parked = registry.jobs.get(job["id"])
    assert parked["status"] == AWAITING, "产物没出现就不许继续"
    assert parked["handoff"]["missing"] == list(expects)
    assert "还没就绪" in json.dumps(parked, ensure_ascii=False)
    registry.jobs.abandon_action(job["id"])


def test_continue_with_a_stale_artifact_does_not_unlock_the_step(tmp_path):
    """AC-2.4：产物**存在但比本步开始还旧**也不算跑完（mtime 判据的必要性）。"""
    _, registry = make_app(tmp_path)
    target = tmp_path / "output" / "600887_伊利" / "latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    import os

    old = time.time() - 3600
    os.utime(target, (old, old))
    _handoff_action(registry, (str(target),))
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run", body={"context": {}})["data"]
    wait_status(registry, job["id"], AWAITING)
    call_route(registry, "POST", f"/api/v1/jobs/{job['id']}/continue")
    time.sleep(0.3)
    assert registry.jobs.get(job["id"])["status"] == AWAITING
    registry.jobs.abandon_action(job["id"])


def test_continue_with_a_fresh_artifact_resumes_the_remaining_steps(tmp_path):
    """AC-2.4：产物存在且是这一步跑出来的 → 继续后续确定性步骤直到完成。"""
    _, registry = make_app(tmp_path)
    target = tmp_path / "output" / "600887_伊利" / "latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    _handoff_action(registry, (str(target),))
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run", body={"context": {}})["data"]
    wait_status(registry, job["id"], AWAITING)
    handoff = registry.jobs.get(job["id"])["handoff"]
    assert handoff["awaiting"] is True
    assert handoff["slash"] == "/update-analysis 600887.SH"
    assert handoff["expects"] == [str(target)]

    target.write_text("{}", encoding="utf-8")     # 模拟「跑完了」
    call_route(registry, "POST", f"/api/v1/jobs/{job['id']}/continue")
    final = wait_job(registry, job["id"], timeout=10)
    assert final["status"] == FINISHED
    assert [step["status"] for step in final["steps"]] == ["done", "done"]
    assert final["progress"]["completed"] == 2


def test_abandoning_a_handoff_cancels_the_job_and_keeps_earlier_products(tmp_path):
    """AC-2.4：「放弃这次」→ 任务收口为取消，前面步骤的产物保留。"""
    _, registry = make_app(tmp_path)
    done = (tmp_path / "output" / "600887_伊利" / "step1.txt").resolve()
    with_write_env(registry, tmp_path, done)
    register_demo_action(registry, steps=(
        CommandStep("step_write", bind={"out": str(done)}),
        HumanStep(title="跑 agent", expects=(str(tmp_path / "nope.json"),)),
        CommandStep("step_echo"),
    ))
    done.parent.mkdir(parents=True, exist_ok=True)
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run", body={"context": {}})["data"]
    wait_status(registry, job["id"], AWAITING)
    call_route(registry, "POST", f"/api/v1/jobs/{job['id']}/abandon")
    final = wait_job(registry, job["id"], timeout=10)
    assert final["status"] == "cancelled"
    assert done.exists()


# --------------------------------------------------------------- AC-11 扩展性


def test_a_demo_action_needs_only_registration_and_does_not_touch_the_core(tmp_path):
    """AC-11（父 AC-9）：注册一个动作只调 `registry.job_type`，核心一行不用改。"""
    _, registry = make_app(tmp_path)
    assert "demo.chain" not in registry.job_types()
    spec = register_demo_action(registry)
    # 富声明只贡献 `kind` 到 job_types（既有契约「类型 id 的元组」继续成立）。
    assert "chain" in registry.job_types()
    assert registry.job_type_spec("demo.chain") is spec
    assert registry.has_job_type_spec("demo.chain")

    # 旧的裸形态一字不变。
    registry.job_type("batch", lambda *args, **kwargs: None)
    assert "batch" in registry.job_types()

    # 诊断快照要能看到「谁注册了什么动作」。
    snapshot = call_route(registry, "GET", "/api/v1/registry")["data"]
    assert "chain" in snapshot["job_types"]
    assert "job_type:demo.chain" in snapshot["origins"]


def test_duplicate_action_id_is_a_registration_conflict(tmp_path):
    """AC-11：同 id 重复注册必须报错并指明来源（静默覆盖会让功能凭空消失）。"""
    from webui.core.errors import RegistrationConflict

    _, registry = make_app(tmp_path)
    register_demo_action(registry)
    with pytest.raises(RegistrationConflict):
        register_demo_action(registry)


def test_actions_panel_is_a_registered_kind_not_a_core_feature(tmp_path):
    """AC-11：`kind=actions` 是插件注册的面板 + 前端可注册的渲染器，不是内核特判。"""
    _, registry = make_app(tmp_path)
    spec = registry.panel_spec("commands.actions")
    assert spec.kind == "actions"
    assert spec.render == "client"
    assert spec.endpoint == "/api/v1/actions"
    from webui.render import panels

    assert "actions" in panels.CLIENT_KINDS
    source = (Path(__file__).resolve().parents[1] / "scripts" / "webui" / "static"
              / "app.js").read_text(encoding="utf-8")
    assert 'registerPanelKind("actions"' in source
    assert 'registerPanelKind("table"' in source


def test_action_submission_resolves_the_context_server_side(tmp_path):
    """AC-2.1 / AC-12 回归：前端只传「哪家公司」，服务端把路径与标的解析出来。

    实测缺陷：`POST /api/v1/actions/{id}/run` 把前端的上下文**原样**交给动作，
    于是编排里的 `{ticker}` / `{company_dir}` 解析不出来，动作在起进程前就 422
    （界面上表现为「点了没反应」）。
    """
    from webui.plugins import actions as actions_plugin
    from webui.plugins import companies as companies_plugin

    config, registry = make_app(tmp_path)
    directory = tmp_path / "output" / "600887_伊利"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "data_pack_market.md").write_text("# pack", encoding="utf-8")
    (directory / "record.json").write_text(
        json.dumps({"subject": {"ticker": "600887.SH", "company": "伊利股份"}},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    actions_plugin.contribute(registry)
    companies_plugin.contribute(registry)

    # 重建动作的预检要求原始仓里有这家公司的记录（这是**正确**的前置）：造一条。
    from datalayer.store import DataStore as LayerStore
    import pandas as pd

    store = LayerStore(config.archive_root)
    store.write_frame(ticker="600887.SH", dataset="daily", period="20260101",
                      params={"ts_code": "600887.SH"},
                      frame=pd.DataFrame({"ts_code": ["600887.SH"], "value": [1.0]}),
                      result="ok")

    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        raise AssertionError("只验证参数解析，不真的跑")

    registry.jobs = JobRunner(
        config, spec_lookup=registry.command_spec, job_lookup=registry.job_type_spec,
        history_dir=tmp_path / "jobs", env={"TUSHARE_TOKEN": "test-token-value-0123456789"},
        popen=fake_popen,
    )
    # 前端只传公司标识（ticker 或目录名），**不传** 路径、期次、run —— 那些由服务端解析。
    job = call_route(registry, "POST", "/api/v1/actions/data.rebuild/run",
                     body={"context": {"company": "600887.SH"}})["data"]
    assert job["status"] in ("pending", "running", "failed")
    argv = captured.get("argv") or job["argv"]
    assert "600887.SH" in argv, f"ticker 必须由服务端解析进 argv：{argv}"
    assert any(str(part).endswith("data_pack_market.md") or part == "output" for part in argv)
    # 解析结果也随任务留存（重试与「产物在哪」都要用）。
    assert job["outputs"]["context"]["ticker"] == "600887.SH"
    assert job["outputs"]["context"]["company_dir"].endswith("600887_伊利")


def test_handoff_is_operable_through_the_http_routes(tmp_path):
    """AC-2.3 / AC-2.4：交接与重试在**真实路由**上可用（不只是 JobRunner 的方法）。

    判据是「界面能做的两件事都能通过接口做完」：`/continue` 校验产物后继续、
    `/abandon` 收口、`/retry` 重新提交。少了任何一条，前端就得走别的通道。
    """
    _, registry = make_app(tmp_path)
    target = tmp_path / "output" / "600887_伊利" / "latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    _handoff_action(registry, (str(target),))
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run",
                     body={"context": {}})["data"]
    wait_status(registry, job["id"], AWAITING)

    parked = call_route(registry, "GET", f"/api/v1/jobs/{job['id']}")["data"]
    assert parked["handoff"]["awaiting"] is True
    assert parked["handoff"]["slash"].startswith("/update-analysis")

    # 产物没出现：继续也不会走。
    call_route(registry, "POST", f"/api/v1/jobs/{job['id']}/continue")
    time.sleep(0.3)
    assert registry.jobs.get(job["id"])["status"] == AWAITING
    assert registry.jobs.get(job["id"])["handoff"]["missing"] == [str(target)]

    # 产物出现且是这一步产出的：继续到底。
    target.write_text("{}", encoding="utf-8")
    call_route(registry, "POST", f"/api/v1/jobs/{job['id']}/continue")
    final = wait_job(registry, job["id"], timeout=10)
    assert final["status"] == FINISHED

    # 已完成的任务不能再「继续」（状态机不许含糊）。
    with pytest.raises(InvalidParam):
        call_route(registry, "POST", f"/api/v1/jobs/{job['id']}/continue")

    # 重试走真实路由，且用的是同一份已解析参数。
    retried = call_route(registry, "POST", f"/api/v1/jobs/{job['id']}/retry")["data"]
    assert retried["id"] != job["id"]
    assert retried["argv"] == final["argv"]
    retried_status = wait_status(registry, retried["id"], AWAITING, timeout=10)
    assert retried_status["status"] == AWAITING
    call_route(registry, "POST", f"/api/v1/jobs/{retried['id']}/abandon")
    abandoned = wait_job(registry, retried["id"], timeout=10)
    assert abandoned["status"] == "cancelled"


def test_step_outputs_and_enums_are_resolved_when_the_step_runs_not_at_submission(tmp_path):
    """AC-2.1 / AC-2.4：上一步的输出与枚举默认值**在执行到那一步时**才解析。

    两条都会被「提交时全量解析」的实现搞坏（实测都踩过）：

    - `{run_dir}` 由上一步（`runs resolve`）的 stdout 捕获，提交时当然还没有值；
    - `{enum:complete|partial}` 是枚举占位符，必须由参数声明给出候选并取第一个。
    """
    import json as _json

    from webui.plugins import actions as actions_plugin
    from webui.plugins import companies as companies_plugin

    config, registry = make_app(tmp_path)
    directory = tmp_path / "output" / "600887_伊利"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "data_pack_market.md").write_text("# pack", encoding="utf-8")
    (directory / "record.json").write_text(
        _json.dumps({"subject": {"ticker": "600887.SH", "company": "伊利股份"}},
                    ensure_ascii=False),
        encoding="utf-8",
    )
    actions_plugin.contribute(registry)
    companies_plugin.contribute(registry)

    # 期次一起给（真实链路由 `datalayer.rebuild.data_as_of` 提供；这里直接给上下文）。
    context = {"company": "600887.SH", "primary_period": "2025FY"}
    job = call_route(registry, "POST", "/api/v1/actions/company.update_analysis/run",
                     body={"context": dict(context)})["data"]
    assert job["status"] in ("pending", "running", "awaiting_agent")
    steps = job["steps"]
    assert [step["kind"] for step in steps] == ["human", "command", "command", "command"]

    # 第 3 步（runs finish）的两个「上一步/枚举」参数：提交时不许报缺值。
    finish = steps[2]
    assert finish["command"] == "runs_finish"
    argv_text = " ".join(finish["argv"])
    # 期次由上下文给：有值就带上，没有就**不发这个可选参数**（不发空值比发空串干净）。
    assert ("--primary-period" in argv_text) == bool(context["primary_period"])
    assert "complete" in argv_text, "枚举占位符取声明的第一个候选"
    # 唯一允许留下的占位符是上一步捕获的 `{run_dir}`（它确实还没跑出来）。
    leftover = set(re.findall(r"\{[^{}]*\}", argv_text))
    assert leftover <= {"{run_dir}"}, f"提交后不该再留别的占位符：{leftover}（{argv_text}）"
    # 第 2 步的 run 目录还是占位符（要等它自己跑出来）——这是**延迟解析**的证据。
    resolve_step = steps[1]
    assert any("{run_dir}" in part or part.endswith("--company-dir")
               for part in resolve_step["argv"])

    # 走到交接点：产物断言已经解析成**绝对路径**（用户要照着它去 agent CLI 里跑）。
    parked = wait_status(registry, job["id"], AWAITING)
    assert all(expect.startswith(str(tmp_path)) for expect in parked["handoff"]["expects"])
    assert parked["handoff"]["slash"] == "/update-analysis 600887.SH"
    assert parked["handoff"]["paths"]["company_dir"] == str(directory)
    registry.jobs.abandon_action(job["id"])


def test_job_history_round_trips_through_a_restart(tmp_path):
    """AC-2.3 / REQ-009.1 的 AC-1.4：任务历史落盘后**读得回来**，列表接口不许 500。

    实测缺陷：`outputs` 里的产物在内存里是 `{声明模板: 路径}`、落盘后是
    `[{declared, path, exists}]`，公开副本只认第一种形态，于是服务重启后
    `/api/v1/jobs` 直接 AttributeError→500（界面上是「任务列表打不开」）。
    """
    config, registry = make_app(tmp_path)
    company_dir = tmp_path / "output" / "600887_伊利"
    company_dir.mkdir(parents=True, exist_ok=True)
    target = company_dir / "data_pack_market.md"
    target.write_text("# pack", encoding="utf-8")
    register_demo_action(registry, steps=(
        CommandStep("step_echo", writes=("{company_dir}/data_pack_market.md",)),
    ))
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run",
                     body={"context": {"company": "600887.SH",
                                       "company_dir": str(company_dir)}})["data"]
    final = wait_job(registry, job["id"])
    assert final["status"] == FINISHED
    assert final["outputs"]["writes"][0]["exists"] is True
    # **等到历史里出现终态**再重启 runner（见 `wait_history_finished` 的说明：
    # 「文件存在」是不够的，它在 `submit()` 阶段就存在了）。判据本身不放宽
    # （仍然断言重启后历史读得回来），只是不再依赖写入时序。
    wait_history_finished(registry, job["id"])

    # 模拟「面板重启」：用同一个历史目录新建一个 runner，再读列表。
    restarted = JobRunner(
        config, spec_lookup=registry.command_spec, job_lookup=registry.job_type_spec,
        history_dir=tmp_path / "jobs", env={},
    )
    registry.jobs = restarted
    listing = call_route(registry, "GET", "/api/v1/jobs")["data"]
    assert listing["count"] >= 1
    reloaded = next(item for item in listing["jobs"] if item["id"] == job["id"])
    assert reloaded["status"] == FINISHED
    assert reloaded["outputs"]["writes"][0]["path"] == str(target)
    assert reloaded["outputs"]["context"]["company_dir"] == str(company_dir)


def test_continue_reports_the_missing_artifacts_in_its_response(tmp_path):
    """AC-2.4：点「继续」而产物不合格时，**响应本身**就要说清缺什么。

    门② 第六轮抓到的界面阻断项有一部分根因在这里：`continue` 返回的是**校验前**的快照
    （`handoff.missing` 恒为空数组），客户端因此没法立刻把「缺什么」显示出来，
    只能再取一次——而界面当时根本没再取。现在响应就是校验后的状态。
    """
    _, registry = make_app(tmp_path)
    target = tmp_path / "output" / "600887_伊利" / "latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    _handoff_action(registry, (str(target),))
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run", body={"context": {}})["data"]
    wait_status(registry, job["id"], AWAITING)

    # 产物不存在：继续的响应必须**当场**带出 missing，而不是空数组。
    parked = call_route(registry, "POST", f"/api/v1/jobs/{job['id']}/continue")["data"]
    assert parked["status"] == AWAITING, "产物不合格不许放行"
    assert parked["handoff"]["missing"] == [str(target)], (
        f"continue 的响应要带校验结果，实际 {parked['handoff'].get('missing')!r}"
    )
    assert "还没就绪" in json.dumps(parked, ensure_ascii=False)

    # 产物就绪：同一个接口把任务放行（响应里状态离开 awaiting）。
    target.write_text("{}", encoding="utf-8")
    resumed = call_route(registry, "POST", f"/api/v1/jobs/{job['id']}/continue")["data"]
    assert resumed["status"] in (FINISHED, "running"), (
        f"产物就绪后应当放行：{resumed['status']}"
    )


def test_interrupted_history_gets_a_rebuilt_failure_summary(tmp_path):
    """AC-5 / AC-2.3：服务重启把在跑的任务标成「已中断」后，摘要要**重建**且措辞正确。

    门② 第八轮登记的 F5：重启读回的历史里只有 `error`，`failure_summary` 是空的，
    卡片上的「发生了什么 + 怎么办」靠前端兜底；而直接调 `_summarise()` 的兜底文案在
    `exit_code is None` 时会说「命令没能启动」——对**启动过**的中断任务是误导。
    所以：先给 `_FAILURE_RULES` 补「已中断」规则，再在 `_load_history` 里重建摘要。
    """
    config, registry = make_app(tmp_path)
    history = Path(registry.jobs.history_dir)
    history.mkdir(parents=True, exist_ok=True)
    (history / "job-interrupted.json").write_text(
        json.dumps({"id": "job-interrupted", "command": "step_echo", "title": "演示",
                    "argv": [], "status": "running", "steps": [], "log": [],
                    "started_at": "2026-10-09T00:00:00+00:00",
                    "error": "服务重启时该任务仍在运行（已中断）"}, ensure_ascii=False),
        encoding="utf-8",
    )

    restarted = JobRunner(config, spec_lookup=registry.command_spec,
                          job_lookup=registry.job_type_spec, history_dir=history, env={})
    job = restarted.get("job-interrupted")
    assert job["status"] == FAILED
    summary = job["failure_summary"]
    assert summary, "重启读回时摘要必须被重建（F5）"
    assert "被中断" in summary["what"], summary
    assert "命令没能启动" not in summary["what"], "中断过的任务不该说「没能启动」"
    assert summary["how"], "「怎么办」也要有"


def test_history_writes_are_safe_under_concurrent_writers(tmp_path):
    """AC-2.3：同一个任务被**并发写**历史时，磁盘上不能出现半截/拼接的历史。

    第二份独立 delta 复核（`…-reverify9.md` 的 R2）发现：临时名原先只带 pid
    （`path.with_suffix(f".{os.getpid()}.tmp")`），而 `cancel()` 的 QUEUED 分支与
    `_release_next()` 都在**锁外**各写一次同一个任务——两个写入者抢同一个临时文件，
    `os.replace` 于是可能发布「两份快照拼接」出来的非法 JSON（它慢盘下 10/10 复现；
    本仓库的探针按同样方法测到旧写法 6/12 损坏、改成每次唯一后 0/12）。
    修法：`tempfile.mkstemp()` 保证每次写入的临时名唯一。
    """
    config, registry = make_app(tmp_path)
    register_demo_action(registry, steps=(CommandStep("step_echo"),))
    job = call_route(registry, "POST", "/api/v1/actions/demo.chain/run",
                     body={"context": {}})["data"]
    runner = registry.jobs
    real = runner._jobs[job["id"]]

    # 12 个线程并发写同一个任务的历史。
    import threading

    threads = [threading.Thread(target=runner._write_history, args=(real,)) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    history = Path(runner.history_dir)
    payload = json.loads((history / f"{real.id}.json").read_text(encoding="utf-8"))
    assert payload["id"] == real.id, "并发写之后历史必须仍然可解析"
    assert payload["status"] == real.status
    leftovers = list(history.glob("*.tmp"))
    assert leftovers == [], f"并发写不该留下临时文件：{leftovers}"

    # 临时名必须**每次唯一**（这正是 R2 的根因）：直接看实现用它而不是拼 pid。
    source = (Path(__file__).resolve().parents[1] / "scripts" / "webui" / "core"
              / "jobs.py").read_text(encoding="utf-8")
    assert "tempfile.mkstemp(" in source, "临时名要用 mkstemp 保证唯一"
    assert 'with_suffix(f".{os.getpid()}.tmp")' not in source, "不许再用「同进程内相同」的临时名"
