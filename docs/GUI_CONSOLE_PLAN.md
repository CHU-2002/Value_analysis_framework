# 图形化控制台设计（REQ-009）

> 本文写**怎么做**。要什么、做到什么程度以 [`docs/requirements/REQ-009-local-gui-console.md`](requirements/REQ-009-local-gui-console.md)
> 为准；两者不互相复制。开发流程见 [`docs/DEVELOPMENT.md`](DEVELOPMENT.md)，测试策略见 [`docs/TESTING.md`](TESTING.md)。
> 只想了解「做完能干什么、怎么干活」请看导读 [`docs/GUI_CONSOLE_OVERVIEW.md`](GUI_CONSOLE_OVERVIEW.md)（非权威）。

**阅读顺序**：§2 原则 → §3 架构 → §4 扩展点 → §5 插件模型 → §6 面板协议 → §7 API 契约 →
§8 数据层与缓存 → §9 远程边界 → §9.1 手动采集与长期存档 → §11 目录布局 →
§17 如何加一个新功能（cookbook）。

## 1. 一句话方案

**用 Python 标准库起一个只监听本机的小服务；内核只做传输、路由、信封、安全与调度，
一切 GUI 功能都是插件；图表等派生数据经统一的本地数据层读取并缓存，浏览路径永不联网。**

不引入任何第三方依赖：不装 Web 框架、不装图表库、不装前端构建链（Node/npm）。

## 2. 设计原则

| # | 原则 | 为什么（不这么做会怎样） |
|---|------|--------------------------|
| P1 | **核心最小、功能外挂**（微内核） | 使用者会不断加 GUI 需求。若把页面与接口硬编码在服务里，每加一个需求都要改同一批核心文件，改动互相耦合、无法独立评审与回滚 |
| P2 | **声明式优先，命令式兜底** | 页面、面板、按键、数据集都用**数据**声明，框架负责渲染与调度；只有真的需要自定义逻辑时才注册 handler。声明式的东西能被测试与文档自动化核对 |
| P3 | **数据与呈现分离** | 后端只产出数据（JSON），前端按 `kind` 渲染。后端不拼 HTML，前端不猜业务。换图表类型不动后端，换数据不动前端 |
| P4 | **本地优先、离线优先** | 一切视图只读已落盘产物；远程调用只在用户显式点按键时发生。财务分析是「慢而贵」的操作，不该被一次页面刷新触发 |
| P5 | **派生数据必须可失效** | 缓存不失效就会显示过期数字（比慢更糟）。指纹把「源文件 + 解析器版本 + 参数」一起锁住，任一变化自动重算 |
| P6 | **零新增依赖** | CI 只装 `requirements-test.txt`，scope 预算只剩 78 条用例；新包同时拖慢 CI 并可能顶破门禁（实测 33/40 文件、1522/1600 用例） |
| P7 | **可扩展性必须可判定** | 「架构很可扩展」是无法验收的表述。因此定义成可测判据：演示插件在 `--plugins` 目录里注册即生效，且核心文件指纹不变（AC-9 / AC-3.2） |

## 3. 分层架构

```
┌──────────────────────────────────────────────────────────────────────┐
│ 前端 shell（static/，原生 JS，无框架）                                │
│   app.js         : 启动 → 取导航/页面描述 → 分发到 kind 渲染器         │
│   kinds/*.js     : table / chart / timeline / markdown / form / …     │
│   registry.js    : registerPanelKind(kind, renderer)  ← 前端扩展点     │
└───────────────────────────▲──────────────────────────────────────────┘
                            │ JSON（/api/v1，统一信封，纯数据）
┌───────────────────────────┴──────────────────────────────────────────┐
│ 内核 core/（不含任何具体业务；AC-9 禁止在这里出现 /api/companies 之类）│
│   server.py     : ThreadingHTTPServer + 静态资源 + 请求日志            │
│   router.py     : 路径模板匹配（/api/v1/companies/{dir}/charts）        │
│   registry.py   : 六类注册点（nav/panel/route/dataset/command/job）    │
│   envelope.py   : 信封 + 稳定错误码 + JSON 编解码                      │
│   security.py   : 路径 jail / 环回校验 / shell 元字符 / token 脱敏      │
│   jobs.py       : 任务运行器（线程、上限、日志环形缓冲、取消、历史落盘）│
│   config.py     : 默认值 + webui.config.json + 环境变量覆盖            │
├──────────────────────────────────────────────────────────────────────┤
│ 数据层 datastore/（框架能力，插件复用）                                │
│   datasets.py   : DatasetSpec 注册 + get(dataset, key, params)        │
│   cache.py      : 指纹 / 命中 / 原子写 / 分片锁 / 失效 / 清理           │
│   parsers/*.py  : markdown_tables / data_pack / run_store / artifacts │
│   paths.py      : 定位 output 根（沿用既有 runs.py resolve 规则）      │
│   render/markdown_safe.py : 先转义再渲染（安全）                        │
├──────────────────────────────────────────────────────────────────────┤
│ 插件层 plugins/（每个 GUI 功能一个模块，只依赖内核与数据层的公开 API）  │
│   companies.py   公司列表 / 产物索引 / 报告渲染                        │
│   charts.py      3 类图表（数据来自 datasets）                         │
│   commands.py    按键白名单（既有脚本入口）                            │
│   run_history.py 迭代台账时间线                                        │
└───────────────────────────▲──────────────────────────────────────────┘
                            │ 只读（不复制、不改写）
┌───────────────────────────┴──────────────────────────────────────────┐
│ 源层 output/**：data_pack_market.md、history.jsonl、latest.json、      │
│ runs/{run_id}/*、qualitative_report.md、value_computed.md、PDF …       │
└──────────────────────────────────────────────────────────────────────┘
             ▲ 只有用户显式点「按键」才会联网（既有脚本），结果落回源层
        Tushare / yfinance / CNINFO
```

依赖方向是**单向**的：插件 → 数据层 → 源层；插件 → 内核公开 API。内核**不 import 插件**
（插件由 `plugins/__init__.py` 在启动时注入注册表），数据层不知道插件存在。

