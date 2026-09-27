# 覆盖需求：REQ-009.1（按键执行器与任务生命周期）—— AC-1.1 任务生命周期、
# AC-1.2 按键清单与参数双向一致、AC-1.3 拒绝与并发上限、AC-1.4 任务历史与脱敏
"""按键执行器测试。

三条刻意的手法：

1. **不跑真实脚本**：假命令用 `sys.executable -c`（打印/退出码/长睡），不联网、不写仓库；
2. **不起子进程也能验拒绝路径**：给 `JobRunner` 注入一个「一被调用就失败」的 `popen`，
   拒绝路径若偷偷起了进程，测试立刻炸（AC-1.3 的判据不是「返回 4xx」而是「没起进程」）；
3. **参数表双向核对**：测试自己用 AST 扫脚本源码，不调用插件里的扫描器（否则是自证）。
"""

import ast
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from webui.config import Config
from webui.core.context import RequestContext
from webui.core.errors import (
    InvalidParam,
    ShellMetachar,
    TooManyJobs,
    UnknownCommand,
    WebUIError,
)
from webui.core.jobs import JobRunner
from webui.core.models import CommandSpec, Param
from webui.core.registry import build_registry
from webui.core.router import find_route
from webui.core.routes import install_core_routes
from webui.core.server import WebUIServer
from webui.datastore import DataStore, parsers
from webui.plugins import commands as commands_plugin

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# AC-2（父需求）点名的入口清单：少一个都算按键覆盖不全。
EXPECTED_COMMANDS = {
    "tushare_collector", "discover_report", "download_report", "pdf_preprocessor",
    "value_analysis_engine", "buy_sell_plan", "buy_sell_engine", "valuation_engine",
    "portfolio_engine", "screener_core", "report_to_html", "md_to_mobile_html",
    "analysis_status", "runs_new", "runs_resolve", "runs_finish", "runs_adopt",
    "runs_export", "runs_downstream", "results_prepare", "results_reconcile",
    "results_synthesis", "results_resolve",
}
RUNS_PARSER_VARS = {
    "runs_new": "new_parser",
    "runs_resolve": "resolve_parser",
    "runs_finish": "finish_parser",
    "runs_adopt": "adopt_parser",
    "runs_export": "export_parser",
    "runs_downstream": "downstream_parser",
}

FAKE_COMMANDS = (
    CommandSpec(id="test_echo", argv=(sys.executable, "-c", "print('hello-from-job')"),
                title="回显"),
    CommandSpec(id="test_fail",
                argv=(sys.executable, "-c",
                      "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"),
                title="失败"),
    CommandSpec(id="test_sleep", argv=(sys.executable, "-c", "import time; time.sleep(30)"),
                title="长睡"),
    CommandSpec(id="test_need", argv=(sys.executable, "-c", "print('ok')"), title="必填参数",
                params=(Param("must", required=True),)),
)


# --------------------------------------------------------------- 测试辅助


def make_config(tmp_path: Path, **overrides) -> Config:
    base = {
        "host": "127.0.0.1",
        "port": 0,
        "output_root": tmp_path / "output",
        "cache_dir": tmp_path / "output",
        "archive_root": tmp_path / "archive",
    }
    base.update(overrides)
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    return Config(**base)


def web_app(tmp_path: Path, *, max_jobs=4, history_dir=None, env=None):
    config = make_config(tmp_path, max_concurrent_jobs=max_jobs)
    registry = build_registry()
    install_core_routes(registry, config)
    registry.config = config
    commands_plugin.contribute(registry)
    for spec in FAKE_COMMANDS:
        registry.command(spec)
    registry.datastore = DataStore(config, spec_lookup=registry.dataset_spec)
    registry.jobs = JobRunner(
        config, spec_lookup=registry.command_spec,
        history_dir=history_dir or (tmp_path / "jobs"), env=env if env is not None else {},
    )
    return config, registry


def call_route(registry, method: str, path: str, **query):
    route, params = find_route(registry.routes(), method, path)
    assert route is not None, f"没有匹配的路由：{method} {path}"
    context = RequestContext(
        method=method, path=path, query=dict(query), config=registry.config, registry=registry
    )
    return route.handler(context, **params)


