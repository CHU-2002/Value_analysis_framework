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
| `job_type(kind, runner)` | 命令以外的任务类型（批次采集等）；富化形态见 `JobTypeSpec`（动作） |

同一 id 重复注册**直接抛错并指明两个来源**，不静默覆盖——静默覆盖会让「谁把我的面板顶掉了」
变成排查不出来的问题。
"""

from __future__ import annotations

from contextlib import contextmanager

from . import models
from .errors import BadRequest, NotFound, RegistrationConflict
from .jobs import CHAIN_RUNNER
from .router import Route


class Registry:
    def __init__(self):
        self._nav: dict = {}
        self._panels: dict = {}
        self._datasets: dict = {}
        self._commands: dict = {}
        self._job_types: dict = {}
        self._job_specs: dict = {}
        self._routes: list = []
        self._origins: dict = {}
        #: 子导航项的 id（`nav_items()` 只回根项，避免层级被渲染两遍）。
        self._child_ids: set = set()
        self.origin = "core"
        # 装配时由 `build_application` 填上（供进程内直接调用 handler 的场景使用；
        # handler 本人拿的是 `ctx.config`）。
        self.config = None
        # 数据层（AC-3.4）：面板的 provider 通过 `ctx.registry.datastore` 取派生数据，
        # 这样内核的路由与面板协议**不需要知道数据层存在**。
        self.datastore = None

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
            raise RegistrationConflict(
                f"{kind} {key!r} 重复注册：已由 {previous!r} 注册，{self.origin!r} 又想注册同一个 id。"
                "请改 id，或删掉其中一处。",
                from_registry=True,
            )
        self._origins[marker] = self.origin

    # ---------------------------------------------------------------- 注册

    def nav(self, item: models.NavItem) -> models.NavItem:
        """注册一个导航项（它同时是一个**页面**）。

        `REQ-012.1` 加了子层级：`children` 里的每一项**也都是页面**，所以这里递归登记到
        `_nav`（否则子项只出现在侧栏里、`/api/v1/pages/{id}` 却打不开——「导航项即页面」
        这条既有语义会被层级破坏）。父项自身仍然按 id 冲突检测；子项的冲突在递归里报。

        `_child_ids` 记住「谁是谁的子项」：`nav_items()` 据此**只回根项**。
        不记的话子项会既出现在父项的 `children` 里、又作为顶层项出现一次，侧栏就渲染两条
        （门② 第四轮用仓库外插件注册了一个子页，真实侧栏确实出现两条同名链接）。
        """
        self._claim("nav", item.id)
        self._nav[item.id] = item
        for child in item.children:
            self.nav(child)
            self._child_ids.add(child.id)
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

    def job_type(self, kind_or_spec, runner=None) -> None:
        """第 6 类注册点：命令以外的任务类型 / 动作。

        两种形态，**同一个注册点**（`REQ-012.2`，见 `docs/CONSOLE_V2_PLAN.md` §4.4）：

        - `job_type("batch", runner)` —— `REQ-009.3` 的原形态，行为**一字不改**：
          `job_types()` 里出现 `"batch"`；
        - `job_type(JobTypeSpec(...), runner=…)` —— 富声明。`kind` 进 `job_types()`，
          `id` 由 `job_type_spec(id)` 查。动作就是「命令以外的任务类型」，
          所以**不新增第 7 类注册点**（`AC-3.2` 的「六类」措辞不被动到）。

        `runner` 可省：动作默认由内核的 `chain_runner` 执行——它只编排**既有按键**，
        所以省掉不写也不会缺业务实现（业务实现在按键里，不在动作里）。
        """
        if isinstance(kind_or_spec, models.JobTypeSpec):
            spec = kind_or_spec
            self._claim("job_type", spec.id)
            self._job_specs[spec.id] = spec
            self._job_types.setdefault(spec.kind, runner or CHAIN_RUNNER)
            self._origins[("job_type_spec", spec.id)] = self.origin
            return
        self._claim("job_type", kind_or_spec)
        self._job_types[kind_or_spec] = runner

    def route(self, method: str, path: str, handler, *, name: str = "") -> Route:
        route = Route(method, path, handler, name=name or path)
        self._claim("route", f"{route.method} {path}")
        self._routes.append(route)
        return route

    # ---------------------------------------------------------------- 查询

    def nav_items(self) -> list:
        """**根**导航项（子项通过各自的父项 `children` 暴露，不在这里重复出现）。"""
        roots = [item for item_id, item in self._nav.items() if item_id not in self._child_ids]
        return sorted(roots, key=lambda item: (item.order, item.id))

    def all_pages(self) -> list:
        """**所有**页面（含子项）——`/api/v1/pages/{id}` 与措辞扫描要的是这一份。"""
        return sorted(self._nav.values(), key=lambda item: (item.order, item.id))

    def panel_spec(self, panel_id: str) -> models.PanelSpec:
        spec = self._panels.get(panel_id)
        if spec is None:
            raise NotFound(f"没有注册过面板 {panel_id!r}", hint="检查插件是否已加载。")
        return spec

    def has_panel(self, panel_id: str) -> bool:
        return panel_id in self._panels

    def panel_ids(self) -> list:
        """已注册的面板 id。

        单独一个方法而不是让调用方去解包 `origins()` 的 `(kind, key)` 键——
        `__main__ --check` 就是在这里解包写错、导致 panels 恒为空（独立验收 D2）。
        """
        return sorted(self._panels)

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
        """页面描述——**纯数据**，前端按 kind 渲染（AC-3.3）。

        `requires`（`REQ-012.1`）一并下发：前端 shell 据此决定「直接渲染面板」还是
        「先显示选公司的空状态」。核心只搬运这个字符串，**不解释**它——所以新增一个
        依赖上下文的页面仍然只是插件里多写一个字段，不用改核心（`AC-11` / `AC-9`）。
        """
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
            "requires": list(item.requires),
            "panels": panels,
        }

    def default_page_id(self) -> str:
        """默认落地页：第一个声明 `default=True` 的导航项；没人声明就退回第一个页面。

        `REQ-012.1` 的 `AC-1.3` 要求默认落地页是**工作台**而不是某一个业务面板。
        实现方式刻意不是「核心写死 `home`」：核心不认识任何页面 id，谁声明谁生效
        （`AC-11`：加页面不改核心）。
        """
        items = self.nav_items()
        for item in items:  # nav_items() 已按 (order, id) 排序，先声明先赢
            if item.default:
                return item.id
        for item in items:
            return item.id
        raise NotFound("还没有注册任何页面")

    def routes(self) -> tuple:
        return tuple(self._routes)

    def job_types(self) -> tuple:
        return tuple(sorted(self._job_types))

    def job_type_spec(self, action_id: str) -> models.JobTypeSpec:
        spec = self._job_specs.get(action_id)
        if spec is None:
            raise NotFound(
                f"没有注册过动作 {action_id!r}",
                hint="动作清单见 /api/v1/actions。",
            )
        return spec

    def has_job_type_spec(self, action_id: str) -> bool:
        return action_id in self._job_specs

    def job_type_specs(self) -> list:
        return sorted(self._job_specs.values(), key=lambda spec: (spec.order, spec.id))

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