## 4. 扩展点清单（框架的核心资产）

六类注册点，全部是「声明式数据 + 可选 handler」。**新增 GUI 功能 = 在这些点上登记，不改核心。**

| 扩展点 | 注册 API（示意） | 声明什么 | 典型用途 |
|--------|------------------|----------|----------|
| 导航/页面 | `registry.nav(NavItem(id, title, group, order))` | 左侧栏一项 + 它包含的面板 id | 新增「对比分析」页 |
| 面板 | `registry.panel(PanelSpec(id, kind, title, endpoint, params, options, size))` | 页面上的一块 | 新增一张图、一张表 |
| API 路由 | `registry.route("GET", "/api/v1/xxx/{id}", handler)` | 自定义数据接口（返回信封） | 需要跨数据集聚合时 |
| 数据集 | `registry.dataset(DatasetSpec(name, sources, parser, parser_version, key, hash_strategy))` | 「从哪些文件、用哪个解析器、怎么算指纹」 | 新增一个数据源（如分红历史） |
| 按键 | `registry.command(CommandSpec(id, argv, params, group, danger))` | 白名单命令 + 结构化参数 | 新增一个脚本入口 |
| 任务类型 | `registry.job_type(kind, runner)` | 命令以外的任务（批次管线、定时任务） | 一键跑「取数 → 解析 → 分析」串 |

**前端扩展点**：`registerPanelKind(kind, renderer)`。新增可视化类型（如热力图、雷达图）只加一个
`kinds/heatmap.js` 并注册，`app.js` 的分发逻辑不动。

**AC-9 的判定方式**（可扩展性不是口号，是可测的）：

1. 测试在 `tmp_path` 造一个**演示插件**（注册导航 + 面板 + 路由 + 数据集），用 `--plugins <tmp>` 启动服务；
2. 断言 `/api/v1/nav`、`/api/v1/pages/{id}`、`/api/v1/demo/...` **自动出现**且可用；
3. 断言核心文件（`core/*.py`、`static/index.html`、`app.js` 里 `dispatchPanel` 段）的 **sha256 与实现时记录一致**；
4. 断言未知 `kind` 的面板返回降级卡片而不是 500 或白屏。

## 5. 插件模型

插件是一个普通 Python 模块，暴露一个函数即可：

```python
# scripts/webui/plugins/charts.py（示意）
from webui.core.models import NavItem, PanelSpec

def contribute(registry):
    registry.dataset(DatasetSpec(
        name="charts.annual_price",
        sources=["data_pack_market.md", "runs/*/inputs/data_pack_market.md"],
        parser="data_pack.annual_price",
        parser_version=1,
    ))
    registry.panel(PanelSpec(
        id="charts.annual_price", kind="chart", title="年度股价走势",
        endpoint="/api/v1/companies/{dir}/charts/annual_price",
        params=[Param("dir", type="company", required=True)],
        options={"chart": {"type": "line", "x": "labels", "series": "series"}},
        size="half",
    ))
    registry.nav(NavItem(id="charts", title="图表", group="分析", order=20,
                         panels=["charts.annual_price", "charts.metrics", "charts.revenue_profit"]))
```

加载规则：

- `plugins/__init__.py` 维护 `BUILTIN = ["companies", "charts", "commands", "run_history"]`，启动时按序 `contribute()`；
- `--plugins <目录>`（可多次）加载仓库外插件：**第三方/实验性扩展不必 fork 仓库**；
- 注册冲突（同 id 重复注册）在启动时报错并指明两个来源，**不静默覆盖**；
- 插件不得 import 另一个插件（避免隐式依赖网）；共享逻辑放数据层或内核公开 API；
- 插件抛异常只让**它自己的扩展点**失效并记日志，不拖垮整个服务（一个坏插件不能让面板打不开）。

## 6. 声明式面板协议

服务端把「页面长什么样」当**数据**返回。前端不知道任何具体业务，只认 `kind`。

**两种渲染模式**（实现时明确的取舍，见 §6.1）：

- `render: "server"` —— 服务端渲染成 HTML 片段（表格 / 时间线 / 指标卡 / Markdown / 降级卡片）；
- `render: "client"` —— 服务端只回数据契约，浏览器画（图表需要 canvas）。

### 6.1 为什么把表格/时间线放服务端（实现时确定）

CI 里**没有浏览器、没有 JS 运行时**。如果所有 kind 都在前端渲染，AC-3.3 的
「给定面板描述 → 断言渲染输出」就只能在人肉点开浏览器时验，等于没法验收。
把结构化展示（表格/时间线/指标卡/Markdown）放服务端渲染后：

- 渲染结果可在 CI 里直接断言（含 XSS 转义：`<script>` 必须以 `&lt;script&gt;` 出现）；
- 未知 `kind` 的降级卡片也在服务端产出，可断言「不白屏、不 500」；
- **只有真正需要 canvas 的图表**保留客户端渲染，服务端只给 `{labels, series}` 数据契约。

代价：前后端各有一小部分渲染代码（服务端 HTML 生成器 + 前端 canvas 绘制），
但换来的是「面板渲染」这件事**真的被测试覆盖**，而不是靠肉眼。

```jsonc
// GET /api/v1/pages/charts → data
{
  "schema": "webui.page", "schema_version": "1.0",
  "id": "charts", "title": "图表", "group": "分析",
  "panels": [
    {
      "id": "charts.annual_price", "kind": "chart", "title": "年度股价走势",
      "size": "half", "render": "client",
      "endpoint": "/api/v1/companies/{dir}/charts/annual_price",
      "params": [{"name": "dir", "type": "company", "required": true, "source": "selection.company"}],
      "options": {"chart": {"type": "line", "x": "labels", "series": "series", "yFormat": "number"}},
      "data": {"labels": ["2025", "2026"], "series": [{"name": "年末收盘", "values": [28.6, 26.6]}]}
    }
  ]
}
```

