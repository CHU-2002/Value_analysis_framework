"""六类注册点 + 冲突检测（AC-3.2）。这是「加功能不改核心」的落点（AC-9）。

内核只认这些注册点，**不认任何具体功能**：`core/` 里不允许出现 `/api/companies` 这类业务路由，
功能一律由插件在 `contribute(registry)` 里声明。

六类注册点：

| 注册点 | 声明什么 |
|--------|----------|
| `nav(NavItem)` | 左侧导航的一项（同时是一个页面） |
| `panel(PanelSpec)` | 页面上的一块（表格/图表/时间线/指标卡…） |
| `route(method, path, handler)` | 自定义接口（返回统一信封） |
| `dataset(DatasetSpec)` | 数据源：哪些文件、哪个解析器、怎么算指纹 |
| `command(CommandSpec)` | 白名单命令 + 结构化参数 |
| `job_type(kind, runner)` | 命令以外的任务类型（批次采集等） |

同一 id 重复注册**直接抛错并指明两个来源**，不静默覆盖——静默覆盖会让「谁把我的面板顶掉了」
变成排查不出来的问题。
"""

from __future__ import annotations

from contextlib import contextmanager

from . import models
from .errors import BadRequest, NotFound
from .router import Route


class Registry:
    def __init__(self):
        self._nav: dict = {}
        self._panels: dict = {}
        self._datasets: dict = {}
        self._commands: dict = {}
        self._job_types: dict = {}
        self._routes: list = []
        self._origins: dict = {}
        self.origin = "core"
        # 装配时由 `build_application` 填上（供进程内直接调用 handler 的场景使用；
        # handler 本人拿的是 `ctx.config`）。
        self.config = None

    # ---------------------------------------------------------------- 来源标记

    @contextmanager
    def contribution_from(self, origin: str):
        """标记接下来的注册来自哪个插件（用于冲突报错与诊断）。"""
        previous = self.origin
        self.origin = origin
        try:
            yield self
        finally:
            self.origin = previous

    def _claim(self, kind: str, key: str) -> None:
        marker = (kind, key)
        previous = self._origins.get(marker)
        if previous is not None:
            raise ValueError(
                f"{kind} {key!r} 重复注册：已由 {previous!r} 注册，{self.origin!r} 又想注册同一个 id。"
                "请改 id，或删掉其中一处。"
            )
        self._origins[marker] = self.origin

    # ---------------------------------------------------------------- 注册

    def nav(self, item: models.NavItem) -> models.NavItem:
        self._claim("nav", item.id)
        self._nav[item.id] = item
        return item

    def panel(self, spec: models.PanelSpec) -> models.PanelSpec:
        self._claim("panel", spec.id)
        self._panels[spec.id] = spec
        return spec

    def dataset(self, spec: models.DatasetSpec) -> models.DatasetSpec:
        self._claim("dataset", spec.name)
        self._datasets[spec.name] = spec
        return spec

    def command(self, spec: models.CommandSpec) -> models.CommandSpec:
        self._claim("command", spec.id)
        self._commands[spec.id] = spec
        return spec

    def job_type(self, kind: str, runner) -> None:
        self._claim("job_type", kind)
        self._job_types[kind] = runner

    def route(self, method: str, path: str, handler, *, name: str = "") -> Route:
        route = Route(method, path, handler, name=name or path)
        self._claim("route", f"{route.method} {path}")
        self._routes.append(route)
        return route

    # ---------------------------------------------------------------- 查询

    def nav_items(self) -> list:
        return sorted(self._nav.values(), key=lambda item: (item.order, item.id))

    def panel_spec(self, panel_id: str) -> models.PanelSpec:
        spec = self._panels.get(panel_id)
        if spec is None:
            raise NotFound(f"没有注册过面板 {panel_id!r}", hint="检查插件是否已加载。")
        return spec

    def has_panel(self, panel_id: str) -> bool:
        return panel_id in self._panels

    def dataset_spec(self, name: str) -> models.DatasetSpec:
        spec = self._datasets.get(name)
        if spec is None:
            raise NotFound(f"没有注册过数据集 {name!r}")
        return spec

    def command_spec(self, command_id: str) -> models.CommandSpec:
        spec = self._commands.get(command_id)
        if spec is None:
            raise NotFound(f"没有注册过按键 {command_id!r}")
        return spec

    def commands(self) -> list:
        return sorted(self._commands.values(), key=lambda spec: (spec.group, spec.id))

    def datasets(self) -> list:
        return [self._datasets[name].to_json() for name in sorted(self._datasets)]

    def page(self, page_id: str) -> models.NavItem:
        item = self._nav.get(page_id)
        if item is None:
            raise NotFound(f"没有这一页：{page_id!r}", hint="导航列表见 /api/v1/nav。")
        return item

    def page_payload(self, page_id: str) -> dict:
        """页面描述——**纯数据**，前端按 kind 渲染（AC-3.3）。"""
        item = self.page(page_id)
        panels = []
        for panel_id in item.panels:
            panels.append(self.panel_spec(panel_id).to_json())
        return {
            "schema": "webui.page",
            "schema_version": models_and_schema(),
            "id": item.id,
            "title": item.title,
            "group": item.group,
            "description": item.description,
            "panels": panels,
        }

    def routes(self) -> tuple:
        return tuple(self._routes)

    def job_types(self) -> tuple:
        return tuple(sorted(self._job_types))

    def origins(self) -> dict:
        return dict(self._origins)


def models_and_schema() -> str:
    """页面/面板描述的 schema 版本（从包根取，避免各处写字面量）。"""
    from .. import SCHEMA_VERSION

    return SCHEMA_VERSION


def build_registry() -> Registry:
    """空注册表。插件加载由 `webui.plugins.load_plugins` 负责。"""
    return Registry()


__all__ = ["Registry", "build_registry", "BadRequest"]
