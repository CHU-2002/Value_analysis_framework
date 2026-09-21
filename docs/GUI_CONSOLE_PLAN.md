# 图形化控制台设计（REQ-009）

> 本文写**怎么做**。要什么、做到什么程度以 [`docs/requirements/REQ-009-local-gui-console.md`](requirements/REQ-009-local-gui-console.md)
> 为准；两者不互相复制。开发流程见 [`docs/DEVELOPMENT.md`](DEVELOPMENT.md)，测试策略见 [`docs/TESTING.md`](TESTING.md)。

**阅读顺序**：§2 原则 → §3 架构 → §4 扩展点 → §5 插件模型 → §6 面板协议 → §7 API 契约 →
§8 数据层与缓存 → §9 远程边界 → §11 目录布局 → §17 如何加一个新功能（cookbook）。

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

```jsonc
// GET /api/v1/pages/charts → data
{
  "schema": "webui.page", "schema_version": "1.0",
  "id": "charts", "title": "图表", "group": "分析",
  "panels": [
    {
      "id": "charts.annual_price", "kind": "chart", "title": "年度股价走势",
      "size": "half",
      "endpoint": "/api/v1/companies/{dir}/charts/annual_price",
      "params": [{"name": "dir", "type": "company", "required": true, "source": "selection.company"}],
      "options": {"chart": {"type": "line", "x": "labels", "series": "series", "yFormat": "number"}},
      "meta": {"description": "来源：data_pack_market.md §11"}
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
├── render/
│   └── markdown_safe.py     # 先转义再渲染：标题/列表/表格/代码/链接
├── plugins/
│   ├── __init__.py          # BUILTIN 列表 + load_plugins(registry, extra_dirs)
│   ├── companies.py         # 公司列表 / 产物索引 / 报告渲染
│   ├── charts.py            # 3 类图（数据来自 datasets）
│   ├── commands.py          # 按键白名单（对应 AC-2 清单）
│   └── run_history.py       # 迭代台账时间线
├── static/
│   ├── index.html           # 只有骨架 + 挂载点
│   ├── app.js               # 启动、导航、选择器、面板分发（dispatchPanel）
│   ├── kinds/               # table.js / chart.js / timeline.js / markdown.js / form.js / jobs.js / stat.js / fallback.js
│   └── style.css
└── webui.config.sample.json # 配置样例（真实配置 webui.config.json 用 .gitignore 忽略）
```

`Makefile` 增加：`gui`（启动）、`gui-cache-clear`（清缓存）。
测试：`tests/test_webui_framework.py`、`tests/test_webui_server.py`、`tests/test_webui_views.py`。

## 12. 既有功能如何落到插件（首版四个插件）

| 插件 | 注册的东西 | 对应 AC |
|------|-----------|---------|
| `companies.py` | 数据集 `companies.index`（扫 `output/*/latest.json`、`record.json`）、`artifacts.index`（产物分类索引）、路由 `/api/v1/companies`、`/api/v1/companies/{dir}/artifacts`、`/api/v1/companies/{dir}/report`（按 id 取产物、服务端渲染 Markdown） | AC-2.1、AC-2.2、AC-2.3 |
| `charts.py` | 数据集 `charts.annual_price` / `charts.metrics` / `charts.revenue_profit`（三个 `data_pack` 解析器）+ 三个 `chart` 面板 + 路由 `/api/v1/companies/{dir}/charts/{name}` | AC-2.4、AC-3 |
| `run_history.py` | 数据集 `runs.timeline`（`history.jsonl` + `latest.json` + `run.json`）+ `timeline` 面板 + `stat` 面板（downstream stale）+ 路由 `/api/v1/companies/{dir}/runs` | AC-2.5、AC-5 |
| `commands.py` | 按键清单（AC-2 的 14 个入口 + `runs` 六子命令 + 定性管线四步）+ `form` / `jobs` 面板 + 路由 `/api/v1/commands`、`/api/v1/jobs*` | AC-2、AC-1.1~AC-1.4 |

`commands.py` 的参数表与脚本真实 CLI **双向校验**（表里的 flag 必须存在于脚本源码；脚本里
`required=True` 的 flag 必须在表里）——表与脚本不会各改各的，且校验是纯源码扫描，
不起子进程、不联网。

## 13. 测试计划与预算

| 文件 | 覆盖 | 用例预算 | 手法 |
|------|------|----------|------|
| `tests/test_webui_framework.py` | REQ-009.3：AC-3.1~AC-3.7 + **AC-9（演示插件 + 核心指纹）** | ≤ 28 | 起真实服务绑 `127.0.0.1:0`（随机端口）；`tmp_path` 造 `output/` 与演示插件；网络用 stub 禁掉；解析器用计数假解析器 |
| `tests/test_webui_server.py` | REQ-009.1：AC-1.1~AC-1.4 | ≤ 12 | `sys.executable -c` 假命令（不跑真实脚本、不联网）；断言不启动子进程的拒绝路径 |
| `tests/test_webui_views.py` | REQ-009.2：AC-2.1~AC-2.5 | ≤ 12 | `tmp_path` 造假公司目录与 `data_pack_market.md`；XSS 注入用例 |

预算核算：当前 1522/1600（余量 78）、33/40 文件；新增 3 文件 ≤ 52 用例 → 1574/1600、36/40，
**均在预算内，不需要上调上限**。测试纪律照旧：不联网、不写仓库 `output/`、不 `time.sleep`、
不依赖当前时间与随机顺序（时间通过注入固定值）。

## 14. 交付顺序与验收

| 顺序 | 编号 | 交付内容 | 独立验收 |
|------|------|----------|----------|
| 1 | `REQ-009.3` | 内核 + 注册表 + 面板协议 + 数据层缓存 + 契约 + 安全 + 演示插件 + 扩展文档 | 报告逐条核对 AC-3.1~3.7 |
| 2 | `REQ-009.1` | 按键执行器 + 任务生命周期 + 任务历史 | 报告逐条核对 AC-1.1~1.4 |
| 3 | `REQ-009.2` | 公司/产物/报告/图表/迭代台账视图 | 报告逐条核对 AC-2.1~2.5 |
| 收口 | `REQ-009` | README / CHANGELOG / `make gui` / 实跑 | 逐条核对 AC-1~AC-9，含「实跑记录」 |

**框架先行的意义**：`REQ-009.1` 与 `REQ-009.2` 都只写插件与前端 `kind`，
正好用来**验证框架**——如果实现它们时被迫改 `core/`，说明框架设计有洞，先修框架再加功能。

## 15. 前端 shell 与 kind 渲染器

`app.js` 只做四件事：取 `/api/v1/nav` 与 `/api/v1/pages/{id}` → 渲染左侧导航 →
维护「当前选择」（公司 / 期次 / run，面板通过 `params[].source` 消费）→ 把每个面板交给
`registry.getKind(kind)` 返回的渲染器。**没有任何业务判断**。

- 渲染器接口：`render(container, panelSpec, data, ctx)`；
- 未注册 `kind` → `fallback` 渲染器给出可读卡片（AC-3.3）；
- 图表用原生 Canvas：折线/柱状 + 悬停数值 + 图例；数据形状固定为 `{labels, series}`，
  将来加 K 线/热力图只加渲染器；
- 无构建步骤：`index.html` 用 `<script type="module">` 引入，改完刷新即生效。

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