已注册的 `kind`（首批）：

| kind | 数据形状 | 渲染 |
|------|----------|------|
| `table` | `{columns:[{key,title,align}], rows:[...]}` | HTML 表格（可排序） |
| `chart` | `{labels:[...], series:[{name, values:[...]}]}` | Canvas 折线/柱状，悬停显示数值 |
| `timeline` | `{items:[{at, title, badges:[], detail, changes:[]}]}` | 竖向时间线（迭代台账） |
| `markdown` | `{html}` | 渲染后的报告（服务端已转义+渲染） |
| `form` | `{command, params:[...]}` | 按键的参数表单 |
| `jobs` | `{jobs:[...]}` | 任务列表与日志尾部 |
| `stat` | `{items:[{label, value, hint, state}]}` | 指标卡（如 downstream stale 警告） |
| `fallback` | — | **未知 `kind` 的降级卡片**：显示「此面板需要更新的前端（kind=xxx）」+ 面板标题，不白屏、不报错 |

`params[].source` 让面板声明「我的参数来自当前选择」（当前公司/期次/run），前端选择器一变即重取——
避免每个页面各写一套状态管理。`schema_version` 不匹配时前端给出版本提示而不是静默错渲染。

## 7. API 契约

- 前缀 `/api/v1`（版本进了路径，将来破坏性变更走 `/api/v2` 并存，不偷偷改语义）；
- 统一信封：

```jsonc
{ "ok": true, "schema_version": "1.0",
  "data": { /* 各接口自己的形状 */ },
  "warnings": ["history.jsonl 第 7 行损坏，已跳过"],
  "meta": { "cached": true, "fingerprint": "sha256:…", "generated_at": "…",
            "source_mtime": "…", "parser_version": 1 },
  "error": null }
```

```jsonc
{ "ok": false, "schema_version": "1.0", "data": null, "warnings": [], "meta": {},
  "error": { "code": "PATH_OUTSIDE_ROOT", "message": "路径越出 output/ 根",
             "hint": "只允许访问 output/ 下的产物" } }
```

- **错误码是稳定枚举**（`core/envelope.py` 一处定义，测试断言全集），禁止把异常文本当错误码：

```
BAD_REQUEST · BAD_JSON · NOT_FOUND · UNKNOWN_ROUTE · INVALID_PARAM · UNKNOWN_COMMAND
SHELL_METACHAR · PATH_OUTSIDE_ROOT · ARTIFACT_MISSING · PARSE_FAILED · PARSE_COLUMNS_MISMATCH
TOO_MANY_JOBS · JOB_NOT_FOUND · NOT_LOOPBACK · PORT_IN_USE · INTERNAL
NO_TOKEN · QUOTA_CONFIRM_REQUIRED · BATCH_RUNNING · BATCH_NOT_FOUND · ARCHIVE_UNWRITABLE
```

- 状态码映射：参数类 → 400/422；越界/穿越 → 403；缺文件 → 404；冲突（并发超限）→ 429；
  未捕获异常 → 500 且**只回 `INTERNAL` + 请求 id**，堆栈只进服务端日志（不外泄路径与内容）；
- 所有响应 `Content-Type: application/json; charset=utf-8`，`Cache-Control: no-store`
  （数据新鲜度由我们自己的指纹与元信息管，交给浏览器缓存会两边打架）。

## 8. 数据层与缓存（图表数据承载）

### 8.1 三层模型

| 层 | 位置 | 内容 | 生命周期 |
|----|------|------|----------|
| **源层** | `output/**` 既有产物 | `data_pack_market.md`、`history.jsonl`、`latest.json`、`runs/{run_id}/*`、报告 | 既有流程写，本框架**只读**（不复制、不改写） |
| **派生缓存层** | `output/.webui_cache/`（可配 `--cache-dir`） | 解析后的结构化 JSON + 元信息 | 由指纹决定命中/失效；可整体删除，删了只是变慢 |
| **请求内存层** | 进程内、单请求作用域 | 同一次请求里共享的解析结果 | 请求结束即释放 |

```jsonc
// GET /api/v1/companies/600887_伊利/charts/annual_price → data（前端直接画）
{ "labels": ["2016","2017",…,"2026"],
  "series": [ {"name":"年末收盘","values":[17.60,32.19,…]},
              {"name":"年度最高","values":[20.66,33.70,…]},
              {"name":"年度最低","values":[17.00,17.38,…]} ] }
```

### 8.2 数据集声明与指纹

```python
DatasetSpec(
    name="charts.annual_price",
    sources=["data_pack_market.md"],        # 相对公司目录的 glob（含 run-store 布局候选）
    parser="data_pack.annual_price",        # datastore/parsers 里的解析器
    parser_version=1,                       # 解析逻辑一改就 +1 → 旧缓存自动失效
    key=lambda params: params["dir"],        # 缓存分片键
    hash_strategy="sha256",                 # 或 "stat"（size+mtime_ns，留给将来的大二进制）
)
```

**指纹 = `sha256(parser_version ‖ 规范化参数 ‖ 每个源文件的 sha256)`**。命中条件：

```
缓存文件存在 && fingerprint 相同 && schema_version 相同  → 直接用（连解析都不做）
否则 → 重新解析 → 原子替换（写 .tmp 再 os.replace）→ 更新 meta
```

- 源文件内容变化、解析器升级（`parser_version` +1）、查询参数变化，任一都会自动失效；
- **选文件内容摘要而不是 mtime**：`data_pack_market.md` 只有 24KB、报告 50KB，全量 sha256 亚毫秒级，
  但能避免「git checkout / 复制文件导致 mtime 变但内容没变」的假失效；大二进制留 `stat` 策略；
- 缓存条目里记录**溯源信息**：用到哪些源文件、各自 sha256、解析器版本、生成时间 → `meta` 回给前端，
  用户能看见「这个图是什么时候、从哪份数据算出来的」。

### 8.3 失效、清理与并发

