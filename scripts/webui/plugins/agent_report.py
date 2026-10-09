"""Fixed report commands plus read-only preflight and fresh artifact links.

The #77 minimal page used only existing registration points. REQ-013.3 adds
optional form metadata and command validation/exclusion declarations; the
registry and router still contain no report-specific behavior.
"""

from __future__ import annotations

import sys
import os
import shlex
from datetime import datetime
from functools import partial
from pathlib import Path
from urllib.parse import urlencode

try:
    from ... import agent_action
except ImportError:
    import agent_action

from ..config import REPO_ROOT
from ..core import envelope
from ..core.models import CommandSpec, NavItem, PanelSpec
from ..core.errors import InvalidParam, UnknownCommand, WebUIError
from ..core.security import redact, safe_join
from .companies import _artifact_id
from .commands import _runner
from .commands import scan_cli_params

#: 包装脚本（REQ-013.1）；参数表与命令模板都以它为准。
SCRIPT = REPO_ROOT / "scripts" / "agent_action.py"

ENDPOINT = "/api/v1/agent/actions"

#: (按键 id, `--action` 的取值, 面向用户的标题, 说明)。
#: 说明里明确写「会消耗模型额度」——这是 REQ-013 的 `AC-4` 要求的告知。
ACTIONS = (
    (
        "agent_update_analysis",
        "update-analysis",
        "更新分析报告",
        "按最新财报期做一次增量更新，产出这家公司新的分析报告与变化报告。会消耗模型额度、可能耗时较长。",
    ),
    (
        "agent_business_analysis",
        "business-analysis",
        "首次全量分析",
        "对这家公司做一次完整的首次分析，产出分析报告并建立迭代台账的第一个 run。会消耗模型额度、可能耗时较长。",
    ),
    (
        "agent_value_analysis",
        "value-analysis",
        "价值分析报告",
        "重算这家公司的价值分析，产出最新的价值分析报告。会消耗模型额度、可能耗时较长。",
    ),
)

GROUP = "生成报告"

#: 表单上真正给用户填的字段（其余 flag 由脚本默认值决定，不进界面）。
USER_FACING_PARAMS = ("ticker",)


def scanned_params() -> dict:
    """扫 `agent_action.py` 的 argparse，返回 `{参数名: Param}`（单次扫描）。

    扫描而不是手抄：脚本改了参数表，界面自动跟着变（与 `commands.py` 的按键表同一手法）。
    """
    return {param.name: param for param in scan_cli_params(SCRIPT)}


def display_params() -> tuple:
    """表单/按键暴露的参数：扫描结果 ∩ `USER_FACING_PARAMS`（保持脚本里的声明）。"""
    scanned = scanned_params()
    return tuple(scanned[name] for name in USER_FACING_PARAMS if name in scanned)


def command_specs(config=None) -> list:
    """3 个 `CommandSpec`：每个把 `--action <值>` 固定进 argv，参数表来自扫描。"""
    params = display_params()
    root = Path(config.output_root) if config else REPO_ROOT / "output"
    cli_value = os.environ.get("AGENT_CLI") or agent_action.DEFAULT_CLI
    return [
        CommandSpec(
            id=command_id,
            argv=(sys.executable, str(SCRIPT), "--action", action,
                  "--output-root", str(root.resolve()), "--cli", cli_value),
            title=title,
            group=GROUP,
            params=params,
            danger=True,          # 复用既有的「确认执行」语义（AC-4）
            description=description,
            validate=partial(_validate, action=action, root=root, cli=cli_value),
            exclusive=True,
            exclusive_group="agent-report",
            exclusive_key=partial(_exclusive_key, root=root),
            outputs={"artifacts_endpoint": "/api/v1/agent/jobs/{job_id}/artifacts"},
        )
        for command_id, action, title, description in ACTIONS
    ]


def _validate(params, *, action, root, cli):
    try:
        ticker, _, _ = agent_action.preflight(action, params.get("ticker", ""), root, cli)
    except (ValueError, FileNotFoundError) as exc:
        raise InvalidParam(str(exc)) from exc
    return {"ticker": ticker}


def _exclusive_key(params, *, root):
    directories = agent_action.find_company_dirs(root, params["ticker"])
    if len(directories) != 1:
        raise InvalidParam("公司目录状态已变化，请刷新后重新提交。")
    return str(safe_join(root, directories[0].name))