def _http(server: WebUIServer, method: str, path: str, payload=None, raw: bytes = None):
    data = raw if raw is not None else (
        json.dumps(payload).encode("utf-8") if payload is not None else None
    )
    request = urllib.request.Request(f"{server.base_url}{path}", data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with _OPENER.open(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _wait(server: WebUIServer, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = _http(server, "GET", f"/api/v1/jobs/{job_id}")[1]["data"]
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内没有结束")


def _required_flags(path: Path, parser_var: str | None = None) -> set:
    """独立扫描脚本源码里的 `required=True` flag（不依赖插件里的扫描器）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    flags = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "add_argument" or not node.args:
            continue
        if parser_var is not None:
            base = node.func.value
            if not (isinstance(base, ast.Name) and base.id == parser_var):
                continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        required = any(
            keyword.arg == "required"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        )
        if required and first.value.startswith("--"):
            flags.add(first.value[2:].replace("-", "_"))
    return flags


@pytest.fixture(autouse=True)
def _isolated_parsers():
    parsers.reset_parsers()
    parsers.register_builtin_parsers()
    yield
    parsers.reset_parsers()
    parsers.register_builtin_parsers()


# --------------------------------------------------------------- AC-1.1 任务生命周期


def test_job_submission_is_async_and_reports_the_full_lifecycle(tmp_path):
    """AC-1.1：POST 立即返回任务 id；状态查询给出 running/finished/failed、退出码、日志与起止时间。"""
    config, registry = web_app(tmp_path)
    with WebUIServer(config, registry) as server:
        started = time.monotonic()
        status, payload = _http(server, "POST", "/api/v1/jobs", {"command": "test_sleep"})
        elapsed = time.monotonic() - started
        assert status == 200 and payload["ok"] is True
        running = payload["data"]
        assert running["id"].startswith("job-") and running["status"] == "running"
        assert elapsed < 2.0, "HTTP 不能阻塞到命令结束（AC-1.1）"
        assert running["started_at"] and running["finished_at"] is None
        _http(server, "POST", f"/api/v1/jobs/{running['id']}/cancel")

        ok = _wait(server, _http(server, "POST", "/api/v1/jobs", {"command": "test_echo"})
                   [1]["data"]["id"])
        assert ok["status"] == "finished" and ok["exit_code"] == 0
        assert "hello-from-job" in "\n".join(ok["log"])
        assert ok["finished_at"] and ok["finished_at"] >= ok["started_at"]

        bad = _wait(server, _http(server, "POST", "/api/v1/jobs", {"command": "test_fail"})
                    [1]["data"]["id"])
        assert bad["status"] == "failed" and bad["exit_code"] == 3
        assert "boom" in "\n".join(bad["log"])

        listing = _http(server, "GET", "/api/v1/jobs")[1]["data"]
        assert listing["count"] >= 3
        assert {job["id"] for job in listing["jobs"]} >= {ok["id"], bad["id"]}


# --------------------------------------------------------------- AC-1.2 按键清单


def test_command_catalog_covers_readme_entries_with_structured_params(tmp_path):
    """AC-1.2：按键清单覆盖既有入口，且每条都带结构化参数定义。"""
    _, registry = web_app(tmp_path)
    data = call_route(registry, "GET", "/api/v1/commands")["data"]
    by_id = {command["id"]: command for command in data["commands"]}
    assert EXPECTED_COMMANDS <= set(by_id), sorted(EXPECTED_COMMANDS - set(by_id))

    for command in data["commands"]:
        assert command["argv"], f"{command['id']} 没有命令行"
        if command["id"] not in EXPECTED_COMMANDS:
            continue
        assert command["group"], f"{command['id']} 没有分组"
        for param in command["params"]:
            assert set(param) == {
                "name", "type", "required", "default", "choices", "source", "help"
            }
            assert param["type"] in {
                "string", "int", "float", "bool", "enum", "list", "path", "company",
                "period", "run",
            }

    def required(command_id):
        return {param["name"] for param in by_id[command_id]["params"] if param["required"]}

    assert "stock_code" in required("discover_report")
    assert {"company_dir", "ticker", "company"} <= required("runs_new")
    assert {"output_dir", "ticker", "company"} <= required("results_prepare")
    assert by_id["portfolio_engine"]["params"], "组合引擎的选择型参数不能为空"

    # 凭据类参数不进面板：token 只从环境变量 / .env 读取，让面板接收它就会落进任务历史。
    all_params = {param["name"] for command in data["commands"] for param in command["params"]}
    assert not {name for name in all_params if "token" in name or "secret" in name}


def test_command_param_table_matches_each_script_cli_both_ways(tmp_path):
    """AC-1.2：表里的 flag 必须存在于脚本；脚本里 `required=True` 的 flag 必须在表里。"""
    _, registry = web_app(tmp_path)
    by_id = {
        command["id"]: command
        for command in call_route(registry, "GET", "/api/v1/commands")["data"]["commands"]
    }
    entries = {entry[0]: entry for entry in commands_plugin.ENTRIES}

    for command_id, entry in entries.items():
        _, _, _, script, subcommand, _, _ = entry
        source_path = (
            SCRIPTS / "results" / f"{script.split('.', 1)[1]}.py"
            if script.startswith("results.")
            else SCRIPTS / script
        )
        params = by_id[command_id]["params"]
        declared = {param["name"] for param in params}
        source_text = source_path.read_text(encoding="utf-8")
        for name in declared:  # 方向一：表 → 脚本
            assert "--" + name.replace("_", "-") in source_text, (
                f"{command_id}：表里的 --{name.replace('_', '-')} 在 {source_path.name} 里不存在"
            )
        parser_var = RUNS_PARSER_VARS.get(command_id) if script == "runs.py" else None
        required = _required_flags(source_path, parser_var)  # 方向二：脚本 → 表
        assert required <= declared, (
            f"{command_id}：脚本里这些 flag 是 required=True，却不在参数表里："
            f"{sorted(required - declared)}"
        )

    # 表本身也要能看出「必填」，否则前端无法提示（AC-1.2 的「是否必填」）。
    assert {param["name"] for param in by_id["runs_new"]["params"] if param["required"]} == {
        "company_dir", "ticker", "company"
    }


# --------------------------------------------------------------- AC-1.3 拒绝与限流


def test_invalid_submissions_are_rejected_before_any_subprocess(tmp_path):
    """AC-1.3：未知命令 / 未声明参数 / 缺必填 / shell 元字符一律拒绝，且**不起子进程**。"""
    config, registry = web_app(tmp_path, history_dir=tmp_path / "jobs", env={})
    calls: list = []

    def exploding_popen(*args, **kwargs):  # pragma: no cover - 被调用即失败
        calls.append(args)
        raise AssertionError("拒绝路径不该启动子进程")

    registry.jobs = JobRunner(
        config, spec_lookup=registry.command_spec, history_dir=tmp_path / "jobs", env={},
        popen=exploding_popen,
    )
    with pytest.raises(UnknownCommand):
        registry.jobs.submit("nope")
    with pytest.raises(InvalidParam):
        registry.jobs.submit("test_echo", {"bogus": "1"})
    with pytest.raises(InvalidParam):
        registry.jobs.submit("test_need")
    with pytest.raises(ShellMetachar):
        registry.jobs.submit("test_need", {"must": "a; rm -rf /"})
    with pytest.raises(ShellMetachar):
        registry.jobs.submit("test_need", {"must": "x$(whoami)"})

    with WebUIServer(config, registry) as server:
        status, payload = _http(server, "POST", "/api/v1/jobs", raw=b"{oops")
        assert status == 400 and payload["error"]["code"] == "BAD_JSON"
        status, payload = _http(server, "POST", "/api/v1/jobs", {"params": {}})
        assert status == 400 and payload["error"]["code"] == "BAD_REQUEST"
        status, payload = _http(server, "POST", "/api/v1/jobs", {"command": "nope"})
        assert status == 404 and payload["error"]["code"] == "UNKNOWN_COMMAND"
        status, payload = _http(server, "POST", "/api/v1/jobs",
                                {"command": "test_need", "params": {"must": "a|b"}})
        assert status == 400 and payload["error"]["code"] == "SHELL_METACHAR"

    assert calls == [], "拒绝路径启动了子进程"
    assert isinstance(TooManyJobs("x"), WebUIError)     # 429 也是 WebUIError 家族

    # 合法提交走的是**列表 argv + shell=False**（AC-1.3 的另一半：命令不经过 shell）。
    recorded: list = []

    class _FakeProcess:
        def __init__(self):
            self.stdout = iter(["ok\n"])

        def wait(self):
            return 0

        def terminate(self):  # pragma: no cover - 本用例不会取消
            pass

    def recording_popen(argv, **kwargs):
        recorded.append((argv, kwargs))
        return _FakeProcess()

    shell_free = JobRunner(
        config, spec_lookup=registry.command_spec, history_dir=tmp_path / "jobs", env={},
        popen=recording_popen,
    )
    job = shell_free.submit("test_need", {"must": "600887"})
    for _ in range(100):
        if shell_free.get(job["id"])["status"] != "running":
            break
        time.sleep(0.02)
    assert recorded, "合法提交应当启动子进程"
    argv, kwargs = recorded[0]
    assert kwargs["shell"] is False
    assert isinstance(argv, list) and argv[-2:] == ["--must", "600887"]

    with WebUIServer(config, registry) as server:
        status, payload = _http(server, "GET", "/api/v1/jobs/does-not-exist")
        assert status == 404 and payload["error"]["code"] == "JOB_NOT_FOUND"


def test_concurrency_limit_returns_too_many_jobs(tmp_path):
    """AC-1.3：并发任务上限——超限 429，任务结束后名额释放。"""
    config, registry = web_app(tmp_path, max_jobs=1)
    with WebUIServer(config, registry) as server:
        first = _http(server, "POST", "/api/v1/jobs", {"command": "test_sleep"})[1]["data"]
        assert first["status"] == "running"
        status, payload = _http(server, "POST", "/api/v1/jobs", {"command": "test_sleep"})
        assert status == 429 and payload["error"]["code"] == "TOO_MANY_JOBS"

        _http(server, "POST", f"/api/v1/jobs/{first['id']}/cancel")
        _wait(server, first["id"])
        assert registry.jobs.get(first["id"])["status"] in ("cancelled", "failed", "finished")

        status, payload = _http(server, "POST", "/api/v1/jobs", {"command": "test_echo"})
        assert status == 200, "任务结束后并发名额必须释放"


# --------------------------------------------------------------- AC-1.4 历史与脱敏


def test_job_history_survives_restart_and_redacts_tokens(tmp_path):
    """AC-1.4：任务历史落盘（重启后可查）且落盘/响应里都不出现 token。"""
    token = "abcdef0123456789abcdef0123456789"
    history_dir = tmp_path / "jobs"
    config, registry = web_app(tmp_path, history_dir=history_dir, env={"TUSHARE_TOKEN": token})
    registry.command(CommandSpec(
        id="test_token", argv=(sys.executable, "-c", f"print('{token}')"), title="打印凭据",
    ))
    with WebUIServer(config, registry, env={"TUSHARE_TOKEN": token}) as server:
        job_id = _http(server, "POST", "/api/v1/jobs", {"command": "test_token"})[1]["data"]["id"]
        finished = _wait(server, job_id)
        assert finished["status"] == "finished"
        assert token not in json.dumps(finished, ensure_ascii=False)
        assert "***" in json.dumps(finished, ensure_ascii=False)
        listing = json.dumps(_http(server, "GET", "/api/v1/jobs")[1], ensure_ascii=False)
        assert token not in listing

    saved = (history_dir / f"{job_id}.json").read_text(encoding="utf-8")
    assert token not in saved and "***" in saved

    restarted = JobRunner(
        config, spec_lookup=registry.command_spec, history_dir=history_dir, env={}
    )
    reloaded = restarted.get(job_id)
    assert reloaded["status"] == "finished" and reloaded["exit_code"] == 0
    assert "***" in reloaded["log"][-1]


def test_gui_command_defaults_keep_conditional_required_flags_runnable(tmp_path):
    """界面默认值不能把「点下去必然 usage error」的按键暴露给用户（用户实机体验发现）。

    `analysis_status.py` 的 `--company-dir` 是**条件必填**（给了 `--root --all` 就不用），
    argparse 静态扫描只能看到 `required=False`——所以按键表里它没有任何必填项，
    用户点「更新判定」只会拿到 `exit 2` 加一行 usage。GUI_DEFAULTS 补上可运行的默认值，
    且默认值必须真的拼进 argv（不是只写在表单里好看）。
    """
    from webui.core.jobs import build_argv

    spec = next(item for item in commands_plugin.command_specs() if item.id == "analysis_status")
    params = {param.name: param for param in spec.params}
    assert params["root"].default == "output"
    assert params["all"].default is True
    defaults = {
        param.name: param.default
        for param in spec.params
        if param.default not in (None, "", False)
    }
    argv = build_argv(spec, defaults)
    assert "--root" in argv and "output" in argv and "--all" in argv