| 场景 | 行为 |
|------|------|
| 内容变化 / 解析器升级 | 指纹不匹配 → 自动重算，**无需手工清缓存** |
| 用户点「刷新数据」 | 数据集级 `invalidate(dataset)`：丢派生缓存并离线重算（**不联网**，见 §9） |
| 手工清理 | `python -m scripts.webui --clear-cache`（或直接删 `output/.webui_cache/`），删完下次自动重建 |
| 并发请求同一数据集 | 每个缓存分片一把 `threading.Lock`；同键并发只算一次，其余等待结果 |
| 写中断 / 崩溃 | `.tmp` → `os.replace` 原子替换；启动时清理残留 `.tmp` |
| 多进程同时跑 | 不在本期范围；用锁文件检测并只读降级（写进演进路线） |

### 8.4 缓存为什么放 `output/.webui_cache/`

`output/` 已被 `.gitignore` 忽略，缓存不会污染仓库；缓存与产物同根，便于「一个公司的数据一起删」；
缓存内容是解析后的产物数据（与 `output/` 同等敏感，**不含 token**），不放到 `/tmp` 之类会被共享/清理的位置。

## 9. 远程边界：怎样避免「每次都从远程拉取」

这是使用者明确提出的问题，答案是**三条保证 + 一个区分**：

| # | 保证 | 落地方式 | 判定 |
|---|------|----------|------|
| ① | **浏览路径永不联网** | 视图、图表、报告、迭代台账全部只读 `output/`；框架不给插件任何 HTTP 客户端 | AC-3.5：socket 被 stub 为抛错时，浏览类接口仍全部正常 |
| ② | **远程只由用户显式触发** | 只有点按键才调用既有脚本（`tushare_collector` / `download_report` / `buy_sell_plan`），由脚本落盘到 `output/` | AC-1.3 白名单 + AC-2 按键清单 |
| ③ | **落盘结果被索引并缓存** | 脚本跑完后源层文件变化 → 指纹变化 → 派生缓存按需重算；下次浏览命中缓存 | AC-3.4 解析计数与失效断言 |

**关键区分：缓存失效 ≠ 重新采集。** 面板上必须是两个不同的动作，不能混成一个「刷新」按钮：

| 动作 | 是否联网 | 做什么 | 何时用 |
|------|----------|--------|--------|
| **刷新视图**（自动/被动） | 否 | 指纹变了就重算派生数据；没变就用缓存 | 每次打开页面 |
| **重新采集**（显式按键） | **是** | 跑 `tushare_collector` / `download_report` 等脚本，更新源层产物 | 你确实想要最新行情/新一期财报时 |

配套的**新鲜度提示**：每个数据集在 `meta` 里回 `source_mtime` / `generated_at` / `cached`，
面板上显示「数据生成于 X（缓存命中 / 刚刚重算）」。这样用户判断的是**数据有多旧**，
而不是「页面卡不卡」——他才知道什么时候值得点那个联网的按键。

## 9.1 手动采集与长期存档（REQ-009.4）

### 9.1.1 它和 §8 的缓存是两回事

| | 派生缓存（§8） | **原始存档（本节）** |
|---|---|---|
| 内容 | 解析后的图表序列、产物索引、迭代台账 | 远程接口的**原始响应** |
| 来源 | `output/` 里已有产物 | Tushare / yfinance ——**花钱换来的** |
| 过期 | 指纹变了就失效重算 | **永不过期**，只有显式重拉才更新 |
| 位置 | `output/.webui_cache/`（仓库内、gitignore） | `~/turtle_archive/`（**仓库外**，可配） |
| 删掉的代价 | 变慢（能重算） | **要重新花钱拉** → 框架不主动删，删除需二次确认 |
| 失效语义 | 自动 | 只能由人显式覆盖（`--force`） |

一句话：**派生缓存是「算出来的」，原始存档是「买来的」**，两者的过期策略与删除策略必须分开。

### 9.1.2 数据流

```
人点「采集」（唯一联网入口）
  → 批次编排器：目标清单（标的 × 期次 × 板块） × 配额档案（frugal | bulk）
  → 调用量预估 → 人确认（--yes 可跳过）
  → 复用既有客户端 scripts/tushare_modules/（rate_limit / MAX_RETRIES / VIP 路由 get_api_url）
  → 原始响应落存档 + 追加采集台账 manifest.jsonl + 批次进度落盘（逐目标）
  → 结果分类：ok / empty / no_permission / rate_limited / error  → 缺口清单 + 完备度
  → 既有 tushare_collector.py 仍产出 data_pack_market.md（契约与退出码不变）
  → §8 数据层解析 → 图表 / 报告 / 迭代台账视图（离线，AC-3.5 不变）
```

**采集是唯一的联网路径**，而且必须由人点。采集完成后所有视图仍走 §8 的本地缓存——
「浏览不联网」这条不变量不因为本切片而放宽。

### 9.1.3 存档布局与元信息

```
~/turtle_archive/                     # archive_root，可配；默认在仓库之外
├── manifest.jsonl                    # 追加式台账：一行一条采集记录（谁、何时、拉了啥、结果）
├── batches/{batch_id}.json           # 批次定义 + 进度 + 结果计数 + owner_pid/heartbeat_at
└── {ticker}/
    ├── meta.json                     # 标的级：最近采集时间、完备度、缺口摘要
    └── {dataset}/{period}.json       # 原始响应
        {dataset}/{period}.meta.json  # 与之一一对应的元信息（人工可读）
```

```jsonc
// 存档元信息（{dataset}/{period}.meta.json）
{ "schema": "webui.archive.record", "schema_version": "1.0",
  "dataset": "income", "ticker": "600887.SH", "period": "2026H1",
  "api": { "name": "income", "params": {"ts_code": "600887.SH", "period": "2026H1"} },
  "fetched_at": "2026-09-25T02:10:11Z",
  "token_fingerprint": "3f9a1c02",        // sha256(token) 前 8 位；永不写 token 本身
  "tier_label": "租用 5000 分",            // 用户填写的账号档位标签
  "quota_profile": "bulk",
  "framework_version": "0.1.0",
  "result": "ok",                          // ok | empty | no_permission | rate_limited | error
  "error_excerpt": null,                   // 失败时保留接口原文摘要（截断）
  "content_sha256": "…", "bytes": 20481 }
```

