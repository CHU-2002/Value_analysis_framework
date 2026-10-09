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

    def placeholder(self) -> str:
        """枚举型参数在动作**参数模板**里的写法（`{"report_type": "{enum:auto|annual}"}`）。

        为什么需要它：动作的步骤要编排既有按键，而被编排的按键有些参数必须是枚举值
        （`download_report --report-type`）。让动作作者把枚举候选抄一遍，就多了一份会漂移的
        副本；由参数声明**自己**给出占位符，动作只写 `{enum:…}` 即可。
        """
        if self.type != "enum" or not self.choices:
            return ""
        return "{enum:" + "|".join(str(choice) for choice in self.choices) + "}"

    @property
    def token(self) -> str:
        """参数在参数模板里的默认占位符：`{"stock_code": "{stock_code}"}`。"""
        return "{" + self.name + "}"


@dataclass(frozen=True)
class NavItem:
    """左侧导航的一项，同时就是一个页面。

    `REQ-012.1` 加了两个**可选**字段（不填即旧行为，所以回退这一片时 `REQ-009` 的页面照常工作）：

    - `children`：子导航项（元素同样是 `NavItem`）。核心只做**结构与序列化**，
      不解释层级含义——侧栏怎么缩进是前端 shell 的事；
    - `requires`：上下文需求，如 `("selection.company",)`。核心同样不解释业务，
      它只是一个字符串，由前端拿 `selection` 比对后决定「显示空状态」还是「渲染面板」；
    - `default`：默认落地页（`REQ-012` 的 `AC-2`：落地页是工作台而不是某一个业务面板）。
      仍然只是数据——核心不认识 `home` 这个 id，谁声明谁生效。
    """

    id: str
    title: str
    group: str = ""
    order: int = 100
    panels: tuple = ()
    description: str = ""
    children: tuple = ()
    requires: tuple = ()
    default: bool = False
    #: 可选：`callable(ctx, value) -> bool`，判断某个上下文值**真的能解析**。
    #: `requires` 只说「需要一个值」，说不清「给了个解析不出来的值」——后者原先会一路走到
    #: 面板渲染失败，把 `NOT_FOUND` 摆进顶栏横幅（门② 第四轮抓到：老的选择被记住、
    #: 产物被删/改名之后重开就会走到）。解析器由**插件**提供，核心只负责调用它并把
    #: 结果变成正常空状态。
    context_resolver: object = None

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "group": self.group,
            "order": self.order,
            "panels": list(self.panels),
            "description": self.description,
            "children": [
                child.to_json() if hasattr(child, "to_json") else dict(child)
                for child in self.children
            ],
            "requires": list(self.requires),
            "default": bool(self.default),
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
    validate: object = None      # callable(params) -> normalized params; runs before enqueue
    exclusive: bool = False     # reject an active command with the same normalized params
    exclusive_group: str = ""   # optional mutual exclusion across related commands
    outputs: dict = field(default_factory=dict)  # optional output resolver metadata

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


# --------------------------------------------------------------- 动作（第 6 类注册点）
#
# 「动作」声明在**既有的第 6 类注册点**上（`REQ-009.3` 的 `AC-3.2` 原话就是「命令以外的
# 任务类型」），不新增第 7 类：动作 = 意图 + 上下文绑定 + 预检 + 若干**既有按键** +
# （必要时）人机交接点。业务实现一行都不搬进面板。
#
# 这里仍然只有**数据**：步骤怎么跑是 `core/jobs.py` 的事，缺什么上下文是插件的事。

EXPECT_KIND = "file"          # 交接点校验产物存在且比「本步开始时」新
EXPECT_GLOBS = "glob"         # 同上，但按 glob 展开（`{run_dir}/modules/*/result.json`）

_NETWORK_FALSE = "false"