def _preflight(ctx, **_):
    command = ctx.query.get("command")
    action = next((a for cid, a, _, _ in ACTIONS if cid == command), None)
    if action is None:
        raise UnknownCommand("没有这个报告动作。")
    unknown = set(ctx.query) - {"command", "ticker"}
    if unknown:
        raise InvalidParam("报告动作只接受股票代码。")
    result = {"ready": False, "command_line": "", "reason": "",
              "notice": "会消耗模型额度，可能需要较长时间。"}
    try:
        ticker, _, argv = agent_action.preflight(
            action, ctx.query.get("ticker", ""), ctx.config.output_root,
        )
        result.update(ready=True, ticker=ticker,
                      command_line=redact(shlex.join(argv), ctx.secrets))
    except FileNotFoundError:
        result["reason"] = "本机找不到可执行的 Codex（或配置的 agent CLI）。请先在终端安装并登录，再刷新页面。"
    except ValueError as exc:
        result["reason"] = str(exc)
    return envelope.ok(result)


def _artifacts(ctx, job_id, **_):
    job = _runner(ctx).get(job_id)
    if job["command"] not in {cid for cid, _, _, _ in ACTIONS}:
        raise InvalidParam("这个任务不是生成报告任务。")
    root = Path(ctx.config.output_root)
    dirs = agent_action.find_company_dirs(root, job["params"].get("ticker", ""))
    links = []
    execution_started_at = job.get("outputs", {}).get("execution_started_at")
    if (len(dirs) == 1 and execution_started_at
            and job["status"] not in ("running", "queued")):
        base = safe_join(root, dirs[0].name)
        start = datetime.fromisoformat(execution_started_at.replace("Z", "+00:00")).timestamp()
        end = datetime.fromisoformat(job["finished_at"].replace("Z", "+00:00")).timestamp()
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(base).as_posix()
            try:
                safe_join(root, dirs[0].name, relative)
                stamp = path.stat().st_mtime
            except (OSError, ValueError, WebUIError):
                continue
            if not start <= stamp <= end:
                continue
            company = dirs[0].name
            if path.suffix == ".md" and ("report" in path.name or "报告" in path.name):
                links.append({"label": path.name, "href": "#report?" + urlencode(
                    {"company": company, "id": _artifact_id(relative)}), "path": relative})
            elif path.name == "run.json" and path.parent.parent.name == "runs":
                links.append({"label": "本次迭代目录：" + path.parent.name,
                              "href": "#runs?" + urlencode({"company": company,
                                                           "run": path.parent.name}),
                              "path": str(path.parent.relative_to(base))})
    return envelope.ok({"links": links, "message": "" if links else "本次没有找到新产物"})


def _catalog_payload(ctx) -> dict:
    specs = command_specs(ctx.config)
    return {"count": len(specs), "commands": [{**spec.to_json(), "preflight_endpoint": "/api/v1/agent/preflight",
                       "artifacts_endpoint": "/api/v1/agent/jobs/{job_id}/artifacts",
                       "field_labels": {"ticker": "股票代码"},
                       "placeholder": "600887.SH / 00700.HK / AAPL"}
                      for spec in specs]}


def _list_actions(ctx, **_):
    """专用目录接口：**只含**这 3 个按键（不把既有的 23 个按键都列出来）。"""
    return envelope.ok(_catalog_payload(ctx))


def _catalog_panel(ctx, **_):
    """面板 provider：与上面那条路由同一份数据（服务端渲染与测试用）。"""
    return {"commands": _catalog_payload(ctx)["commands"]}


def contribute(registry):
    for spec in command_specs(getattr(registry, "config", None)):
        registry.command(spec)
    registry.route("GET", ENDPOINT, _list_actions, name="agent action catalog")
    registry.route("GET", "/api/v1/agent/preflight", _preflight, name="agent preflight")
    registry.route("GET", "/api/v1/agent/jobs/{job_id}/artifacts", _artifacts, name="agent artifacts")
    registry.panel(PanelSpec(
        id="agent.actions",
        kind="form",
        title="生成报告",
        render="client",
        endpoint=ENDPOINT,
        provider=_catalog_panel,
        size="full",
        description=(
            "选一个动作、填股票代码，确认后由本机的 agent CLI 生成报告；"
            "会消耗模型额度、可能耗时较长。"
        ),
    ))
    registry.nav(NavItem(
        id="agent",
        title="生成报告",
        group="分析",
        order=60,
        panels=("agent.actions",),
        description="一键让 agent CLI 跑分析并产出报告（消耗模型额度）。",
    ))


__all__ = ["contribute", "command_specs", "scanned_params", "display_params", "ACTIONS", "ENDPOINT"]