**为什么默认放仓库外**：`output/` 是 gitignore 目录，容易被 `git clean`、切分支、清缓存顺手删掉；
而这里的每一条都是花配额换的。放仓库外 + 框架不主动删 + 删除要二次确认，才配得上它的成本。

### 9.1.4 批次状态机与断点续跑

```
pending ──▶ running ──▶ done      （全部 ok/empty）
                │  ├──▶ partial   （跑完但有缺口：no_permission / rate_limited / error）
                │  └──▶ failed    （编排层错误：存档不可写、清单为空…）
                └──◀── resume     （同 batch_id 重启：只补未完成目标）
```

- **进度逐目标落盘**：每完成一个目标就更新 `batches/{batch_id}.json`，
  所以 Ctrl-C、崩溃、断电之后重启同一批次**只补未完成的目标**，已完成的不重拉（AC-4.4）；
- **同批次并发保护**：批次文件带 `owner_pid` + `heartbeat_at`；启动时若心跳未过期 → `BATCH_RUNNING`，
  防止手滑点两次把配额烧两遍；
- **去重**：同一（接口 + 参数 + 期次）已有存档 → 默认跳过并计入「命中存档」；`--force` 才重拉；
- **批次结束报告**：新增请求数 / 命中存档数 / 失败数 / 跳过（无权限）数——这是核对配额消耗的凭据。

### 9.1.5 配额档案

| 档案 | 适用 | 拉什么 | 调用量 |
|------|------|--------|--------|
| `frugal` | 低配额年包（日常） | 必需板块（§1 基本信息 / §2 行情 / §3–5 三表 / §12 关键指标）+ 最新期次 | 少 |
| `bulk` | **短租高积分账号** | 全部板块 × 全部期次 × 全部标的 | 大，**先预估再确认** |

调用量预估 = Σ(标的数 × 期次数 × 该档案的接口数)，在触发前展示（`QUOTA_CONFIRM_REQUIRED` 要求确认）。
`bulk` 的存在理由就是使用者的用法：**趁租用的短窗口，一次把能拿的都拿下来，然后长期不拉**。

### 9.1.6 缺口清单与完备度（修掉现状盲区）

现状问题（已核对代码）：`scripts/tushare_modules/financials.py:1083` 等处把权限错误与空数据
统一写成「数据缺失 (接口可能无权限)」，`data_pack_market.md` 末尾也只有一行「共 N/M 个数据板块成功获取」。
使用者据此**无法判断租一个高权限账号能补到哪些数据**——这正好抵消了「短租高权限」策略的价值。

```jsonc
// 缺口清单（GET /api/v1/companies/{dir}/gaps 或批次报告里）
{ "ticker": "600887.SH",
  "completeness": { "ok": 12, "empty": 1, "no_permission": 2, "rate_limited": 0, "error": 0, "total": 15 },
  "gaps": [ { "dataset": "holding_detail", "period": "2026H1",
              "result": "no_permission",
              "error_excerpt": "抱歉，您没有访问该接口的权限…" } ] }
```

- 结果枚举区分 **`no_permission`（权限/积分不足）**、**`rate_limited`（频率超限）**、
  `empty`（真没数据）、`error`（其他错误）——不再一律写成「数据缺失」；
- 缺口清单是**机器可读**的，界面上显示完备度（如 `12/14` 板块）与每个缺口的原因；
- **不猜积分门槛**：Tushare 的积分规则会变，猜错会误导决策。只如实保留错误原文，让使用者自己判断
  该租多高的档位（这一点写进 AC-4.5 的判据里：记录原文，不做门槛推断）；
- **按缺口补齐**（AC-4.6）：高配额账号下**只请求缺口目标**，补齐后完备度与缺口清单相应收敛。

### 9.1.7 与「浏览不联网」的关系

| 路径 | 联网 | 触发 |
|------|------|------|
| 视图 / 图表 / 报告 / 迭代台账 | 否（AC-3.5） | 打开页面 |
| 派生缓存重算 | 否（§8） | 指纹变化，自动 |
| **采集批次** | **是** | **只能由人点按键或跑 CLI**（AC-4.1） |

`frugal` 与 `bulk` 的切换、以及「补齐缺口」都不引入任何自动触发路径——没有调度器、没有启动即拉、
没有页面加载即拉（AC-4.1 会断言不存在这类代码路径）。

### 9.1.8 反模式（明确写下来，防止以后走偏）

| 反模式 | 为什么不行 |
|--------|-----------|
| 定时 / 启动自动采集 | 配额会被悄悄烧掉；违反 AC-4.1 |
| 把原始存档放 `output/` | 那是 gitignore 的临时目录，容易被顺手清理，而数据是花钱换的 |
| 用 TTL 过期原始数据 | 过期就要求重拉 = 重新花钱 |
| 权限错误当「数据缺失」 | 使用者无法判断高权限能补到什么（现状盲区） |
| 猜测「需要多少积分」 | 规则会变，猜错会误导；只记录错误原文 |
| 同一批次并发跑 | 重复烧配额（用 `owner_pid` + 心跳挡掉） |
| 把 token 写进存档或日志 | 存档可能被拷走/备份；只写指纹与档位标签 |

## 10. 安全中间件

