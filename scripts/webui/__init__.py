"""本地图形化控制台（REQ-009）。

内核只做传输、路由、信封、安全与调度；**一切具体功能都是插件**（`plugins/`），
所以「加功能不改核心」（AC-9）。

- 需求（要什么、做到什么程度）：`docs/requirements/REQ-009-local-gui-console.md`
- 设计与扩展点清单（怎么做）：`docs/GUI_CONSOLE_PLAN.md`
- 给人读的导读：`docs/GUI_CONSOLE_OVERVIEW.md`

零新增第三方依赖：只用 Python 标准库与仓库既有依赖。
"""

__version__ = "0.1.0"
# API 版本进路径（/api/v1/...）：将来破坏性变更走 /api/v2 并存，不偷偷改语义。
API_VERSION = "v1"
# 面板/页面描述的 schema 版本：前端据此判断能不能渲染，而不是静默错渲染。
SCHEMA_VERSION = "1.0"
