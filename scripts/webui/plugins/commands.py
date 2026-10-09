"""按键执行器（REQ-009.1）：既有入口 → 白名单按键 → 异步任务。

**参数表从各脚本的 argparse 源码静态扫描得出**（`scan_cli_params`），不是手抄的第二份清单。
理由是 AC-1.2 要求「表与脚本真实 CLI **双向**一致」：手抄必然漂移，而扫描是纯文本/AST 解析，
不起子进程、不联网、不 import 目标脚本（那些脚本会读环境变量、连数据源）。

白名单本身是**声明**（`ENTRIES`）——加一个按键仍然只改这个插件，不碰内核（AC-9）。
"""

from __future__ import annotations

import ast
import sys
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
    # REQ-012.4：数据页的两个动作要编排数据层自己的入口（同一个白名单机制，不新开通道）。
    ("datalayer_pull", "数据层 · 拉取（联网）", "数据", "datalayer/cli.py", "pull", True,
     "按自选股清单拉取原始数据；只补缺口是它的一个开关。"),
    ("datalayer_rebuild", "数据层 · 离线重建", "数据", "datalayer/cli.py", "rebuild", False,
     "从原始仓离线重建数据包，不联网、不花配额。"),
)

_TYPE_MAP = {"int": "int", "float": "float", "Path": "path", "str": "string"}
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


def _common_parser_names(tree) -> list:
    """`add_parser(..., parents=[common])` 里的**公共** parser 名。

    `scripts/datalayer/cli.py` 把 `--store` / `--json` 放在公共父 parser 上，
    子命令自己只声明业务开关。只扫子 parser 会让「表 → 脚本」方向的核对漏掉它们，
    而这些开关（尤其 `--store`）恰恰是必须能传的。
    """
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) != "add_parser":
            continue
        for keyword in node.keywords:
            if keyword.arg != "parents" or not isinstance(keyword.value, ast.List):
                continue
            for element in keyword.value.elts:
                if isinstance(element, ast.Name):
                    names.append(element.id)
    return names


def _parser_names(tree, subcommand) -> list:
    """找出要扫描的 argparse 变量名：子命令 → 它的 subparser（含公共父 parser）；
    否则 → 所有顶层 parser。"""
    if subcommand:
        names = _common_parser_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            if _call_name(call.func) != "add_parser" or not call.args:
                continue
            first = call.args[0]
            if isinstance(first, ast.Constant) and first.value == subcommand:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id not in names:
                    names.append(target.id)
                break
        return names
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


def _scan_target(script: str) -> Path:
    """`ENTRIES` 里的脚本名 → 真实源码路径。

    `results.prepare` 是包路径（`scripts/results/prepare.py`），而 `tushare_collector.py`
    这样的文件名*本身*带点号——所以判据不是「有没有点号」，而是「按包路径找不找得到」。
    猜错的代价是扫描器直接抛 `FileNotFoundError`，整个按键表装配不起来。
    """
    direct = SCRIPT_DIR / script
    if direct.is_file():
        return direct
    candidate = SCRIPT_DIR.joinpath(*script.split(".")).with_suffix(".py")
    if candidate.is_file():
        return candidate
    return direct


def _argv_for(script: str, subcommand) -> tuple:
    """带点号的脚本名是**模块**（包内相对导入），必须走 `-m`；其余按文件路径调用。

    `results.prepare` → `python -m scripts.results.prepare`；
    `datalayer.cli` → `python -m scripts.datalayer.cli`（它是 `-m scripts.datalayer` 的实现，
    命令行开关完全一样，走同一份 argparse）。
    """
    # 三种形态，判据都是「哪种调用方式真的能跑」：
    #   `results.prepare`   → `python -m scripts.results.prepare`（包内相对导入）
    #   `datalayer/cli.py`  → `python -m scripts.datalayer`（目录里有 __main__）
    #   `tushare_collector.py` → 直接按文件路径调用
    if "/" in script:
        # `datalayer/cli.py` → `python -m scripts.datalayer`：表格里写的是**源码路径**
        # （`ENTRIES` → 脚本的自检靠它），但要用 `-m 包名` 调用——`cli.py` 内部是**相对导入**
        # （`from . import __version__`），裸文件路径执行会 `ImportError: attempted relative
        # import with no known parent package`（实测踩到：动作在界面上失败、CLI 手跑却正常）。
        # `scripts/datalayer/__main__.py` 转调的就是 `cli.main`，两条路等价。
        package = script[:-3] if script.endswith(".py") else script
        package = package.rsplit("/", 1)[0].replace("/", ".")
        base = (sys.executable, "-m", f"scripts.{package}")
    elif (SCRIPT_DIR / script).is_file():
        base = (sys.executable, str(SCRIPT_DIR / script))
    else:
        base = (sys.executable, "-m", f"scripts.{script}")
    subs = subcommand if isinstance(subcommand, (list, tuple)) else ((subcommand,) if subcommand else ())
    return base + tuple(subs)


