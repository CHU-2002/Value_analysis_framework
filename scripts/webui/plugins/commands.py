"""按键执行器（REQ-009.1）：既有入口 → 白名单按键 → 异步任务。

**参数表从各脚本的 argparse 源码静态扫描得出**（`scan_cli_params`），不是手抄的第二份清单。
理由是 AC-1.2 要求「表与脚本真实 CLI **双向**一致」：手抄必然漂移，而扫描是纯文本/AST 解析，
不起子进程、不联网、不 import 目标脚本（那些脚本会读环境变量、连数据源）。

白名单本身是**声明**（`ENTRIES`）——加一个按键仍然只改这个插件，不碰内核（AC-9）。
"""

from __future__ import annotations

import ast
import sys
from dataclasses import replace
from pathlib import Path

from ..config import REPO_ROOT
from ..core import envelope
from ..core.errors import BadRequest, WebUIError
from ..core.models import CommandSpec, NavItem, PanelSpec, Param

SCRIPT_DIR = REPO_ROOT / "scripts"

# (id, 标题, 分组, 相对 scripts/ 的脚本或 -m 模块, 子命令, danger, 说明)
ENTRIES: tuple = (
    ("tushare_collector", "取数（Tushare）", "取数", "tushare_collector.py", None, True,
     "拉取行情与财务数据，写入 data_pack_market.md。"),
    ("discover_report", "发现最新定期报告", "取数", "discover_report.py", None, True,
     "在披露源上查找年报/中报/季报链接。"),
    ("download_report", "下载定期报告 PDF", "取数", "download_report.py", None, True,
     "下载并落盘定期报告 PDF（已有期次自动跳过）。"),
    ("pdf_preprocessor", "解析年报 PDF 章节", "分析", "pdf_preprocessor.py", None, False,
     "从 PDF 里切出管理层讨论、治理、重要事项等章节。"),
    ("value_analysis_engine", "价值分析预计算", "分析", "value_analysis_engine.py", None, False,
     "现金流折现等预计算，产出报告与冻结估值。"),
    ("buy_sell_plan", "生成买卖计划", "分析", "buy_sell_plan.py", None, True,
     "触发式计划：采集当时行情并离线生成买卖计划。"),
    ("buy_sell_engine", "离线重放买卖计划", "分析", "buy_sell_engine.py", None, False,
     "用已写入的行情与基准纯离线重放计划。"),
    ("valuation_engine", "通用估值预计算", "分析", "valuation_engine.py", None, False,
     "DCF / DDM / 可比公司 / Graham 等估值子模块。"),
    ("portfolio_engine", "组合策略预计算", "组合", "portfolio_engine.py", None, False,
     "画像 → 宏观 → 配置 → 风险。"),
    ("screener_core", "选股器", "选股", "screener_core.py", None, False,
     "两级选股与结果导出。"),
    ("report_to_html", "报告转 HTML", "报告", "report_to_html.py", None, False,
     "把 Markdown 报告转成 HTML 看板。"),
    ("md_to_mobile_html", "报告转移动端 HTML", "报告", "md_to_mobile_html.py", None, False,
     "把 Markdown 报告转成移动端 HTML。"),
    ("analysis_status", "更新判定", "迭代台账", "analysis_status.py", None, False,
     "判断最新 / 需增量 / 需全量重跑。"),
    ("runs_new", "runs · new", "迭代台账", "runs.py", "new", False, "新建一个不可变 run。"),
    ("runs_resolve", "runs · resolve", "迭代台账", "runs.py", "resolve", False, "打印 run 目录路径。"),
    ("runs_finish", "runs · finish", "迭代台账", "runs.py", "finish", False, "把完成的 run 记入台账。"),
    ("runs_adopt", "runs · adopt", "迭代台账", "runs.py", "adopt", False, "把扁平目录接管为基线 run。"),
    ("runs_export", "runs · export", "迭代台账", "runs.py", "export", False, "导出台账摘要。"),
    ("runs_downstream", "runs · downstream", "迭代台账", "runs.py", "downstream", False,
     "更新下游（估值/买卖计划）新鲜度。"),
    ("results_prepare", "定性管线 · prepare", "定性管线", "results.prepare", None, False,
     "建立证据索引并切出受控材料。"),
    ("results_reconcile", "定性管线 · reconcile", "定性管线", "results.reconcile_results", None,
     False, "对账各模块结果的时间口径与冲突。"),
    ("results_synthesis", "定性管线 · synthesis", "定性管线", "results.synthesis", None, False,
     "生成有界的最终汇总上下文。"),
    ("results_resolve", "定性管线 · resolve", "定性管线", "results.resolve_qualitative", None,
     False, "解析下游消费的定性输入。"),
)