| 风险 | 措施 | 归属 |
|------|------|------|
| 远程访问 | 仅绑定 `127.0.0.1`；**不提供**任何把它暴露到非环回的配置项（远程访问将来另开 REQ，连鉴权一起设计） | AC-3.1 |
| 命令注入 | 白名单 + 结构化参数 + `shell=False`；取值拒绝 `;` `|` `&&` `$(` 与换行 | AC-1.3 |
| 路径穿越 | 统一 `_safe_join(OUTPUT_ROOT, rel)`：`resolve()` 后必须仍在 `output/` 子树内；符号链接按真实路径判定 | AC-3.7 |
| 静态资源穿越 | 同上，jail 到 `static/` | AC-3.7 |
| 任意文件读取 | 报告接口只接受**服务端产物索引里的 id**（相对路径的 sha1 前 12 位），不接受路径参数 | AC-2.2 |
| token 泄露 | 不回显环境变量；日志环形缓冲与任务历史写入前，用 `TUSHARE_TOKEN` 的实际值做替换脱敏 | AC-3.7 / AC-1.4 |
| 报告内容 XSS | Markdown **先转义再渲染**（`render/markdown_safe.py`），原始 HTML 不生效 | AC-2.3 |
| 资源耗尽 | 并发任务上限 3；日志只留尾部 N 行并截断单行长度；解析失败快速失败不重试风暴 | AC-1.3 |

## 11. 目录与文件布局

```
scripts/webui/
├── __init__.py              # __version__ / API_VERSION
├── __main__.py              # CLI：--host/--port/--no-browser/--cache-dir/--plugins/--clear-cache
├── config.py                # 默认值 + webui.config.json + 环境变量覆盖（3.10 安全：不用 tomllib）
├── core/
│   ├── models.py            # NavItem / PanelSpec / Param / CommandSpec / DatasetSpec（纯数据）
│   ├── registry.py          # 六类注册点 + 冲突检测 + 插件贡献加载
│   ├── envelope.py          # 信封 + 错误码枚举
│   ├── errors.py            # 异常 → 错误码
│   ├── router.py            # 路径模板匹配 + 方法分发 + 中间件链
│   ├── security.py          # 路径 jail / 环回校验 / shell 元字符 / 脱敏
│   ├── jobs.py              # 任务运行器：线程、上限、环形缓冲、取消、历史落盘
│   └── server.py            # ThreadingHTTPServer + 静态资源 + 请求日志
├── datastore/
│   ├── datasets.py          # DatasetSpec 注册 + get(dataset, key, params)
│   ├── cache.py             # 指纹 / 命中 / 原子写 / 分片锁 / 失效 / 清理
│   ├── paths.py             # output 根定位（沿用既有 runs.py resolve 规则）
│   └── parsers/
│       ├── markdown_tables.py   # 空格容忍的通用表格解析
│       ├── data_pack.py         # §11 / §12 / §3 → 图表序列
│       ├── run_store.py         # history.jsonl / latest.json / record.json
│       └── artifacts.py         # 产物索引（分类、大小、mtime、id）
├── archive/                     # 原始存档层（REQ-009.4；与 datastore 的派生缓存分层）
│   ├── store.py                 # 存档根定位 / 读写 / manifest.jsonl 追加台账 / 二次确认删除
│   ├── batch.py                 # 批次状态机：目标清单、逐目标进度落盘、断点续跑、并发保护
│   ├── quota.py                 # 配额档案（frugal | bulk）与调用量预估
│   ├── gaps.py                  # 结果分类（ok/empty/no_permission/rate_limited/error）与缺口清单、完备度
│   ├── token.py                 # token 来源（env/.env）与指纹（sha256 前 8 位），绝不落明文
│   └── adapters/tushare.py      # 复用既有 scripts/tushare_modules 客户端的薄适配层（不重写协议）
├── render/
│   └── markdown_safe.py     # 先转义再渲染：标题/列表/表格/代码/链接
├── plugins/
│   ├── __init__.py          # BUILTIN 列表 + load_plugins(registry, extra_dirs)
│   ├── companies.py         # 公司列表 / 产物索引 / 报告渲染
│   ├── charts.py            # 3 类图（数据来自 datasets）
│   ├── commands.py          # 按键白名单（对应 AC-2 清单）
│   ├── collect.py           # 采集面板 / 批次进度 / 缺口清单（REQ-009.4）
│   └── run_history.py       # 迭代台账时间线
├── static/
│   ├── index.html           # 只有骨架 + 挂载点（已实现）
│   ├── app.js               # 启动、导航、选择器、面板分发（已实现）
│   ├── kinds/               # 只有客户端渲染的 kind 需要渲染器：
│   │                        #   chart.js（canvas 折线/柱状，已实现）、fallback.js（降级卡片，已实现）
│   │                        #   form.js / jobs.js 随 REQ-009.1、.4 加
│   └── style.css            # 已实现
└── webui.config.sample.json # 配置样例（真实配置 webui.config.json 用 .gitignore 忽略）——待补
```

> **实现进度（2026-09-21）**：`config.py`、`core/*`（models / registry / router / routes /
> envelope / errors / security / server / context）、`datastore/*`（cache / datasets / parsers +
> `markdown_tables`）、`render/panels.py`、`plugins/__init__.py`、`__main__.py`、
> `static/`（index/app/style/kinds）已完成并有 30 条用例（REQ-009.3 → `implemented`，PR #44）；
> `archive/*`（REQ-009.4）与三个功能插件（`commands.py` / `companies.py` / `charts.py` /
> `run_history.py` / `collect.py`）尚未实现。
> 服务端渲染的 kind（table / timeline / stat / markdown / fallback）**不需要** `static/kinds/` 下的文件，
> 原因见 §6.1。

`Makefile` 增加：`gui`（启动）、`gui-cache-clear`（清派生缓存）、`gui-collect`（跑一个采集批次，需 `--batch` 或 `--profile`）。
测试：`tests/test_webui_framework.py`、`tests/test_webui_archive.py`、`tests/test_webui_server.py`、`tests/test_webui_views.py`。

## 12. 既有功能如何落到插件（首版四个插件）