def command_specs() -> list:
    specs = []
    for ident, title, group, script, subcommand, danger, description in ENTRIES:
        scan_target = _scan_target(script)
        specs.append(
            CommandSpec(
                id=ident,
                argv=_argv_for(script, subcommand),
                title=title,
                group=group,
                params=tuple(scan_cli_params(scan_target, subcommand)),
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


def _actions_panel(ctx, **_):
    """`kind=actions` 面板的进程内取数（与 `GET /api/v1/actions` 同一份实现）。"""
    return list_actions(ctx)


def _jobs_panel(ctx, **_):
    runner = _runner(ctx)
    return {"jobs": runner.list_jobs(), "queue": runner.queue()}


def _list_commands(ctx, **_):
    specs = [spec.to_json() for spec in ctx.registry.commands()]
    return envelope.ok({"count": len(specs), "commands": specs})


def _list_jobs(ctx, **_):
    runner = _runner(ctx)
    jobs = runner.list_jobs()
    return envelope.ok({"count": len(jobs), "jobs": jobs, "queue": runner.queue()})


def _action_entry(ctx, spec, *, extra_context=None) -> dict:
    """一个动作的**可用性快照**（`REQ-012.2` 的 `AC-4`）。

    预检全部在服务端算（前端只负责禁用与展示），所以「缺什么、为什么不能跑」是
    **可被 CI 断言**的字符串，而不是渲染出来才知道的东西。
    """
    selection = _selection(ctx, extra_context)
    blockers: list = []
    missing = [key for key in spec.requires if not _context_value(ctx, key, selection)]
    if "selection.company" in missing:
        blockers.append("还没选公司：用页面右上角的「当前公司」选一家，或从工作台的公司列表点进去。")
    if spec.preflight is not None:
        try:
            extra = spec.preflight(ctx, selection) or {}
        except WebUIError as exc:
            extra = {"blockers": [f"{exc.message}（{exc.hint}）" if exc.hint else exc.message]}
        blockers.extend(str(item) for item in (extra.get("blockers") or []))
    payload = spec.to_json()
    payload.update({
        "enabled": not blockers,
        "blockers": blockers,
        "handler": f"/api/v1/actions/{spec.id}/run",
        # 预估（`AC-4` 的「预计耗时/调用量」）：同样是**服务端**算。
        # 算不出来（仓不可用、清单为空…）就不给这个键——前端据此不显示预估，
        # 而不是显示一个编出来的数字（预估宁可没有，也不能骗人）。
        "estimate": _estimate_for(spec, ctx, selection, blockers),
    })
    return payload


def _estimate_for(spec, ctx, selection, blockers) -> dict:
    """动作的预估值；**失败不影响动作本身**（预估是锦上添花，不是前置条件）。"""
    if spec.estimate is None or blockers:
        return {}
    try:
        payload = spec.estimate(ctx, selection) or {}
    except WebUIError:
        return {}
    except Exception:  # noqa: BLE001（预估失败只丢预估，不该让动作清单整块失败）
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _context_value(ctx, key: str, selection: dict | None = None):
    """上下文需求的取值：`selection.company` → 选择器给的 `company`。

    取值来源有两处，都算数：**查询串**（页面上的当前公司）与**请求体里的 context**
    （动作提交时前端显式带上）。只看查询串会让「请求体带了上下文但没有 URL 参数」的
    合法提交被误判成禁用。
    """
    name = key.split(".", 1)[-1]
    return (selection or {}).get(name) or ctx.query.get(name) or ""


def _selection(ctx, extra: dict | None = None) -> dict:
    """当前选择 + 动作上下文（`company` → 绝对路径与规范标识由插件解析，见 actions 插件）。"""
    values = {name: value for name, value in ctx.query.items() if value}
    for name, value in (extra or {}).items():
        if value not in (None, ""):
            values[name] = value
    try:
        from .actions import action_context

        return action_context(ctx, values)
    except Exception:  # noqa: BLE001（actions 插件没装配时退回原始选择）
        return values


def list_actions(ctx, extra_context=None) -> dict:
    """动作可用性快照（`GET /api/v1/actions` 与面板共用这一份实现）。"""
    specs = ctx.registry.job_type_specs()
    actions = [_action_entry(ctx, spec, extra_context=extra_context) for spec in specs]
    return {
        "count": len(actions),
        "actions": actions,
        "groups": sorted({action["group"] for action in actions if action["group"]}),
        "empty_hint": "还没有注册任何动作。" if not actions else "",
    }


def _list_actions(ctx, **_):
    return envelope.ok(list_actions(ctx))


def _run_action(ctx, action_id, **_):
    """提交一个动作。参数与上下文由服务端解析（`AC-2.1`：用户不手敲内部参数）。"""
    payload = envelope.json_body(ctx)
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        raise BadRequest("params 必须是对象", hint="参数按名称放在 params 对象里。")
    context = payload.get("context")
    if not isinstance(context, dict):
        context = {}
    # **解析后的**上下文才拿去提交：前端只传「哪家公司」（`company`），
    # `company_dir` / `ticker` / `primary_period` 这些编排要用的值由服务端解析出来
    # （`AC-2.1`：用户不手敲路径）。少这一步时动作会在起进程前报
    # 「需要一个还没解析出来的值 {ticker}」——实测踩到。
    resolved = _selection(ctx, context)
    runner = _runner(ctx)
    spec = ctx.registry.job_type_spec(action_id)
    entry = _action_entry(ctx, spec, extra_context=context)
    if not entry["enabled"]:
        # 服务端**再判一次**：前端的禁用只是展示，不能当成约束（预检不通过就不起进程）。
        raise BadRequest(
            "这个动作现在还不能执行",
            hint="；".join(entry["blockers"]) or "缺前置条件。",
        )
    return envelope.ok(runner.submit_action(action_id, params, context=resolved))


def _get_job(ctx, job_id, **_):
    return envelope.ok(_runner(ctx).get(job_id))


def _submit_job(ctx, **_):
    payload = envelope.json_body(ctx)
    command = payload.get("command")
    if not command or not isinstance(command, str):
        raise BadRequest("缺少 command 字段", hint='形如 {"command": "runs_export", "params": {}}')
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        raise BadRequest("params 必须是对象", hint="参数按名称放在 params 对象里。")
    return envelope.ok(_runner(ctx).submit(command, params))


def _cancel_job(ctx, job_id, **_):
    return envelope.ok(_runner(ctx).cancel(job_id))


def _retry_job(ctx, job_id, **_):
    return envelope.ok(_runner(ctx).retry(job_id))


def _continue_job(ctx, job_id, **_):
    return envelope.ok(_runner(ctx).continue_action(job_id))


def _abandon_job(ctx, job_id, **_):
    return envelope.ok(_runner(ctx).abandon_action(job_id))


def contribute(registry):
    for spec in command_specs():
        registry.command(spec)
    registry.panel(PanelSpec(
        id="commands.actions", kind="actions", title="可以做的事", render="client",
        endpoint="/api/v1/actions", provider=_actions_panel, size="full",
        description="意图化动作：参数由当前上下文预填，执行前会说明会发生什么。",
    ))
    registry.panel(PanelSpec(
        id="commands.jobs", kind="jobs", title="任务与日志", render="client",
        endpoint="/api/v1/jobs", provider=_jobs_panel, size="full",
        description="队列 / 进行中 / 历史：进度、取消、重试、失败原因与原始日志。",
    ))
    registry.panel(PanelSpec(
        id="commands.catalog", kind="form", title="高级：直接运行既有命令", render="client",
        endpoint="/api/v1/commands", provider=_catalog_panel, size="full",
        description="技术视图：白名单命令 + 结构化参数（参数表由各脚本 argparse 源码扫描得出）。",
    ))
    # `REQ-012.1` 的 IA：侧栏按「用户的事」分组（工作台 / 数据 / 公司 / 任务），
    # 「按键」页改名「任务」并**退到技术视图**（动作入口在对象上，任务页只管队列与历史）。
    registry.nav(NavItem(id="commands", title="任务", group="任务", order=50,
                         panels=("commands.actions", "commands.jobs", "commands.catalog"),
                         description="队列与历史任务；「可以做的事」按当前公司列出可执行的动作。"))
    registry.route("GET", "/api/v1/commands", _list_commands, name="command catalog")
    registry.route("GET", "/api/v1/jobs", _list_jobs, name="job list")
    registry.route("POST", "/api/v1/jobs", _submit_job, name="job submit")
    registry.route("GET", "/api/v1/jobs/{job_id}", _get_job, name="job detail")
    registry.route("POST", "/api/v1/jobs/{job_id}/cancel", _cancel_job, name="job cancel")
    registry.route("POST", "/api/v1/jobs/{job_id}/retry", _retry_job, name="job retry")
    registry.route("POST", "/api/v1/jobs/{job_id}/continue", _continue_job, name="job continue")
    registry.route("POST", "/api/v1/jobs/{job_id}/abandon", _abandon_job, name="job abandon")
    # 动作层（REQ-012.2）：注册点在**第 6 类**（job_type），这里是它的消费者。
    registry.route("GET", "/api/v1/actions", _list_actions, name="action list")
    registry.route("POST", "/api/v1/actions/{action_id}/run", _run_action, name="action run")


__all__ = ["contribute", "command_specs", "scan_cli_params", "ENTRIES"]
