"""扩展点的**纯数据**定义：导航、面板、按键、数据集。

为什么全是不可变 dataclass：插件只「声明」这些东西，内核负责渲染与调度（设计文档 P2 声明式优先）。
声明是数据 → 能被测试逐项核对、能被文档引用，也不会在运行中被谁改掉。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 面板渲染模式：
#   server —— 服务端渲染成 HTML 片段（表格/时间线/指标卡/降级卡片），可在无浏览器环境下断言
#   client —— 服务端只回数据，浏览器画（图表需要 canvas）
RENDER_SERVER = "server"
RENDER_CLIENT = "client"
RENDER_MODES = (RENDER_SERVER, RENDER_CLIENT)


@dataclass(frozen=True)
class Param:
    """面板/按键的参数声明。`source` 表示它取自前端的「当前选择」（company/period/run）。"""

    name: str
    type: str = "string"          # string | int | float | bool | enum | company | period | run | path
    required: bool = False
    default: object = None
    choices: tuple = ()
    source: str = ""              # 非空时表示该参数由前端选择器提供，不由用户手填
    help: str = ""

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "choices": list(self.choices),
            "source": self.source,
            "help": self.help,
        }


@dataclass(frozen=True)
class NavItem:
    """左侧导航的一项，同时就是一个页面。"""

    id: str
    title: str
    group: str = ""
    order: int = 100
    panels: tuple = ()
    description: str = ""

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "group": self.group,
            "order": self.order,
            "panels": list(self.panels),
            "description": self.description,
        }


@dataclass(frozen=True)
class PanelSpec:
    """页面上的一块。`provider` 是进程内取数函数（服务端渲染与测试用），不参与序列化。"""

    id: str
    kind: str
    title: str = ""
    render: str = RENDER_SERVER
    endpoint: str = ""
    provider: object = None
    params: tuple = ()
    options: dict = field(default_factory=dict)
    size: str = "full"            # full | half
    description: str = ""

    def __post_init__(self):
        if self.render not in RENDER_MODES:
            raise ValueError(
                f"面板 {self.id!r} 的 render={self.render!r} 不合法，只能是 {RENDER_MODES}"
            )

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "render": self.render,
            "endpoint": self.endpoint,
            "params": [param.to_json() for param in self.params],
            "options": dict(self.options),
            "size": self.size,
            "description": self.description,
        }


@dataclass(frozen=True)
class CommandSpec:
    """按键：一条白名单命令 + 结构化参数（不经过 shell）。"""

    id: str
    argv: tuple
    title: str = ""
    group: str = ""
    params: tuple = ()
    danger: bool = False          # 会覆盖/删除/联网的按键，界面上要二次确认
    description: str = ""

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "argv": list(self.argv),
            "title": self.title,
            "group": self.group,
            "params": [param.to_json() for param in self.params],
            "danger": self.danger,
            "description": self.description,
        }


@dataclass(frozen=True)
class DatasetSpec:
    """数据集：从哪些源文件、用哪个解析器、怎么算指纹（AC-3.4）。"""

    name: str
    sources: tuple = ()
    parser: str = ""
    parser_version: int = 1
    key: object = None            # callable(params) -> str，缓存分片键
    hash_strategy: str = "sha256"  # sha256 | stat
    schema_version: str = "1.0"

    def __post_init__(self):
        if self.hash_strategy not in ("sha256", "stat"):
            raise ValueError(
                f"数据集 {self.name!r} 的 hash_strategy={self.hash_strategy!r} 不合法"
            )

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "sources": list(self.sources),
            "parser": self.parser,
            "parser_version": self.parser_version,
            "hash_strategy": self.hash_strategy,
            "schema_version": self.schema_version,
        }