| 插件 | 注册的东西 | 对应 AC |
|------|-----------|---------|
| `companies.py` | 数据集 `companies.index`（扫 `output/*/latest.json`、`record.json`）、`artifacts.index`（产物分类索引）、路由 `/api/v1/companies`、`/api/v1/companies/{dir}/artifacts`、`/api/v1/companies/{dir}/report`（按 id 取产物、服务端渲染 Markdown） | AC-2.1、AC-2.2、AC-2.3 |
| `charts.py` | 数据集 `charts.annual_price` / `charts.metrics` / `charts.revenue_profit`（三个 `data_pack` 解析器）+ 三个 `chart` 面板 + 路由 `/api/v1/companies/{dir}/charts/{name}` | AC-2.4、AC-3 |
| `run_history.py` | 数据集 `runs.timeline`（`history.jsonl` + `latest.json` + `run.json`）+ `timeline` 面板 + `stat` 面板（downstream stale）+ 路由 `/api/v1/companies/{dir}/runs` | AC-2.5、AC-5 |
| `commands.py` | 按键清单（AC-2 的 14 个入口 + `runs` 六子命令 + 定性管线四步）+ `form` / `jobs` 面板 + 路由 `/api/v1/commands`、`/api/v1/jobs*` | AC-2、AC-1.1~AC-1.4 |
| `collect.py` | 采集面板（目标清单、配额档案、调用量预估确认）+ 批次进度 + 缺口清单与完备度 + 路由 `/api/v1/collect/batches*`、`/api/v1/companies/{dir}/gaps` | AC-4.1~AC-4.7 |

`commands.py` 的参数表与脚本真实 CLI **双向校验**（表里的 flag 必须存在于脚本源码；脚本里
`required=True` 的 flag 必须在表里）——表与脚本不会各改各的，且校验是纯源码扫描，
不起子进程、不联网。

## 13. 测试计划与预算

| 文件 | 覆盖 | 用例预算 | 手法 |
|------|------|----------|------|
| `tests/test_webui_framework.py` | REQ-009.3：AC-3.1~AC-3.7 + **AC-9（演示插件 + 核心指纹）** + 门② 两轮验收的缺口回归（D1~D10、N1~N9） | ≤ 42（实际 42） | 起真实服务绑 `127.0.0.1:0`（随机端口）；`tmp_path` 造 `output/` 与演示插件；网络用 stub 禁掉；解析器用计数假解析器 |
| `tests/test_webui_archive.py` | REQ-009.4：AC-4.1~AC-4.7 | ≤ 8 | `tmp_path` 当存档根；**假采集适配器**（返回预设响应或抛权限/频率错误），0 次真实请求；断言批次断点续跑与缺口分类；token 用假值断言"只出现指纹" |
| `tests/test_webui_server.py` | REQ-009.1：AC-1.1~AC-1.4 | ≤ 7 | `sys.executable -c` 假命令（不跑真实脚本、不联网）；断言不启动子进程的拒绝路径 |
| `tests/test_webui_views.py` | REQ-009.2：AC-2.1~AC-2.5 | ≤ 7 | `tmp_path` 造假公司目录与 `data_pack_market.md`；XSS 注入用例 |

四项合计 ≤ 64，与 `REQ-009` 的 AC-7 一致。**框架那一片从 30 → 38 → 42**：
门② 独立验收两轮共发现 19 个缺口（第一轮 `AC-3.3` 不成立 + 2 个阻断项；复验又发现
响应体泄漏 token 的 `N1` 阻断项），修复必须带回归测试，因此后面三片的配额压到 8/7/7。
**这三片如果再涨就要先清理或申请上调**——AC-7 的「先清理」纪律不变，
不得为了让测试挤进预算而删断言或放宽判定。

预算核算：当前 1522/1800（余量 278；文件 33/48）。预算已于 2026-09-21 **经 owner 批准**由 40 文件 / 1600 用例
上调为 48 / 1800（理由、代价与留痕见 `scripts/test_scope.py` 的注释、REQ-006 的 AC-7 变更记录与任务 T7、
`docs/TESTING.md` §5）；新增 4 文件 ≤ 64 用例 → 预计 **1586/1800（88%）、37/48**。
**纪律不变**：不得为了让新测试挤进预算而删断言、加 `skip` 或放宽门禁；下次接近新上限仍先清理/合并冗余用例。
其余测试纪律照旧：不联网、不写仓库 `output/`、不 `time.sleep`、不依赖当前时间与随机顺序。

## 14. 交付顺序与验收

| 顺序 | 编号 | 交付内容 | 独立验收 |
|------|------|----------|----------|
| 1 | `REQ-009.3` | 内核 + 注册表 + 面板协议 + 数据层缓存 + 契约 + 安全 + 演示插件 + 扩展文档 | **`implemented`（PR #44）**；报告逐条核对 AC-3.1~3.7（待独立验收） |
| 2 | `REQ-009.4` | 采集批次 + 配额档案 + 原始存档层 + 去重与断点续跑 + 权限缺口清单与补齐 + token 留痕 | 报告逐条核对 AC-4.1~4.7；**实跑记录必需**（真实数据源与真实 token 属 mock 测不到的类别） |
| 3 | `REQ-009.1` | 按键执行器 + 任务生命周期 + 任务历史 | 报告逐条核对 AC-1.1~1.4 |
| 4 | `REQ-009.2` | 公司/产物/报告/图表/迭代台账视图 | 报告逐条核对 AC-2.1~2.5 |
| 收口 | `REQ-009` | README / CHANGELOG / `make gui` / 实跑 | 逐条核对 AC-1~AC-9，含「实跑记录」 |

**框架先行的意义**：`REQ-009.1` / `REQ-009.2` / `REQ-009.4` 都只写插件、数据层与前端 `kind`，
正好用来**验证框架**——如果实现它们时被迫改 `core/`，说明框架设计有洞，先修框架再加功能。

**采集排在按键执行器之前**（顺序 2 早于 3）：采集先于「给既有脚本配按钮」提供了本项目真正需要的取数
能力**存档化**；而它的界面本身就是框架扩展点的第一个真实用例（新插件 + 新数据集 + 新面板 + 新任务类型）。