@dataclass(frozen=True)
class CommandStep:
    """一个确定性步骤：跑一条**已注册的按键**，参数由 `bind` 声明。

    `bind` 的值是参数模板，占位符有三种来源（见 `docs/CONSOLE_V2_PLAN.md` §5.5 / §17.5）：

    1. 已解析的上下文值 —— `{ticker}` / `{company_dir}` / `{run_dir}` / `{home}`；
    2. 被编排按键自己的参数占位符 —— `{stock_code}`（由动作从上下文给值）；
    3. 参数声明自己给出的枚举占位符 —— `{enum:auto|annual}`；
    4. 未写 `bind` 的参数用默认占位符 `{参数名}` 兜底（写不出值就在**起进程之前**报错，
       不猜、不静默少传一个必填参数）。
    """

    command: str
    title: str = ""
    bind: dict = field(default_factory=dict)
    capture: dict = field(default_factory=dict)   # {"占位符名": "正则"} 从步骤输出捕获
    writes: tuple = ()

    def to_json(self) -> dict:
        return {
            "kind": "command",
            "command": self.command,
            "title": self.title,
            "bind": dict(self.bind),
            "capture": dict(self.capture),
            "writes": list(self.writes),
        }


@dataclass(frozen=True)
class HumanStep:
    """人机交接点：这一步只能在 Claude Code / OpenCode 里跑（仓库没有程序化 LLM 入口）。

    它**不产生子进程**：任务进入 `awaiting_agent`，界面给出可复制的命令与已解析好的路径，
    「已跑完」后由服务端校验 `expects`（存在且比本步开始时新）才继续。
    """

    title: str
    slash: str = ""
    hint: str = ""
    expects: tuple = ()
    expect_kind: str = EXPECT_KIND
    writes: tuple = ()

    def __post_init__(self):
        if self.expect_kind not in (EXPECT_KIND, EXPECT_GLOBS):
            raise ValueError(
                f"交接点的 expect_kind={self.expect_kind!r} 不合法，只能是 "
                f"{(EXPECT_KIND, EXPECT_GLOBS)}"
            )

    def to_json(self) -> dict:
        return {
            "kind": "human",
            "title": self.title,
            "slash": self.slash,
            "hint": self.hint,
            "expects": list(self.expects),
            "expect_kind": self.expect_kind,
            "writes": list(self.writes),
        }


@dataclass(frozen=True)
class JobTypeSpec:
    """动作声明（第 6 类注册点的富化形态）。

    `id` 是**动作 id**（`company.update_analysis`），`kind` 是任务类型（本需求只有 `chain`）。
    这样 `job_types()` 仍然是「类型 id 的元组」（`REQ-009.3` 的诊断快照据此列出），
    而动作 id 由 `job_type_spec(id)` 查——旧契约不动，新能力另开查询。
    """

    id: str
    title: str
    kind: str = "chain"
    description: str = ""
    group: str = ""
    order: int = 100
    requires: tuple = ()          # 缺上下文 → 禁用 + 给理由（预检在服务端算）
    steps: tuple = ()
    effects: dict = field(default_factory=dict)   # {"network":…, "quota":…, "writes":(…)}
    danger: bool = False
    confirm: dict = field(default_factory=dict)   # {"title","body","confirm_label"}
    preflight: object = None      # 可选：callable(ctx, selection) -> {"blockers": [...]}
    # 可选：callable(ctx, selection) -> {"requests": n, "seconds": n, ...}。
    # `AC-4` 要求执行前说明「预计耗时/调用量」——预估必须是**服务端**算出来的
    # （前端算的测不到），所以它是声明的一部分，由 `GET /api/v1/actions` 下发。
    estimate: object = None
    endpoint: str = ""            # 面板 kind=actions 的取数地址（可选）

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "group": self.group,
            "order": self.order,
            "requires": list(self.requires),
            "steps": [
                step.to_json() if hasattr(step, "to_json") else dict(step)
                for step in self.steps
            ],
            # `effects` 的值可能是元组（`writes=("数据包",)`）：JSON 里必须是数组，
            # 否则前端 `join` 会炸、断言也得跟着写元组（实测踩到）。
            "effects": {
                key: (list(value) if isinstance(value, (list, tuple)) else value)
                for key, value in self.effects.items()
            },
            "danger": bool(self.danger),
            "confirm": dict(self.confirm),
        }
