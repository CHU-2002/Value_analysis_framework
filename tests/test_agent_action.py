# 覆盖需求：REQ-015、REQ-015.2（AC-2.1~2.4：目录、单次模型传递、非法组合零分析、产物判定与审计）
# 覆盖需求：REQ-013.1（包装脚本与动作白名单：AC-1.1~AC-1.4）
# 覆盖需求：REQ-013.2（最小界面「一键页」：AC-2.1~AC-2.5）
# 覆盖需求：REQ-013、REQ-013.3（预检、链接、审计、并发与取消：AC-1~AC-7 / AC-3.1~AC-3.4）
"""一键出报告的测试。

三条刻意的手法：

1. **假 CLI**：`tmp_path` 里一个带 shebang 的小脚本，把收到的 argv 写成 JSON，
   并按环境变量决定退出码 / sleep。**测试不调用任何真实模型**（AC-8 的实跑是人的事）。
2. **「没启动进程」是可断言的**：假 CLI 一被调用就会写出 marker 文件——
   拒绝路径的判据因此不是「返回了非 0」而是「marker 文件不存在」。
3. **prompt 必须是单个 argv 元素**：如果哪天有人改成 shell 拼接，
   `/update-analysis 600887.SH` 会被拆成两个参数，断言立刻红。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import agent_action
from webui.config import Config
from webui.core.context import RequestContext
from webui.core.errors import InvalidParam
from webui.core.jobs import build_argv, JobRunner
from webui.core.registry import build_registry
from webui.core.router import find_route
from webui.core.routes import install_core_routes
from webui.plugins import agent_report as agent_plugin

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "agent_action.py"
FAKE_CLI_SOURCE = '''#!__PYTHON__
import json
import os
import sys
import time

out = os.environ.get("FAKE_CLI_OUT")
if out:
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(sys.argv[1:], handle, ensure_ascii=False)
if os.environ.get("FAKE_CLI_CHILD_PID"):
    import subprocess
    child = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"])
    with open(os.environ["FAKE_CLI_CHILD_PID"], "w") as handle:
        handle.write(str(child.pid))
sleep_for = float(os.environ.get("FAKE_CLI_SLEEP") or 0)
if sleep_for:
    time.sleep(sleep_for)
if os.environ.get("FAKE_CLI_SECRET"):
    print(os.environ["FAKE_CLI_SECRET"], flush=True)
if os.environ.get("FAKE_CLI_ARTIFACTS"):
    from pathlib import Path
    base = Path(os.environ["FAKE_CLI_ARTIFACTS"])
    run = base / "runs" / "new-run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "run.json").write_text('{"run_id":"new-run"}')
    (run / "qualitative_report.md").write_text("# updated report")
    if not os.environ.get("FAKE_CLI_NO_CHANGE"):
        (run / "change_report_2026H1.md").write_text("# changed")
sys.stdout.write("fake-cli-stdout\\n")
sys.stdout.flush()
sys.stderr.write("fake-cli-stderr\\n")
sys.stderr.flush()
sys.exit(int(os.environ.get("FAKE_CLI_EXIT") or 0))
'''


# --------------------------------------------------------------- 测试辅助


def make_fake_cli(tmp_path: Path, name: str = "fake-agent-cli") -> Path:
    path = tmp_path / name
    path.write_text(FAKE_CLI_SOURCE.replace("__PYTHON__", sys.executable), encoding="utf-8")
    path.chmod(0o755)
    return path


def make_company_dir(tmp_path: Path, name: str = "600887_伊利") -> Path:
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def run_script(tmp_path: Path, *extra, env=None):
    """跑包装脚本；返回 (CompletedProcess, 假 CLI 的 marker 文件路径)。"""
    marker = tmp_path / "cli-argv.json"
    child_env = dict(os.environ)
    child_env["FAKE_CLI_OUT"] = str(marker)
    child_env.update(env or {})
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *[str(item) for item in extra]],
        capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT), env=child_env,
    )
    return completed, marker


def make_app(tmp_path: Path):
    config = Config(
        host="127.0.0.1", port=0,
        output_root=tmp_path / "output", cache_dir=tmp_path / "output",
        archive_root=tmp_path / "archive",
    )
    registry = build_registry()
    install_core_routes(registry, config)
    registry.config = config
    agent_plugin.contribute(registry)
    return config, registry


def call_route(registry, method: str, path: str, **query):
    route, params = find_route(registry.routes(), method, path)
    assert route is not None, f"没有匹配的路由：{method} {path}"
    context = RequestContext(
        method=method, path=path, query=dict(query), config=registry.config, registry=registry
    )
    return route.handler(context, **params)


# --------------------------------------------------------------- REQ-013.1 包装脚本


def test_unknown_action_exits_2_and_starts_no_process(tmp_path):
    """AC-1.1：不在枚举里的动作 → 非 0 退出，且**没有**任何外部进程被启动。"""
    completed, marker = run_script(
        tmp_path, "--action", "rm-everything", "--ticker", "600887.SH",
        "--cli", make_fake_cli(tmp_path), "--output-root", tmp_path,
    )
    assert completed.returncode == 2
    assert "action" in completed.stderr
    assert not marker.exists()


def test_invalid_ticker_exits_2_and_starts_no_process(tmp_path):
    """AC-1.1 / AC-2：非法标的 → 非 0 退出 + 人话原因，且没有启动进程。"""
    completed, marker = run_script(
        tmp_path, "--action", "update-analysis", "--ticker", "不是代码",
        "--cli", make_fake_cli(tmp_path), "--output-root", tmp_path,
    )
    assert completed.returncode == 2
    assert "标的校验失败" in completed.stderr
    assert not marker.exists()


def test_valid_ticker_without_company_dir_exits_2_and_starts_no_process(tmp_path):
    """AC-2：代码合法但本机找不到 `output/<code>_*/` → 非 0 退出且不启动进程。"""
    completed, marker = run_script(
        tmp_path, "--action", "update-analysis", "--ticker", "600887.SH",
        "--cli", make_fake_cli(tmp_path), "--output-root", tmp_path,
    )
    assert completed.returncode == 2
    assert "找不到" in completed.stderr and "600887" in completed.stderr
    assert not marker.exists()


def test_dry_run_prints_the_command_line_and_starts_no_process(tmp_path):
    """AC-1.3：`--dry-run` 只打印命令行与解析出的路径，不启动任何进程。"""
    company = make_company_dir(tmp_path)
    completed, marker = run_script(
        tmp_path, "--action", "update-analysis", "--ticker", "600887.SH",
        "--cli", make_fake_cli(tmp_path), "--output-root", tmp_path, "--dry-run",
    )
    assert completed.returncode == 0
    assert "-p" in completed.stdout
    assert "/update-analysis 600887.SH" in completed.stdout
    assert str(company) in completed.stdout
    assert "--dry-run" in completed.stdout
    assert not marker.exists()


def test_build_agent_argv_and_action_table_agree():
    """AC-1.1：命令行形状是 `[cli, "-p", "/<动作> <代码>"]`，动作表与 choices 一致。"""
    assert agent_action.build_agent_argv("claude", "update-analysis", "600887.SH") == [
        "claude", "-p", "/update-analysis 600887.SH",
    ]
    assert set(agent_action.ACTIONS) == set(agent_action.ACTION_CHOICES)
    for action in agent_action.ACTION_CHOICES:
        prompt = agent_action.build_prompt(action, "600887.SH")
        assert prompt == f"/{action} 600887.SH"
        assert prompt.count("{") == 0 and "ticker" not in prompt


def test_prompt_is_a_single_argv_element(tmp_path):
    """AC-1.2：参数按列表传递（不经 shell）——prompt 带着空格也必须是一个元素。"""
    make_company_dir(tmp_path)
    completed, marker = run_script(
        tmp_path, "--action", "update-analysis", "--ticker", "600887.SH",
        "--cli", make_fake_cli(tmp_path), "--output-root", tmp_path,
    )
    assert completed.returncode == 0
    assert json.loads(marker.read_text(encoding="utf-8")) == ["-p", "/update-analysis 600887.SH"]


def test_agent_exit_code_is_passed_through(tmp_path):
    """AC-1.4：agent CLI 的退出码原样透传（这里是 3），本脚本不自动重试。"""
    make_company_dir(tmp_path)
    completed, marker = run_script(
        tmp_path, "--action", "value-analysis", "--ticker", "600887.SH",
        "--cli", make_fake_cli(tmp_path), "--output-root", tmp_path,
        env={"FAKE_CLI_EXIT": "3"},
    )
    assert completed.returncode == 3
    assert marker.exists()  # 假 CLI 确实跑过 → 这个 3 是透传，不是「找不到 CLI」


def test_child_output_is_streamed_to_this_process(tmp_path):
    """AC-5（审计）：子进程输出逐行透传，任务日志里能看到进度而不是一片空白。"""
    make_company_dir(tmp_path)
    completed, _ = run_script(
        tmp_path, "--action", "business-analysis", "--ticker", "600887.SH",
        "--cli", make_fake_cli(tmp_path), "--output-root", tmp_path,
    )
    assert "fake-cli-stdout" in completed.stdout
    assert "fake-cli-stderr" in completed.stderr


def test_missing_cli_exits_3_with_install_hint(tmp_path):
    """AC-6：CLI 不存在 → 退出码 3 + 安装/登录指引，且不启动进程、不留半成品。"""
    make_company_dir(tmp_path)
    completed, marker = run_script(
        tmp_path, "--action", "update-analysis", "--ticker", "600887.SH",
        "--cli", tmp_path / "no-such-cli", "--output-root", tmp_path,
    )
    assert completed.returncode == 3
    assert "找不到 agent CLI" in completed.stderr
    assert "--cli" in completed.stderr and "安装并登录" in completed.stderr
    assert not marker.exists()


def test_non_executable_cli_exits_3(tmp_path):
    """AC-1.2：文件存在但不可执行 → 同样按「找不到 CLI」处理（退出码 3）。"""
    make_company_dir(tmp_path)
    not_executable = tmp_path / "cli-without-x"
    not_executable.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    completed, marker = run_script(
        tmp_path, "--action", "update-analysis", "--ticker", "600887.SH",
        "--cli", not_executable, "--output-root", tmp_path,
    )
    assert completed.returncode == 3
    assert not marker.exists()


def test_timeout_exits_4(tmp_path):
    """AC-1.4：`--timeout` 到点就终止并给明确退出码，不静默挂住。"""
    make_company_dir(tmp_path)
    completed, _ = run_script(
        tmp_path, "--action", "update-analysis", "--ticker", "600887.SH",
        "--cli", make_fake_cli(tmp_path), "--output-root", tmp_path, "--timeout", "1",
        env={"FAKE_CLI_SLEEP": "5"},
    )
    assert completed.returncode == 4
    assert "超时" in completed.stderr


def test_agent_cli_env_var_is_used_when_the_flag_is_absent(tmp_path):
    """AC-1.2：CLI 可执行文件可配置——没有 `--cli` 时取环境变量 `AGENT_CLI`。"""
    make_company_dir(tmp_path)
    fake = make_fake_cli(tmp_path)
    completed, marker = run_script(
        tmp_path, "--action", "update-analysis", "--ticker", "600887.SH",
        "--output-root", tmp_path, env={"AGENT_CLI": str(fake)},
    )
    assert completed.returncode == 0
    assert json.loads(marker.read_text(encoding="utf-8"))[-1] == "/update-analysis 600887.SH"


def test_directory_code_derivation_matches_the_slash_command_convention(tmp_path):
    """AC-2：`{directory_code}` = 只去掉最后的市场后缀（与 `.claude/commands/*.md` 一致）。"""
    assert agent_action.directory_code("600887.SH") == "600887"
    codex = agent_action.build_agent_argv("/bin/codex", "update-analysis", "600887.SH", "codex")
    assert codex[:6] == ["/bin/codex", "exec", "--sandbox", "workspace-write", "--json", codex[-1]]
    assert "/update-analysis 600887.SH" in codex[-1]
    assert ".claude/commands/update-analysis.md" in codex[-1]
    assert agent_action.resolve_backend("codex") == "codex"
    assert agent_action.directory_code("00700.HK") == "00700"
    assert agent_action.directory_code("AAPL.US") == "AAPL"
    assert agent_action.directory_code("BRK.B.US") == "BRK.B"
    make_company_dir(tmp_path, "600887_伊利")
    make_company_dir(tmp_path, "600887_伊利股份")
    found = agent_action.find_company_dirs(tmp_path, "600887.SH")
    assert [path.name for path in found] == ["600887_伊利", "600887_伊利股份"]
    assert agent_action.find_company_dirs(tmp_path / "missing", "600887.SH") == []


# --------------------------------------------------------------- REQ-013.2 最小界面


def test_agent_actions_route_exposes_exactly_three_commands(tmp_path):
    """AC-2.1：专用目录接口只含这 3 个按键（不把既有的 23 个按键都列出来）。"""
    _, registry = make_app(tmp_path)
    payload = call_route(registry, "GET", agent_plugin.ENDPOINT)
    assert payload["ok"] is True
    assert payload["data"]["count"] == 3
    assert [item["id"] for item in payload["data"]["commands"]] == [
        "agent_update_analysis", "agent_business_analysis", "agent_value_analysis",
    ]


def test_each_command_pins_its_action_and_exposes_only_the_ticker(tmp_path):
    """AC-2.1 / AC-2.3：动作固定进 argv；表单只暴露 ticker，且说明里说了会消耗额度。"""
    _, registry = make_app(tmp_path)
    commands = call_route(registry, "GET", agent_plugin.ENDPOINT)["data"]["commands"]
    expected_actions = [action for _, action, _, _ in agent_plugin.ACTIONS]
    for item, action in zip(commands, expected_actions):
        assert [param["name"] for param in item["params"]] == ["ticker", "backend", "model"]
        assert item["params"][0]["required"] is True
        assert "600887.SH" in item["params"][0]["help"]
        assert item["argv"][2:4] == ["--action", action]
        assert item["group"] == agent_plugin.GROUP
        assert item["danger"] is True                      # 复用既有「确认执行」语义（AC-4）
        assert "消耗模型额度" in item["description"]


def test_hidden_flags_are_scanned_from_the_script_but_not_shown(tmp_path):
    """AC-2.1：参数表来自脚本源码扫描；`--action` 等内部 flag 不进入表单。"""
    scanned = agent_plugin.scanned_params()
    assert {"action", "ticker", "cli", "timeout", "output_root", "dry_run"} <= set(scanned)
    assert [param.name for param in agent_plugin.display_params()] == ["ticker"]
    # 暴露的是扫描出来的那个 Param（required / help 随脚本走），不是手写的第二份
    assert agent_plugin.display_params()[0] == scanned["ticker"]


def test_build_argv_yields_the_full_audited_command_line(tmp_path):
    """AC-4 / AC-5：真实命令行 = 解释器 + 脚本 + 固定动作 + 用户填的标的。"""
    config, registry = make_app(tmp_path)
    spec = registry.command_spec("agent_update_analysis")
    assert build_argv(spec, {"ticker": "600887.SH"}) == [
        sys.executable, str(SCRIPT), "--action", "update-analysis",
        "--output-root", str(config.output_root.resolve()), "--require-report",
        "--ticker", "600887.SH",
    ]


def test_free_text_cannot_enter_the_command(tmp_path):
    """AC-1：没有自由文本入口——未声明的参数在起进程之前就被拒绝。"""
    _, registry = make_app(tmp_path)
    spec = registry.command_spec("agent_update_analysis")
    with pytest.raises(InvalidParam):
        build_argv(spec, {"ticker": "600887.SH", "prompt": "rm -rf /"})
    with pytest.raises(InvalidParam):
        build_argv(spec, {})


def test_agent_page_is_a_form_panel_pointing_at_the_dedicated_endpoint(tmp_path):
    """AC-2.1 / AC-2.5：独立 agent-form 继续使用 CommandSpec 与相同预检/确认协议；页面不降级。"""
    _, registry = make_app(tmp_path)
    payload = call_route(registry, "GET", "/api/v1/pages/agent")
    assert payload["warnings"] == []
    page = payload["data"]
    assert page["id"] == "agent" and page["panels"]
    panel = page["panels"][0]
    assert panel["id"] == "agent.actions"
    assert panel["kind"] == "agent-form" and panel["render"] == "client"
    assert panel["endpoint"] == agent_plugin.ENDPOINT
    assert len(panel["data"]["commands"]) == 3


def test_agent_nav_item_needs_no_company_context(tmp_path):
    """AC-2.1：`agent` 是一个独立导航页，不依赖「当前公司」上下文。"""
    _, registry = make_app(tmp_path)
    items = call_route(registry, "GET", "/api/v1/nav")["data"]["items"]
    item = next(entry for entry in items if entry["id"] == "agent")
    assert item["title"] == "生成报告"
    assert item["group"] == "分析"
    assert item["panels"] == ["agent.actions"]
    assert not item.get("requires")
    assert item["description"]


def test_user_facing_text_has_no_internal_details(tmp_path):
    """AC-2.3：标题与说明里不得出现 CLI 开关、脚本名、路径或内部参数名。"""
    forbidden = ("--action", "--ticker", "--cli", "--output-root", "--dry-run",
                 "agent_action", "output/", "output_root", "dry_run", "/Users", ".venv")
    for _, _, title, description in agent_plugin.ACTIONS:
        for text in (title, description, agent_plugin.GROUP):
            assert not [token for token in forbidden if token in text], text
    _, registry = make_app(tmp_path)
    page = call_route(registry, "GET", "/api/v1/pages/agent")["data"]
    panel = page["panels"][0]
    for text in (panel["title"], panel["description"]):
        assert not [token for token in forbidden if token in text], text


def test_plugin_only_adds_registrations_through_existing_extension_points(tmp_path):
    """AC-2.5：本片只加 1 条路由 + 3 个按键 + 1 个面板 + 1 个导航项，不新增核心能力。"""
    config, _ = make_app(tmp_path)
    registry = build_registry()
    install_core_routes(registry, config)
    before = {(route.method, route.template) for route in registry.routes()}
    agent_plugin.contribute(registry)
    added = {(route.method, route.template) for route in registry.routes()} - before
    assert added == {("GET", agent_plugin.ENDPOINT),
                     ("GET", "/api/v1/agent/preflight"),
                     ("GET", "/api/v1/agent/jobs/{job_id}/artifacts")}
    assert [spec.id for spec in registry.commands() if spec.id.startswith("agent_")] == [
        "agent_business_analysis", "agent_update_analysis", "agent_value_analysis",
    ]
    assert "agent.actions" in registry.panel_ids()


# Three scenarios keep the REQ-013 budget at 25 cases.
def wait_for(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if predicate():
            return
        threading.Event().wait(0.02)
    raise AssertionError("task did not reach the expected state")


def test_preflight_and_all_submission_paths_reject_before_launch(tmp_path, monkeypatch):
    """AC-1/2/6, AC-3.1/3.2: read-only preflight and runner validation."""
    fake = make_fake_cli(tmp_path)
    monkeypatch.setenv("AGENT_CLI", str(fake))
    config, registry = make_app(tmp_path)
    company = make_company_dir(config.output_root)
    calls = []
    runner = JobRunner(config, spec_lookup=registry.command_spec,
                       popen=lambda *a, **kw: calls.append(a))
    registry.jobs = runner
    path = "/api/v1/agent/preflight"
    preview = call_route(registry, "GET", path,
                         command="agent_update_analysis", ticker="600887")["data"]
    assert preview["ready"] and preview["ticker"] == "600887.SH"
    assert str(fake.resolve()) in preview["command_line"]
    assert "/update-analysis 600887.SH" in preview["command_line"]
    assert "消耗模型额度" in preview["notice"]
    assert runner.list_jobs() == [] and not calls
    for ticker in ("not a ticker", "999999.SH", "600887.SH;echo x"):
        assert not call_route(registry, "GET", path, command="agent_update_analysis",
                              ticker=ticker)["data"]["ready"]
        with pytest.raises(Exception) as failure:
            runner.submit("agent_update_analysis", {"ticker": ticker})
        assert failure.value.status == (400 if ";" in ticker else 422)
    with pytest.raises(Exception) as failure:
        call_route(registry, "GET", path, command="arbitrary", ticker="600887.SH")
    assert failure.value.status == 404
    for extra in ("prompt", "api_key", "cli"):
        with pytest.raises(InvalidParam):
            runner.submit("agent_update_analysis", {"ticker": "600887", extra: "x"})
    fake.chmod(0o644)
    assert not call_route(registry, "GET", path, command="agent_update_analysis",
                          ticker="600887")["data"]["ready"]
    with pytest.raises(InvalidParam, match="安装并登录"):
        runner.submit("agent_update_analysis", {"ticker": "600887"})
    assert runner.list_jobs() == [] and not calls
    assert list(company.iterdir()) == []
    # CLI submissions using a different market suffix resolve to the same
    # directory and must share its lock, without launching the fake CLI.
    import fcntl
    fake.chmod(0o755)
    locks = config.output_root / ".agent_locks"
    locks.mkdir()
    with (locks / f"{company.name}.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed, marker = run_script(
            tmp_path, "--action", "value-analysis", "--ticker", "600887.SZ",
            "--output-root", config.output_root, "--cli", fake)
        assert completed.returncode == agent_action.EXIT_BUSY
        assert not marker.exists()


def test_jobs_normalize_deduplicate_cancel_and_preserve_redacted_audit(tmp_path, monkeypatch):
    """AC-3/5/7: atomic duplicate protection, cancellation and persisted audit."""
    from concurrent.futures import ThreadPoolExecutor
    from webui.core.errors import DuplicateJob
    fake = make_fake_cli(tmp_path)
    monkeypatch.setenv("AGENT_CLI", str(fake))
    config, registry = make_app(tmp_path)
    make_company_dir(config.output_root)
    marker = tmp_path / "spawned.json"
    secret = "fake-model-credential-for-test"
    env = {**os.environ, "FAKE_CLI_OUT": str(marker), "FAKE_CLI_SLEEP": "30",
           "ANTHROPIC_API_KEY": secret, "FAKE_CLI_CHILD_PID": str(tmp_path / "child.pid")}
    runner = JobRunner(config, spec_lookup=registry.command_spec, env=env)
    def submit(ticker):
        try:
            return runner.submit("agent_update_analysis", {"ticker": ticker})
        except DuplicateJob as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ("600887", "600887.SH")))
    assert sum(isinstance(item, DuplicateJob) for item in results) == 1
    duplicate = next(item for item in results if isinstance(item, DuplicateJob))
    assert duplicate.status == 409 and duplicate.code == "DUPLICATE_JOB"
    job = next(item for item in results if isinstance(item, dict))
    with pytest.raises(DuplicateJob):
        runner.submit("agent_value_analysis", {"ticker": "600887"})
    with pytest.raises(DuplicateJob):
        runner.submit("agent_value_analysis", {"ticker": "600887.SZ"})
    wait_for(lambda: marker.exists() and (tmp_path / "child.pid").exists())
    runner.cancel(job["id"])
    wait_for(lambda: runner.get(job["id"])["status"] == "cancelled")
    first = runner.get(job["id"])
    assert first["finished_at"] and first["params"] == {"ticker": "600887.SH"}
    assert first["danger"] and "消耗模型额度" in first["description"]
    assert first["argv"][-2:] == ["--ticker", "600887.SH"]
    env.pop("FAKE_CLI_CHILD_PID")
    env.update(FAKE_CLI_SLEEP="0", FAKE_CLI_SECRET=secret, FAKE_CLI_EXIT="7")
    second_runner = JobRunner(config, spec_lookup=registry.command_spec, env=env)
    second = second_runner.submit("agent_update_analysis", {"ticker": "600887"})
    wait_for(lambda: second_runner.get(second["id"])["status"] == "failed")
    wait_for(lambda: (second_runner.history_dir / (second["id"] + ".json")).exists())
    detail = second_runner.get(second["id"])
    history = (second_runner.history_dir / (second["id"] + ".json")).read_text()
    assert detail["exit_code"] == 7
    assert secret not in json.dumps(detail) + history
    assert "***" in history
    assert len(second_runner.list_jobs()) == 2  # explicit submissions only; no auto retry
    assert any("手动" in line for line in detail["log"])
    failed_job = second_runner._jobs[second["id"]]
    failed_job.error = "服务重启，已中断"
    failed_job.log.append("model gpt-test not supported")
    assert "中断" in second_runner._summarise(failed_job)["what"]


def test_only_this_tasks_new_outputs_link_and_empty_result_is_explicit(tmp_path, monkeypatch):
    """AC-5, AC-3.3/3.4: fresh artifacts, safe links, restart and empty result."""
    from webui.plugins import companies
    from webui.datastore import DataStore
    fake = make_fake_cli(tmp_path)
    monkeypatch.setenv("AGENT_CLI", str(fake))
    config, registry = make_app(tmp_path)
    companies.contribute(registry)
    registry.datastore = DataStore(config, spec_lookup=registry.dataset_spec)
    company = make_company_dir(config.output_root)
    old = company / "qualitative_report.md"
    old.write_text("# old")
    os.utime(old, (1, 1))
    env = {**os.environ, "FAKE_CLI_ARTIFACTS": str(company)}
    runner = JobRunner(config, spec_lookup=registry.command_spec, env=env)
    registry.jobs = runner
    job = runner.submit("agent_update_analysis", {"ticker": "600887"})
    wait_for(lambda: runner.get(job["id"])["status"] == "finished")
    path = f"/api/v1/agent/jobs/{job['id']}/artifacts"
    output = call_route(registry, "GET", path)["data"]
    assert len(output["links"]) == 3 and not output["message"]
    assert all(link["path"] != "qualitative_report.md" for link in output["links"])
    from urllib.parse import urlsplit, parse_qs
    for link in output["links"]:
        if link["href"].startswith("#report"):
            query = {key: values[0] for key, values in parse_qs(
                urlsplit(link["href"][1:]).query).items()}
            response = call_route(registry, "GET", f"/api/v1/companies/{query['company']}/report", **query)
            assert response["data"]["html"]
    wait_for(lambda: (runner.history_dir / (job["id"] + ".json")).exists())
    registry.jobs = JobRunner(config, spec_lookup=registry.command_spec, env=os.environ)
    assert call_route(registry, "GET", path)["data"]["links"] == output["links"]
    empty = registry.jobs.submit("agent_value_analysis", {"ticker": "600887"})
    wait_for(lambda: registry.jobs.get(empty["id"])["status"] == "failed")
    assert registry.jobs.get(empty["id"])["exit_code"] == 6
    no_output = call_route(registry, "GET", f"/api/v1/agent/jobs/{empty['id']}/artifacts")["data"]
    assert no_output["links"] == [] and "报告未产出" in no_output["message"]
    assert no_output["outcome"] == "missing" and no_output["actual_model"] == "未知"
    # A cancelled queue entry never executed; another producer's fresh file
    # must not become its artifact merely because it waited in the queue.
    from dataclasses import replace
    config = replace(config, max_concurrent_jobs=1)
    from webui.core.models import CommandSpec
    registry.command(CommandSpec(id="unrelated_busy", argv=(str(fake),)))
    marker = tmp_path / "slow-cli-started.json"
    slow = JobRunner(config, spec_lookup=registry.command_spec,
                     env={**os.environ, "FAKE_CLI_SLEEP": "30",
                          "FAKE_CLI_OUT": str(marker)})
    registry.jobs = slow
    active = slow.submit("unrelated_busy", {})
    wait_for(marker.exists)
    queued = slow.submit("agent_value_analysis", {"ticker": "600887"})
    assert queued["status"] == "queued"
    old.write_text("# concurrent producer's report")
    slow.cancel(queued["id"])
    cancelled_outputs = call_route(
        registry, "GET", f"/api/v1/agent/jobs/{queued['id']}/artifacts")["data"]
    assert cancelled_outputs["links"] == [] and "报告未产出" in cancelled_outputs["message"]
    assert cancelled_outputs["outcome"] == "missing" and cancelled_outputs["audit"] == {}
    assert "execution_started_at" not in slow.get(queued["id"])["outputs"]
    slow.cancel(active["id"])
    wait_for(lambda: slow.get(active["id"])["status"] == "cancelled")
    slow.shutdown()


@pytest.mark.parametrize("backend,model", [("codex", "gpt-5.5"), ("claude", "sonnet")])
def test_selected_model_reaches_each_fake_cli_once_and_is_audited(tmp_path, backend, model):
    # AC-2.3: both protocols receive the selected model, per-process only.
    base = make_company_dir(tmp_path)
    completed, marker = run_script(tmp_path, "--action", "update-analysis", "--ticker", "600887",
                                   "--backend", backend, "--model", model,
                                   "--cli", make_fake_cli(tmp_path), "--output-root", tmp_path,
                                   "--require-report", env={"FAKE_CLI_ARTIFACTS": str(base)})
    assert completed.returncode == 0
    argv = json.loads(marker.read_text())
    assert argv[argv.index("--model") + 1] == model
    assert ("exec" in argv) == (backend == "codex")
    assert ("-p" in argv) == (backend == "claude")
    audit = json.loads(next((tmp_path / ".agent_audit").glob("*.json")).read_text())
    assert audit["requested_model"] == model and audit["requested_agent"] == backend
    assert audit["actual_model"] == "未知" and audit["reports"]
    assert not (tmp_path / "config.toml").exists()
    partial_root = tmp_path / "partial"
    partial_base = make_company_dir(partial_root)
    partial, _ = run_script(partial_root, "--action", "update-analysis", "--ticker", "600887",
                            "--backend", backend, "--model", model, "--cli", make_fake_cli(partial_root),
                            "--output-root", partial_root, "--require-report",
                            env={"FAKE_CLI_ARTIFACTS": str(partial_base), "FAKE_CLI_NO_CHANGE": "1"})
    assert partial.returncode == 6 and "变化报告" in partial.stdout and "不会自动重跑" in partial.stdout
    partial_audit = json.loads(next((partial_root / ".agent_audit").glob("*.json")).read_text())
    assert partial_audit["exit_code"] == 0 and partial_audit["missing_reports"] == ["变化报告"]


def test_invalid_agent_model_combinations_never_launch(tmp_path, monkeypatch):
    fake = make_fake_cli(tmp_path)
    monkeypatch.setenv("AGENT_CLI", str(fake))
    make_company_dir(tmp_path)
    for backend, model in [("codex", "sonnet"), ("claude", "gpt-5.5"), ("codex", "gpt-6.1-sol"), ("codex", "bad;command")]:
        completed, marker = run_script(tmp_path, "--action", "value-analysis", "--ticker", "600887",
                                       "--backend", backend, "--model", model, "--cli", fake, "--output-root", tmp_path)
        assert completed.returncode == 2 and not marker.exists()
    config, registry = make_app(tmp_path / "gui")
    make_company_dir(config.output_root)
    calls = []
    runner = JobRunner(config, spec_lookup=registry.command_spec, popen=lambda *a, **kw: calls.append(a))
    for params in ({"ticker":"600887", "backend":"arbitrary"}, {"ticker":"600887", "backend":"claude", "model":"gpt-5.5"}):
        with pytest.raises(InvalidParam): runner.submit("agent_value_analysis", params)
    assert calls == [] and runner.list_jobs() == []


def test_preflight_accepts_global_company_and_does_not_create_task(tmp_path, monkeypatch):
    # REQ-013 T13 + AC-2.1/2.2: metadata in the transport must not block legal input.
    fake=make_fake_cli(tmp_path);monkeypatch.setenv("AGENT_CLI",str(fake))
    config,registry=make_app(tmp_path);make_company_dir(config.output_root)
    result=call_route(registry,"GET","/api/v1/agent/preflight",command="agent_update_analysis",company="600887.SH",ticker="600887")["data"]
    assert result["ready"] and result["ticker"]=="600887.SH"
    assert "gpt-5.5" in [m["id"] for a in result["configuration"] for m in a["models"]]
    with pytest.raises(InvalidParam):
        call_route(registry,"GET","/api/v1/agent/preflight",command="agent_update_analysis",company="600887.SH",ticker="600887",prompt="evil")
    assert list((config.output_root/"600887_伊利").iterdir())==[]


def test_defaults_remain_unknown_and_known_incompatible_default_is_blocked(tmp_path, monkeypatch):
    import agent_models
    monkeypatch.setattr(agent_models.Path,"home",lambda:tmp_path)
    monkeypatch.delenv("ANTHROPIC_MODEL",raising=False);monkeypatch.delenv("CODEX_HOME",raising=False)
    assert agent_models.default_model("codex")["model"] is None
    assert agent_models.default_model("claude")["model"] is None
    (tmp_path/".codex").mkdir(); (tmp_path/".codex/config.toml").write_text('model = "gpt-6.1-sol"')
    with pytest.raises(ValueError,match="默认模型已知不兼容"):
        agent_models.validate_default("codex","default")
    assert agent_models.validate_model("codex","default") is None


def test_report_missing_is_failure_and_only_content_changes_count(tmp_path):
    base=make_company_dir(tmp_path);(base/"qualitative_report.md").write_text("# unchanged")
    completed,_=run_script(tmp_path,"--action","business-analysis","--ticker","600887","--cli",make_fake_cli(tmp_path),"--output-root",tmp_path,"--require-report")
    assert completed.returncode==6 and "报告未产出" in completed.stdout
    audit=json.loads(next((tmp_path/".agent_audit").glob("*.json")).read_text())
    assert audit["exit_code"]==0 and audit["reports"]==[]


def test_actual_cli_model_event_and_codex_record_are_evidence(tmp_path, monkeypatch):
    import sqlite3
    cli=tmp_path/"claude";cli.write_text('#!'+sys.executable+'\nprint(\'{"type":"system","subtype":"init","model":"claude-sonnet-4-6"}\')\n');cli.chmod(0o755)
    make_company_dir(tmp_path)
    completed,_=run_script(tmp_path,"--action","business-analysis","--ticker","600887","--cli",cli,"--output-root",tmp_path,"--backend","claude","--model","sonnet")
    assert completed.returncode==0
    audit=json.loads(next((tmp_path/".agent_audit").glob("*.json")).read_text())
    assert audit["actual_model"]=="claude-sonnet-4-6"
    monkeypatch.setenv("CODEX_HOME",str(tmp_path));db=tmp_path/"state_5.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE threads (id TEXT, model TEXT)")
        conn.execute("INSERT INTO threads VALUES ('own-thread','gpt-5.5')")
    assert agent_action._codex_recorded_model("own-thread")=="gpt-5.5"
    assert agent_action._codex_recorded_model("another-thread") is None