_TYPE_MAP = {"int": "int", "float": "float", "Path": "path", "str": "string"}
# 少量脚本的「条件必填」argparse 静态扫描看不出来，照默认值点下去必然 exit 2：
# `analysis_status.py` 要求 `--company-dir` 或 `--root --all` 二选一（用户实机体验发现
# 「更新判定」按键点了只会打印 usage）。这里只补**界面默认值**，不改脚本 CLI、也不谎报
# required——默认值会显示在表单里，用户随时可改；脚本的真实 CLI 仍是唯一事实来源。
GUI_DEFAULTS = {
    "analysis_status": {"root": "output", "all": True},
}
# 凭据类参数**不进按键表**：token 只从环境变量 / `.env` 读取（REQ-009 约束），
# 让面板接收它就会把明文写进任务历史与日志——那是「回显/落盘 token」的另一条路径。
# 这些 flag 都不是 required，所以 AC-1.2 的「required 必须在表里」不受影响。
_SENSITIVE_PARAM_NAMES = ("token", "secret", "password", "passwd", "api_key", "apikey")


# --------------------------------------------------------------- CLI 静态扫描


def _call_name(node) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _literal(node, constants):
    """尽量把 AST 表达式求成字面量；求不出就返回 None（不猜）。"""
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal(element, constants) for element in node.elts]
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _type_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _module_constants(tree) -> dict:
    constants: dict = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                try:
                    constants[target.id] = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    continue
    return constants


def _parser_names(tree, subcommand) -> list:
    """找出要扫描的 argparse 变量名：子命令 → 它的 subparser；否则 → 所有顶层 parser。"""
    if subcommand:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            if _call_name(call.func) != "add_parser" or not call.args:
                continue
            first = call.args[0]
            if isinstance(first, ast.Constant) and first.value == subcommand:
                target = node.targets[0]
                return [target.id] if isinstance(target, ast.Name) else []
        return []
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if _call_name(node.value.func) == "ArgumentParser":
            target = node.targets[0]
            if isinstance(target, ast.Name):
                names.append(target.id)
    return names


def scan_cli_params(script, subcommand=None) -> list:
    """静态扫描脚本源码，返回它真实 CLI 的 `Param` 列表（AC-1.2 的「表」这一侧）。"""
    path = Path(script)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = _module_constants(tree)
    wanted = set(_parser_names(tree, subcommand))
    if not wanted:
        return []
    collected = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) != "add_argument":
            continue
        base = node.func.value
        if not (isinstance(base, ast.Name) and base.id in wanted):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        if not first.value.startswith("--"):
            continue
        kwargs = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        action = _literal(kwargs.get("action"), constants)
        choices = _literal(kwargs.get("choices"), constants)
        if action in ("store_true", "store_false"):
            kind = "bool"
        elif action == "append":
            kind = "list"
        elif choices:
            kind = "enum"
        else:
            kind = _TYPE_MAP.get(_type_name(kwargs.get("type")), "string")
        default = _literal(kwargs.get("default"), constants)
        if action == "store_true" and default is None:
            default = False
        name = first.value[2:].replace("-", "_")
        if any(marker in name.lower() for marker in _SENSITIVE_PARAM_NAMES):
            if bool(_literal(kwargs.get("required"), constants)):
                raise BadRequest(
                    f"{path.name} 把凭据参数 --{first.value[2:]} 声明为必填："
                    "面板不接收凭据，请改成从环境变量 / .env 读取。"
                )
            continue
        collected.append(
            (
                getattr(node, "lineno", 0),
                Param(
                    name=name,
                    type=kind,
                    required=bool(_literal(kwargs.get("required"), constants)),
                    default=None if isinstance(default, list) else default,
                    choices=tuple(choices or ()),
                    help=str(_literal(kwargs.get("help"), constants) or ""),
                ),
            )
        )
    collected.sort(key=lambda item: item[0])
    seen = set()
    params = []
    for _, param in collected:
        if param.name in seen:
            continue
        seen.add(param.name)
        params.append(param)
    return params


