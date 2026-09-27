"""一键出报告（REQ-013.2）：把 `REQ-013.1` 的动作包成 3 个**既有**「按键」。

三个刻意保持最小的设计：

1. **复用既有注册点**：只用 `registry.command` / `route` / `panel` / `nav` 四类，
   加上 `commands.py` 已经有的 `scan_cli_params`。**不新增注册点、不改 `core/*`、
   不改 `static/*`**——所以前端的 `form`（选按键 + 填参数 + 确认 + 跟日志）与 `jobs`
   两个渲染器原样复用（AC-2.5）。
2. **参数表从脚本源码扫出来**：`--ticker` 的 `required` 与 help 永远和
   `scripts/agent_action.py` 的真实 CLI 一致，不手抄第二份。`--action` 被**固定进 argv**
   （一个动作一个按键），所以不出现在表单里——这正是「不自由文本」的界面体现。
3. **表单只暴露一个字段**：`USER_FACING_PARAMS` 之外的东西（`--cli` / `--timeout` /
   `--output-root` / `--dry-run`）是命令行细节，不进界面（AC-2.1「表单只需一个标的字段」、
   AC-2.3「不得出现文件系统路径 / 内部参数名 / CLI 开关」）。
"""

from __future__ import annotations

import sys

from ..config import REPO_ROOT
from ..core import envelope
from ..core.models import CommandSpec, NavItem, PanelSpec
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


def command_specs() -> list:
    """3 个 `CommandSpec`：每个把 `--action <值>` 固定进 argv，参数表来自扫描。"""
    params = display_params()
    return [
        CommandSpec(
            id=command_id,
            argv=(sys.executable, str(SCRIPT), "--action", action),
            title=title,
            group=GROUP,
            params=params,
            danger=True,          # 复用既有的「确认执行」语义（AC-4）
            description=description,
        )
        for command_id, action, title, description in ACTIONS
    ]


def _catalog_payload() -> dict:
    specs = command_specs()
    return {"count": len(specs), "commands": [spec.to_json() for spec in specs]}


def _list_actions(ctx, **_):
    """专用目录接口：**只含**这 3 个按键（不把既有的 23 个按键都列出来）。"""
    return envelope.ok(_catalog_payload())


def _catalog_panel(ctx, **_):
    """面板 provider：与上面那条路由同一份数据（服务端渲染与测试用）。"""
    return {"commands": _catalog_payload()["commands"]}


def contribute(registry):
    for spec in command_specs():
        registry.command(spec)
    registry.route("GET", ENDPOINT, _list_actions, name="agent action catalog")
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