> ✅ **预算前置条件已解决（2026-09-21）**：使用者选择「改 REQ-006 AC-7 并上调上限」，
> 预算已由 40 文件 / 1600 用例调整为 **48 文件 / 1800 用例**（留痕见 `scripts/test_scope.py` 注释、
> REQ-006 的 AC-7 变更记录与任务 T7）。本批 4 个测试文件 ≤ 64 用例 → 预计 **1586/1800（88%）、37/48**。
> **纪律不变**：接近新上限时仍先清理，不得靠上调解决。

## 15. 前端 shell 与 kind 渲染器

`app.js` 只做四件事：取 `/api/v1/nav` 与 `/api/v1/pages/{id}` → 渲染左侧导航 →
维护「当前选择」（公司 / 期次 / run，面板通过 `params[].source` 消费）→ 把每个面板交给
对应渲染器。**没有任何业务判断**。

- **`render: "server"` 的面板不需要前端渲染器**：服务端已经给了 HTML 片段，shell 直接插入
  （这是 §6.1 的取舍——让渲染在 CI 里可断言）；
- **`render: "client"` 的面板**才走 `registerPanelKind(kind, renderer)` 注册表；
  渲染器接口：`render(container, panelSpec, data)`；
- 未注册 `kind` → `kinds/fallback.js` 给出可读卡片（AC-3.3），**不白屏**；
- 图表用原生 Canvas：折线/柱状 + 悬停数值 + 图例；数据形状固定为 `{labels, series}`，
  将来加 K 线/热力图只加渲染器与 kind；
- 无构建步骤：`index.html` 用 `<script type="module">` 引入，改完刷新即生效。

已实现的静态资源：`index.html`（骨架）、`app.js`（shell + 面板分发）、
`style.css`、`kinds/chart.js`（canvas 折线/柱状）、`kinds/fallback.js`（降级卡片）。

## 16. 配置

`webui.config.json`（样例 `webui.config.sample.json` 入库，真实配置 gitignore）：

```jsonc
{ "host": "127.0.0.1",          // 只允许环回，填别的直接拒绝启动
  "port": 8765,                  // 0 = 随机端口（测试用）
  "open_browser": true,
  "output_root": "output",
  "cache_dir": "output/.webui_cache",
  "max_concurrent_jobs": 3,
  "job_log_tail": 200,
  "plugins": [] }                // 额外插件目录
```

环境变量覆盖：`WEBUI_PORT`、`WEBUI_CACHE_DIR`、`WEBUI_NO_BROWSER`、`WEBUI_PLUGINS`。
**不提供** `WEBUI_HOST` 的任意值——非环回一律拒绝（AC-3.1），避免「为了图方便暴露到局域网」
把安全边界悄悄放开。

## 17. 如何加一个新功能（cookbook）

以「加一张分红历史柱状图」为例，全程**不碰 `core/`**：

1. **加解析器**（若数据形状是新的）：`datastore/parsers/data_pack.py` 加 `def dividend_history(text)`,
   或在插件里就地实现小解析函数；
2. **注册数据集**：`registry.dataset(DatasetSpec(name="charts.dividend",
   sources=["data_pack_market.md"], parser="data_pack.dividend_history", parser_version=1))`
   → 缓存目录自动为它建分片，指纹变化自动失效；
3. **注册面板**：`registry.panel(PanelSpec(id="charts.dividend", kind="chart", title="分红历史",
   endpoint="/api/v1/companies/{dir}/charts/dividend", params=[Param("dir","company",True)],
   options={"chart":{"type":"bar"}}))`；
4. **挂到页面**：`registry.nav(NavItem(id="charts", panels=[... , "charts.dividend"]))`（或在插件里新开一页）；
5. **登记**：把插件模块名加进 `plugins/__init__.py` 的 `BUILTIN`（仓库外插件用 `--plugins` 免登记）；
6. **加测试**：`tests/test_webui_views.py` 加一条「给定假数据包 → 图表序列正确」+ 一条失败路径。

要新可视化类型（如热力图）就再加一步：`static/kinds/heatmap.js` + `registerPanelKind("heatmap", render)`。
要新按键就 `registry.command(CommandSpec(...))`。**以上任何一步都不需要改 `server.py`、`router.py`、
`app.js` 的分发逻辑或 `index.html`**——这正是 AC-9 断言的东西。

## 18. 演进路线（明确触发条件，不提前做）

| 演进 | 触发条件 | 做法 |
|------|----------|------|
| SQLite 索引 / 跨公司聚合查询 | run 数或公司数增长到「扫目录」明显变慢（如 >100 个公司目录或 >1000 个 run），或需要跨公司筛选排序 | `cache/index.sqlite`（stdlib `sqlite3`）作为**派生索引**，仍以 JSON 缓存为真相源，可随时重建 |
| 远程访问 / 鉴权 | 使用者明确要在局域网/多设备用 | **另开 REQ**：鉴权、CSRF、TLS、审计一起设计，不能靠改一个 host 配置 |
| 图表导出图片 | 使用者需要把图贴进报告 | Canvas `toDataURL()` + 一个静态路由，不引新依赖 |
| 图表类型扩展（K 线/热力图/雷达） | 使用者提需求 | 加 `kinds/*.js` + `registerPanelKind`，数据形状走 `{labels, series}` 或新的 `kind` 专属形状 |
| 后台定时采集 | 使用者要「每天自动更新」 | `registry.job_type` 加调度任务类型；仍受「联网只在显式动作」约束（需要后台任务时另开 REQ 讨论同意边界） |
| 主题/暗色模式 | 使用者要求 | CSS 变量 + 前端本地存储，不动内核 |

## 19. 开放问题

- 缓存是否要设「最大年龄」（如 7 天强制重算）？当前设计**不设**：本地产物是确定性文件，
  内容不变就没有重算的必要；年龄只用于显示新鲜度徽标。
- 面板参数选择器（公司/期次/run）的状态是否要进 URL（可分享/可刷新保持）？倾向于**进 URL**，
  但属 `REQ-009.2` 的实现细节，先记在这里。
- 多进程/多实例同时跑（如同一台机器开两个端口）：当前只保证单进程内的并发安全，
  写进演进路线（锁文件 + 只读降级）。