def _argv_for(script: str, subcommand) -> tuple:
    """`results.*` 是包内相对导入，必须走 `-m`；其余脚本按文件路径调用。"""
    if script.startswith("results."):
        base = (sys.executable, "-m", f"scripts.{script}")
    else:
        base = (sys.executable, str(SCRIPT_DIR / script))
    return base + ((subcommand,) if subcommand else ())


def command_specs() -> list:
    specs = []
    for ident, title, group, script, subcommand, danger, description in ENTRIES:
        if script.startswith("results."):
            scan_target = SCRIPT_DIR / "results" / f"{script.split('.', 1)[1]}.py"
        else:
            scan_target = SCRIPT_DIR / script
        params = scan_cli_params(scan_target, subcommand)
        defaults = GUI_DEFAULTS.get(ident, {})
        if defaults:
            params = [replace(param, default=defaults[param.name]) if param.name in defaults else param
                      for param in params]
        specs.append(
            CommandSpec(
                id=ident,
                argv=_argv_for(script, subcommand),
                title=title,
                group=group,
                params=tuple(params),
                danger=danger,
                description=description,
            )
        )
    return specs


# --------------------------------------------------------------- 面板与路由


def _runner(ctx):
    runner = getattr(ctx.registry, "jobs", None)
    if runner is None:
        raise WebUIError(
            "任务运行器未装配",
            hint="服务启动时会装配它；直接调用 handler 的测试需要先挂 registry.jobs。",
        )
    return runner


def _catalog_panel(ctx, **_):
    return {"commands": [spec.to_json() for spec in ctx.registry.commands()]}


def _jobs_panel(ctx, **_):
    return {"jobs": _runner(ctx).list_jobs()}


def _list_commands(ctx, **_):
    specs = [spec.to_json() for spec in ctx.registry.commands()]
    return envelope.ok({"count": len(specs), "commands": specs})


def _list_jobs(ctx, **_):
    jobs = _runner(ctx).list_jobs()
    return envelope.ok({"count": len(jobs), "jobs": jobs})


def _submit_job(ctx, **_):
    payload = envelope.json_body(ctx)
    command = payload.get("command")
    if not command or not isinstance(command, str):
        raise BadRequest("缺少 command 字段", hint='形如 {"command": "runs_export", "params": {}}')
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        raise BadRequest("params 必须是对象", hint="参数按名称放在 params 对象里。")
    return envelope.ok(_runner(ctx).submit(command, params))


def _get_job(ctx, job_id, **_):
    return envelope.ok(_runner(ctx).get(job_id))


def _cancel_job(ctx, job_id, **_):
    return envelope.ok(_runner(ctx).cancel(job_id))


def contribute(registry):
    for spec in command_specs():
        registry.command(spec)
    registry.panel(PanelSpec(
        id="commands.catalog", kind="form", title="按键", render="client",
        endpoint="/api/v1/commands", provider=_catalog_panel, size="full",
        description="白名单命令 + 结构化参数；参数表由各脚本 argparse 源码扫描得出。",
    ))
    registry.panel(PanelSpec(
        id="commands.jobs", kind="jobs", title="任务与日志", render="client",
        endpoint="/api/v1/jobs", provider=_jobs_panel, size="full",
        description="任务状态、退出码与日志尾部；历史任务在服务重启后仍可查看。",
    ))
    registry.nav(NavItem(id="commands", title="按键", group="执行", order=50,
                         panels=("commands.catalog", "commands.jobs")))
    registry.route("GET", "/api/v1/commands", _list_commands, name="command catalog")
    registry.route("GET", "/api/v1/jobs", _list_jobs, name="job list")
    registry.route("POST", "/api/v1/jobs", _submit_job, name="job submit")
    registry.route("GET", "/api/v1/jobs/{job_id}", _get_job, name="job detail")
    registry.route("POST", "/api/v1/jobs/{job_id}/cancel", _cancel_job, name="job cancel")


__all__ = ["contribute", "command_specs", "scan_cli_params", "ENTRIES"]
