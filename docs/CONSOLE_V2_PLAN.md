# 控制台 2.0 设计（REQ-012）

> 本文写**怎么做**。要什么、做到什么程度以
> [`docs/requirements/REQ-012-console-v2.md`](requirements/REQ-012-console-v2.md) 为准；两者不互相复制。
> 既有的控制台设计见 [`docs/GUI_CONSOLE_PLAN.md`](GUI_CONSOLE_PLAN.md)（`REQ-009`）——**本文只写增量**，
> 不重复它的扩展点清单、面板协议与数据层设计。数据层的重构见 [`docs/DATA_LAYER_PLAN.md`](DATA_LAYER_PLAN.md)（`REQ-011`）。
> 产品侧的走查证据见 [`docs/proposals/2026-09-28-console-pm-review.md`](proposals/2026-09-28-console-pm-review.md)。

**阅读顺序**：§1 一句话方案 → §2 原则 → §3 目标 IA → §4 扩展点增量 → §5 动作模型 →
§6 任务中心 → §7 工作台 → §8 图表与口径 → §9 数据页 → §10 文案与错误 → §11 前端结构 →
§12 兼容与回退 → §13 测试与预算 → §14 交付顺序 → §15 反模式 → §16 演进 → §17 开放问题。

---

## 0. 实现状态（2026-10-09）

> 四片（`REQ-012.1`~`.4`）已全部落地，代码与测试在同一条改动里；需求侧的状态是 `implemented`
> （待独立验收推进 `verified`）。本节回答一个问题：**实现与本文设计差在哪**——
> 设计文档不改写成「事后诸葛亮」，偏差逐条摆出来，读的人自己判断哪边对。

| 片 | 落地情况 | 对应的设计节 |
|----|----------|--------------|
| `REQ-012.1` 公司上下文与信息架构 | 侧栏按 `group` 分 5 组、9 个页面：`工作台`(home)、`数据`(collect, data, companies)、`公司`(charts, report, runs)、`任务`(commands)、`分析`(agent)。`home` 用 `NavItem(default=True)` 作默认落地页（**核心不认识 `home` 这个 id**，谁声明谁生效）。`charts` / `report` / `runs` 声明 `requires=("selection.company",)`；URL 与选择器统一 ticker；缺上下文时 shell **不渲染面板、不发面板请求**，改显示「先选一家公司」空状态（`REQ-009` 的 `E2` 关闭，真实浏览器走查验证冷开 `#report` 零降级卡） | §3.1 / §3.2 / §3.3 / §4.1 |
| `REQ-012.2` 任务式动作层 | 四个内置动作：`company.update_analysis`（公司级，含**一个交接步** + `capture` 取 `runs resolve` 的 stdout + `runs finish` + `analysis_status`）、`data.pull_all`、`data.fill_gaps`（两者联网/花配额/需确认）、`data.rebuild`（离线/不花钱/无需确认）。消费者：`GET /api/v1/actions`（可用性快照）、`POST /api/v1/actions/{id}/run`、`POST /api/v1/jobs/{id}/retry`、`/continue`、`/abandon`。`core/jobs.py` 支持一个 job = 一串步骤，新增 `queued` / `awaiting_agent`（后者**不占并发槽**）、`failure_summary` / `progress` / `outputs`；旧 `submit(command_id, params)` 行为与载荷形状不变 | §4.4 / §5 / §6 |
| `REQ-012.3` 视图质量与口径修正 | 图表按 `basis` 分组（`annual` / `half` / `quarter`，默认年度），序列带 `basis` / `cumulative`，数据集回 `bases` / `basis_labels` / `basis_kind` / `labels_by_basis` / `series_by_basis`；`filter_basis` 按口径裁剪并**重算索引**；图表面板 `options.chart.toolbar = {basis, unit, export}`；图表数据集 `parser_version` 1→2（缓存键含版本，老缓存自动失效）。前端拆成 `kinds/chart_core.js`（比例尺/刻度/图例流动布局/DPR/按实测宽度抽稀/缺失值断开+空心点/悬停/导出 PNG 与 TSV）与 `kinds/chart.js`（只做分发 + 工具栏）；新增 `kinds/table.js`（搜索/排序/分页）与 `kinds/actions.js`（动作按钮 + 预检禁用 + 确认层 + 交接面板）；降级卡与错误卡改成「这块内容暂时看不到」+ 折叠的「技术细节」 | §4.2 / §4.3 / §8 / §10 |
| `REQ-012.4` 数据页 | `plugins/data_page.py` 三个数据面板（`data.universe` 清单+完备度、`data.gaps` 缺口下钻、`data.store` 存储概览）+ `data.actions`（联网 vs 离线两个动作并排）。数据全部读 **`datalayer` 的读接口**（`DataStore` / `Universe`），`pull_estimate` 直接调数据层自己的 `plan()` / `estimate()` | §9 |

### 0.1 与本文设计的偏差（如实登记）

| # | 设计里怎么写的 | 实际怎么落的 | 为什么 |
|---|----------------|--------------|--------|
| ① | §3.1 的侧栏里「公司」组下有一个**「概览」页**（这家公司的一句话状态 + 待办 + 直达动作） | **没有新建这个页面**。公司级信息由 `charts` 页与工作台的 `home.universe`（每行一家公司：显示名 / 数据期次与拉取时间 / 最近分析 / 状态 / 待办）承担 | 「公司概览」与工作台面板在数据与判据上几乎重合（同一批本地事实），做成两处会让「待办」有两份定义（`AC-2` 与 `AC-1` 的判据也会分叉）。归属：`home.universe` 是 `REQ-012.1` 的交付物 |
| ② | §3.1 的差异表写「`采集存档` → 拆成『数据获取 / 数据缺口 / 存储概览』」（读起来像**删掉** `collect`） | **新增 `data` 页（4 个面板）；`collect` 页面保留为兼容视图**（仍在「数据」组，`order=5`），旧入口继续可用 | `REQ-009.4` 已 `verified`，它的既有验收判据（批次 / 完备度 / 缺口 / 离线重建四个面板）**不许回退**；删掉旧视图等于回退已验收判据。新页与旧页并存，代价是导航里多一项 |
| ③ | §4.4 的动作注册点形态是 `registry.job_type(JobTypeSpec(...), runner=chain_runner)` | 最终形态：`registry.job_type(JobTypeSpec(...))`——**`runner` 可省**，默认落到内核的 `CHAIN_RUNNER`；`JobTypeSpec.kind` 默认 `"chain"`，且**本需求只实现 `chain` 一种**；`job_types()` 仍然是「类型 id 的元组」（实测回 `('chain',)`，`REQ-009.3` 的诊断快照语义不变） | 动作**只编排既有按键**，业务实现在按键里、不在动作里，所以「省掉 runner」不会缺实现；把它做成默认值而不是必填，插件声明更短，也让「动作 = 命令以外的任务类型」这句话在代码里成立 |
| ④ | §4.5 的表只说 `core/jobs.py` 要动；§6 提到「`max_concurrent_jobs` 满了以后的排队」但没定上限从哪来 | 队列上限是**新配置项** `max_queued_jobs`（默认 **20**，`0` 等于「不排队、超限即拒」）。超限语义因此从「一律 429」变成「**先排队**，队列也满了才拒」；`REQ-009.1` 的既有断言（`AC-1.3` 的 429）用 `max_queued_jobs=0` 复现 | `AC-5` 要求任务中心**能列出队列**，「看不到的等待」比「明确拒绝」更糟；上限与并发上限一样是运行时配置，不该硬编码在调度器里。默认 20 是「够用且不会把内存塞爆」的粗估 |
| ⑤ | §8.1 把「口径从哪里来」列成两阶段（阶段 1 从列标签解析、阶段 2 从仓取 `period_type`/`cumulative`） | 落地为：**服务端按 `basis` 分组 + 按口径裁剪（`filter_basis` 重算索引），前端只画**。`basis_kind` 区分累计/单期；数据集 `parser_version` 升到 2 让老缓存失效 | 阶段 2 的前提（`REQ-011.3` 的结构化口径）**已经交付**，但本期仍走标签解析——把口径判断收口在**一处**（服务端），前端不做任何口径推断，于是「不混口径」这条判据是可断言的（`bases` / `series_by_basis` / `basis_kind` 直接进测试），而不是靠人眼看图。切换数据源（阶段 2）因此只改那一处解析、不动前端 |
| ⑥ | §4.2 的通用表格能力写的是 `options.table`（`{"search": true, "sort": true, "page": 50}`） | 落成**服务端产出契约标记 + `kinds/table.js`**：`render_table` 输出 `data-table-controls="search,sort,page"` / `data-page-size` / `data-sort-key` / `data-sort-type`，客户端按这些属性接管筛选/排序/分页 | 契约放服务端，CI 就能断言「声明了 search 的表就有搜索控件」（`AC-3.4` 因此可判定）；真实交互由 `AC-12` 的走查覆盖——与 `REQ-009` 处理 Canvas 的方式一致（服务端管契约，走查管行为） |

### 0.2 §17 开放问题的实际结论

| §17 | 问题 | 实际结论 |
|-----|------|----------|
| §17.1 | `AC-1` 的页面范围（估值/买卖计划） | 已裁决（2026-09-28，owner）：**收窄**为「概览 / 图表 / 报告 / 迭代记录」，变更记录见 `REQ-012` 的「## 变更记录」。实现按收窄后的范围落，机制仍是通用的（新增公司级页面只要声明 `requires=("selection.company",)`） |
| §17.2 | 端点是否也改成 ticker | 已裁决（2026-09-28，owner）：**改**。路径参数语义从「公司目录名」变为「公司标识」，ticker 规范 + 目录名兼容别名；`REQ-009` 的 2026-09-28 变更记录在案，实现见 `plugins/companies.py` |
| §17.3 | 工作台「数据过期」怎么判定 | 按 §7 的表落地：只用**本地事实**（仓里的期次/拉取时间、`latest.json`、任务历史）判定，不引入「最新可用期次」这类需要外部日历的知识。留待真实使用反馈 |
| §17.4 | `actions` 是否算新 kind、要不要服务端兜底 | `actions` 进 `CLIENT_KINDS`（`render/panels.py`），**没有**做服务端渲染的兜底列表——旧前端（浏览器缓存）走 `fallback` 降级卡，这是 `REQ-009.3` 的 `AC-3.3` 明确承诺的行为（未知 kind 一律降级为可读卡片） |
| §17.5 | 多步编排的参数捕获表达能力 | **做了**：`CommandStep.capture`（`{"占位符名": "正则"}`）从该步的 stdout 取值。真实用例就是 `company.update_analysis` 里 `runs resolve` 的输出 → `{run_dir}`（`capture={"run_dir": r"^(\S+)$"}`），随后 `runs finish` 用它。**没有**收窄成「只编排参数互不依赖的步」 |
| §17.6 | `AC-2.4` 与「人机交接」的冲突 | 已裁决（2026-09-28，owner）：**修订 `AC-2.4`**，把交接写成合法形态，变更记录见 `REQ-012` 的「## 变更记录」。实现见 §5.5：`HumanStep` 声明可复制的 slash 命令、已解析路径与**产物断言**；服务端在「我跑完了，继续」后校验产物**存在且比该步开始时新**（真实墙钟 + 进入交接前的 mtime 快照，取更严者），不通过就停在原地并列出缺什么 |
| §17.7 | 人机交接的产物校验强度（要不要比对 `run_manifest.json`） | 本期用「存在 + 比该步开始新」，并且**两个基准取更严的一个**：只看快照会让「一开始就存在但没动过」的文件蒙混过关（实现过程中真的漏过，已修）。更严的清单比对（`REQ-003` 的 `run_manifest.json`）留作后续演进的触发条件 |

---

## 1. 一句话方案

**给框架补三样缺的东西——导航层级、可空的「当前公司」上下文、动作（Action）注册点——
然后把控制台从「按键目录」改造成「任务式界面」：用户看到的是事，不是命令。**

判断标准只有一条：**用户需不需要知道内部结构**。需要 → 设计没做完。

## 2. 设计原则

| # | 原则 | 为什么（不这么做会怎样） |
|---|------|--------------------------|
| U1 | **上下文优先于页面** | 公司是这个项目的主语。把公司做成平级页面，用户就得在脑子里记住「我在看哪家」，还依赖页面之间的链接传递状态（`E2` 的根因） |
| U2 | **面向事，不面向命令** | 23 个脚本入口是**实现**，不是产品结构。用户心里只有 5~6 件事 |
| U3 | **技术细节可查但不占主视觉** | 真实 argv / 退出码 / 日志是这套工具最值钱的部分（审计价值），**一个字都不能删**——收进「详情」抽屉 |
| U4 | **预检优于报错** | 前置不满足时按钮禁用 + 说明原因，比「点了才报错」好；执行前说清「会发生什么」比事后解释好 |
| U5 | **状态要可见** | 数据多旧、任务跑没跑完、上次为什么失败，都应该是界面告诉用户，而不是让用户猜 |
| U6 | **扩展点优先，核心最后手段** | `REQ-009.3` 的 `AC-9` 是硬判据。新增能力先问「能不能加一个注册点」，实在不行才动 `core/*`，并留痕（§4.5） |
| U7 | **零新增依赖不变** | 手写前端 + Python 标准库。当前的问题不是技术栈问题，换框架换不来 IA |
| U8 | **不回退 `REQ-009` 的已验收判据** | `AC-1`~`AC-9` 一条都不改（§12 逐条对照）；改到已验收产出的片，收口时重跑既有用例与走查 |
| U9 | **复用既有权威实现，不新造第二套** | 仓库里已经有 `scripts/periods.py`（期次）、`jobs.py::build_argv`（参数→命令行）、`ENTRIES`（按键白名单）、六类注册点。加能力先问「能不能用既有的那一处」，再问「能不能加一个注册点」；两套实现必然漂移 |

## 3. 目标信息架构

### 3.1 侧栏结构（`REQ-012.1` 交付）

```
工作台                      ← 首页（默认落地页，替代今天的「采集存档」）
数据
  ├ 数据获取                ← REQ-012.4（依赖 REQ-011）：名单 / 一键拉全 / 离线重建
  ├ 数据缺口                ← REQ-012.4
  └ 存储概览                ← REQ-012.4（仓规模、各 result 计数、最近批次）
公司  ▸ 需要「当前公司」上下文
  ├ 概览                    ← REQ-012.1 新增：这家公司的一句话状态 + 待办 + 直达动作
  ├ 图表                    ← 今天的 charts 页，接入上下文
  ├ 报告                    ← 今天的 report 页
  └ 迭代记录                ← 今天的 runs 页
分析（跨公司）
  └ 批量分析                ← 演进项（触发条件见 §16）
任务                        ← 今天的「按键」页的去向（§5/§6）
```

> ⚠️ **实现与上图的两处偏差**（详见 §0.1）：**① 「公司 · 概览」这一格没有落地**——公司级信息
> 并入工作台的 `home.universe`（每行一家公司：显示名 / 数据期次与拉取时间 / 最近分析 / 状态 / 待办）
> 与 `charts` 页，理由见 §0.1 第 ① 条；**② 「采集存档」不是被拆掉而是并存**——
> 新开了 `data` 页（4 个面板），`collect` 页面**保留**为兼容视图（`REQ-009.4` 的既有验收判据不许回退）。

与今天的差异：

| 今天 | 2.0 | 为什么 |
|------|-----|--------|
| `数据 / 浏览 / 执行` 三个分组 | `工作台 / 数据 / 公司 / 分析 / 任务` | 分组名是**用户的任务**，不是代码分层 |
| `公司` 与 `图表/报告/迭代记录` 平级 | 公司级页面收进「公司」组，共享上下文 | §3.2 |
| `按键` | `任务`（动作入口在**对象上**，任务页只管队列/历史） | 动作属于它所作用的对象（公司页的「更新分析」、数据页的「拉取」） |
| `采集存档` | 拆成「数据获取 / 数据缺口 / 存储概览」 | 「采集存档」是内部叫法；用户要的是「我的数据够不够、缺什么」 |

> **范围注记**：`估值` / `买卖计划` 两个页面**不在本期**（属 `REQ-010` 之后的演进，§16）。
> `REQ-012.1` 的「公司概览」页会显示 `REQ-010` 的价值报告 `stale`/`unavailable` 状态，
> 但不新建这两个页面。⚠️ `REQ-012` 的 `AC-1` 括号里列了这两个页面名——**这是 AC 写得比范围宽**，
> 需要 owner 定（§17 开放问题 1）。

### 3.2 当前公司：可空的全局上下文

```
                 ┌──────────────────────────────────────────────┐
topbar:  [ 公司：600887 伊利股份  ▾ ]  [ 搜索 / 切换 ]        ← 全局，任何页面都在
                 └──────────────────────────────────────────────┘
```

- **来源**：URL 的 `company=<ticker>` 参数（§3.3），初始值来自上次选择（`localStorage`，
  键 `webui.selection.company`），两者不一致时 **URL 优先**。
- **为空是合法状态**：公司级页面显示空状态卡片
  「先选一家公司 →（按钮：选公司 / 去工作台）」，**不是** `BAD_REQUEST` 降级卡（关掉 `E2`）。
- **服务端是权威**：`ticker → 公司目录` 的解析在服务端（`companies` 数据集已有的
  `subject.ticker`），前端只传 ticker。解析不到时返回**正常空状态**（不是 4xx）。
- **跨公司页面忽略上下文**：数据页/任务页/工作台不消费 `company`（面板不声明
  `source="selection.company"` 即自动忽略——这条机制 `REQ-009.3` 的面板协议已经支持，不用新增）。
- **可空但不必唯一**：`selection` 已经是一袋查询参数（`app.js`），本期仍是单值；
  未来多公司对比时把 `company` 换成重复参数即可（数据模型上不写死「只有一个」）。

### 3.3 URL 契约

```
#<page>                          → 页面，无上下文
#<page>?company=<ticker>          → 公司级页面
#report?company=600887.SH&id=<artifact_id>
#runs?company=600887.SH&run=<run_id>
#charts?company=600887.SH&period=20260630&basis=annual   ← basis 见 §8
```

规则：

1. `company` 的值统一是 **ticker**（`600887.SH`），不是目录名（`600887_伊利`）——配合 `AC-9`；
2. **端点也接受 ticker（owner 2026-09-28 决定）**：`/api/v1/companies/{标量}/report|artifacts|charts|runs`
   的路径参数语义从「公司目录名」改为「公司标识」，**ticker 为规范形式**；
   服务端先按 ticker 解析（`companies.index` 数据集里的 `subject.ticker`），失败再当目录名——
   因此**旧的目录名链接继续可用**。这是 `REQ-009.2` 的 `AC-2.2` / `AC-2.4` / `AC-2.5` 的需求变更，
   已按 §8 在 `REQ-009` 留变更记录（含「关联测试同步更新」的做法）；
   解析前仍必须过 `REQ-009.3` 的 `AC-3.7` jail（**这是安全边界，不因参数语义变化而放宽**）；
3. 切换公司 = 改 hash 的一个参数，**不重新加载页面**（`hashchange` 已经接好）；
4. 刷新 / 前进后退 / 复制链接给别人（本机）都得到同一视图；
5. 未知 page → 落到工作台并提示（今天的行为是 `items[0]`，会落到采集存档）。

> 面板里所有站内链接（`companies.list` 的 `href`、产物表的 `href`、时间线的 `link`）
> 一并改为带 ticker；`report` 面板的 `company` 参数随之从「目录名」变成 ticker，
> `company_base()` 的解析顺序（ticker → 目录名）是**唯一**一处改动点，`render_table` 的
> `href` 白名单（只允许 `#` 与 `/`）不变。

## 4. 框架扩展点增量（`REQ-012` 的地基改动）

按 `REQ-009.3` 的先例：**扩展点是框架切片，先交付、先验收，业务切片再消费**。
本需求把它分散到两个业务切片里（不另开 `.5`）：`.1` 交付导航与上下文增量，`.2` 交付动作增量。
每次动 `core/*` 或 `static/app.js` 都要同步核心指纹并留痕（§4.5）。

### 4.1 导航层级与上下文依赖（`NavItem` 增量）

```python
# core/models.py —— NavItem 只加两个可选字段（其余字段与 to_json 输出保持现状，
# 见 core/models.py:43；description / group / order 今天已有，不要重复加）
@dataclass(frozen=True)
class NavItem:
    id: str
    title: str
    group: str = ""
    order: int = 100
    panels: tuple = ()
    description: str = ""
    children: tuple = ()          # 【新增】子导航项（元素同样是 NavItem）
    requires: tuple = ()          # 【新增】上下文需求，如 ("selection.company",)
```

- 注册点 `registry.nav(...)` 的签名不变（同 id 冲突仍然致命），只是 `NavItem` 多了字段；
- 核心只做**结构与序列化**（`/api/v1/nav` 返回树 + `requires`），**不解释业务**——`requires` 是字符串，
  由前端 shell 比对 `selection` 决定「要不要显示空状态」；
- `AC-9` 的「演示插件不改核心」判据继续成立：新增页面仍然只加插件文件。

### 4.2 通用表格能力（`options.table`）

服务端渲染的表格（`render/panels.py::render_table`）加一层**声明式控件**：

```json
{"kind": "table", "options": {"table": {"search": true, "sort": true, "page": 50}}}
```

- 服务端产出控件标记与 `data-*` 契约（`data-table-search`、`data-sort-key`、`data-page-size`），
  **因此 CI 能断言**「声明了 search 的表就有搜索控件」；
- 前端 `kinds/table.js`（新增）接管筛选/排序/分页（纯本地，数据已在 DOM）——
  真实行为由 `AC-12` 的浏览器走查覆盖（与 `REQ-009` 处理 Canvas 的方式一致：服务端管契约，走查管行为）；
- 大表（产物索引、批次）默认 `page: 50`，并显示「显示 50 / 共 213 行」。

### 4.3 图表公共能力（`chart_core.js`）

把 `kinds/chart.js` 拆成两层：

```
static/kinds/chart_core.js   比例尺 / 刻度 / 图例 / DPR 适配 / 悬停 / 导出   ← 公共，可测
static/kinds/chart.js        kind=chart 的渲染器：读 options.chart（type/x/series/basis）
```

- 新增图型（`bars`、`heatmap`…）只写渲染器、复用 `chart_core`；
- `AC-7` 的绘制质量要求全部落在 `chart_core`，一处修好、所有图受益。

### 4.4 动作（Action）：**接上既有的第 6 类注册点，不新增第 7 类**（`REQ-012.2` 交付）

`REQ-009.3` 的 `AC-3.2` 的原话是「框架提供**六类**注册点（导航/页面、面板、API 路由、数据集、按键、**任务类型**）」，
而第六类 `registry.job_type(kind, runner)` 今天**只做登记与诊断暴露、从不被执行**
（`core/registry.py:92` 存进 `self._job_types`，`core/routes.py:176` 只是在诊断快照里列出它，
`JobRunner.submit()` 只认 `command_id`）——
它本来就是为「一键跑『取数 → 解析 → 分析』串」预留的位置（见 `GUI_CONSOLE_PLAN.md` §4 的「典型用途」列）。

所以本设计的做法是：

> **把「动作」声明在既有的第 6 类注册点上，并把它的消费者接上；不新增第 7 类注册点。**

理由：① 不触碰 `AC-3.2` 的「六类」措辞，`REQ-009.3` 的结论原样成立；
② 动作的本质就是「命令以外的任务类型」，与这个注册点的定义一致；
③ 少一类注册点，`AC-9` 的「加功能不改核心」判据覆盖面积不变而更集中。

```python
# core/models.py：把第 6 类注册点从「裸 callable」富化成显式声明
@dataclass(frozen=True)
class JobTypeSpec:
    kind: str                     # "chain"（本需求唯一实现的类型）
    id: str                       # "company.update_analysis"
    title: str                    # 面向用户的话：「更新这家公司的分析」
    description: str = ""
    group: str = "分析"
    requires: tuple = ()          # ("selection.company",) → 缺上下文则禁用 + 理由
    steps: tuple = ()             # 有序步骤：既有按键 id + 参数绑定 + 人或 agent 的交接点
    effects: dict = field(default_factory=dict)   # {"network":…, "quota":…, "writes":(…)}
    danger: bool = False

# core/registry.py：签名向后兼容（今天只有 job_type(kind, runner)）
registry.job_type(JobTypeSpec(...), runner=chain_runner)
```

一句话：**动作 = 意图 + 上下文绑定 + 预检 + 若干既有命令 + （必要时）人机交接点**。
业务实现一行都不搬进面板；参数→命令行的转换仍然是 `core/jobs.py::build_argv` 这唯一一处。

**怎么嵌进去（不改既有调用方）**：

| 事项 | 现状 | 本设计的做法 |
|------|------|--------------|
| `job_type` 的签名 | `registry.job_type(kind: str, runner)`（`core/registry.py:92`） | 改成 `job_type(kind_or_spec, runner=None)`：传 `str` 时行为与今天**完全一致**（`test_webui_framework.py:307` 的 `job_type("batch", lambda…)`、`assert job_types() == ("batch",)` 必须继续绿）；传 `JobTypeSpec` 时登记富声明 |
| `job_types()` 的返回值 | 类型名元组，被 `core/routes.py:176` 的诊断快照列出 | 保持「类型 id 的元组」语义；富声明的 `id` 另由 `job_type_spec(id)` 查询 |
| `JobRunner` 怎么拿到动作 | `JobRunner(config, spec_lookup=registry.command_spec)`（`__main__.py:40`） | 增加一个**可选**的 `job_lookup=registry.job_type_spec`；不传时行为与今天一致（只认按键） |
| 现有按键路径 | `POST /api/v1/jobs {command, params}` → `submit(command_id)` | **一字不改**；动作走 `POST /api/v1/jobs {action, params}`（同一路由按字段分发），或新增 `POST /api/v1/actions/{id}/run`——取其一，由 `.2` 定 |
| 诊断 | `_registry_snapshot` 列出 job_types | 增列「动作 id + requires + effects」，让「谁注册了什么」在诊断里仍然可查 |

### 4.5 动了核心怎么留痕

| 文件 | 什么时候动 | 怎么留痕 |
|------|-----------|----------|
| `core/models.py`（`NavItem` 加字段 / 新增 `JobTypeSpec`） | `.1` / `.2` | 同步 `tests/fixtures/webui_core_fingerprint.json`；在需求「## 备注」写清是「扩展面增量」还是「缺陷修复」 |
| `core/registry.py`（`job_type` 富化 + 消费者接线） | `.2` | 同上；断言「演示插件注册一个 job_type 就能用」 |
| `core/jobs.py`（`Job.steps` 与多步运行） | `.2` | 同上；既有 `submit(command_id)` 行为与测试**不许变** |
| `static/app.js`（上下文选择器 + nav 树） | `.1` | 同上；**并请独立评审者确认是否影响 `REQ-009.3` 的既有结论**（实现者不自判） |
| `core/security.py`（新增允许根） | `.4` 才可能 | 只在 `REQ-012.4` 需要读仓时；倾向不改——GUI 走 `datalayer` 的读接口，不走文件路径 |

## 5. 动作模型

### 5.1 描述与绑定

| 字段 | 作用 |
|------|------|
| `id` / `title` / `description` / `group` | 面向用户（`title` 进扫描禁则，§10.1） |
| `requires` | 上下文需求；缺了就禁用并给出「缺什么」 |
| `steps` | 有序步骤：既有**按键**（`CommandStep`，含参数 `bind`）或**人机交接点**（`HumanStep`，§5.5） |
| `effects` | `network` / `quota` / `writes`——由**服务端**计算，供预检与确认文案使用 |
| `danger` | 是否需要显式确认（弹窗） |
| `preflight` | 可选的自定义检查函数（如「仓里有没有这家公司」） |

一个**真实**的动作声明（不是示意），注意中间那一步：

```python
registry.job_type(JobTypeSpec(
    kind="chain",
    id="company.update_analysis",
    title="更新这家公司的分析",                    # 面向用户的话
    description="按最新财报期跑一次增量更新，产出新的分析 run 与变化报告。",
    group="分析",
    requires=("selection.company",),              # 缺上下文 → 禁用 + 理由
    steps=(
        # ① 确定性前段：GUI 能跑
        CommandStep("runs_new",        bind={"company_dir": "{company_dir}", "ticker": "{ticker}"}),
        CommandStep("download_report", bind={"stock_code": "{ticker}", "report_type": "auto"}),
        CommandStep("pdf_preprocessor", bind={"pdf": "{latest_pdf}", "output": "{run_dir}/inputs"}),
        CommandStep("results_prepare", bind={"output_dir": "{run_dir}", "ticker": "{ticker}"}),
        # ② 人机交接：模块 Agent 只能在 Claude Code / OpenCode 里跑（§5.5）
        HumanStep(title="在 Claude Code 里跑模块分析",
                  slash="/update-analysis {ticker}",
                  expects=("{run_dir}/modules/*/result.json", "{run_dir}/synthesis/result.json")),
        # ③ 确定性后段：GUI 能跑
        CommandStep("results_reconcile", bind={"input": "{run_dir}/modules"}),
        CommandStep("results_synthesis", bind={"input": "{run_dir}/contexts"}),
        CommandStep("results_resolve",   bind={"output_dir": "{company_dir}"}),
        CommandStep("runs_finish",       bind={"company_dir": "{company_dir}", "run_dir": "{run_dir}"}),
    ),
    effects={"network": True, "quota": False, "writes": ("run", "change_report")},
    danger=False,
), runner=chain_runner)
```

业务实现一行都不搬进面板：`steps` 引用的全是 `commands.py::ENTRIES` 里**已有的**按键。

### 5.2 预检（`AC-4`）

`GET /api/v1/actions` 返回每个动作的**可用性快照**（针对当前上下文）：

```json
{"id": "company.update_analysis", "enabled": false,
 "blockers": ["还没选公司"],
 "effects": {"network": false, "quota": false, "writes": ["run", "report"]}}
```

- 禁用理由必须是用户语言（「还没选公司」「没有配置 TUSHARE_TOKEN」「上游产物缺失：先跑一次取数」）；
- 预估（联网动作）：调用量/大致耗时来自 `REQ-011` 的预估接口（数据页用），
  公司级动作给「会写哪些产物」；
- **前端不做判断**：预检结果全部由服务端算，前端只负责禁用与展示——这样预检可以被 CI 断言。

### 5.3 确认（`AC-4`）

危险/花钱动作的确认文案由服务端给（`confirm: {title, body, confirm_label}`），
前端弹一个确认层。取消 = 不提交，**不产生任何副作用**（不发 `POST /api/v1/jobs`）。

### 5.4 多步编排

- 一个动作 = **一个任务**（一个 job），job 内部按 `steps` 顺序执行；
- 每步是一条既有命令的子进程（复用 `core/jobs.py` 的执行与日志能力）；
- 进度 = `已完成步数 / 总步数` + 当前步的日志尾部；
- 某步失败 → 任务 `failed`，**不继续后续步**，并说明「第 3 步失败，前 2 步的产物已落盘，可重试」；
- 取消 → 终止当前子进程，任务 `cancelled`，已完成步保留（幂等重跑由 `REQ-011`/既有 run-store 保障）。

> 这需要 `core/jobs.py` 从「一个 job = 一条命令」扩展到「一个 job = 一串命令」。
> 属核心扩展面改动，留痕见 §4.5；后端编排的理由是「离开页面任务继续」（`AC-5`）——
> 前端串联做不到这一点。

### 5.5 人机交接（handoff）：为什么「完整价值分析」不能是一个自动链

**这是本设计里最重要的一条边界，也是现状最容易让人误解的地方。**

这个项目的「分析」由两段拼成，中间隔着一次 **LLM 调用**：

```
确定性前段（Python，GUI 能跑）        人/agent 段（Claude Code / OpenCode）      确定性后段（Python，GUI 能跑）
取数 → 下载 PDF → 解析章节      →     模块 Agent（D1~D7）+ Final Synthesis  →  reconcile → synthesis → resolve
prepare（构建 evidence/contexts）     写出 modules/*/result.json、            final_synthesis → qualitative_report.md
                                      synthesis/result.json
```

- **仓库里没有任何程序化的 LLM 入口**：没有 SDK、没有 API key、没有 HTTP 客户端调用模型；
  `.claude/commands/*.md` 与 `.opencode/commands/*.md` 是**给 agent  CLI 读的提示词文件**，
  模块 Agent 与 Final Synthesis Agent 由那个 CLI 驱动（`ARCHITECTURE.md` §2 的
  「证据优先的定性分析（LLM + 确定性脚手架）」）。
- 所以 GUI 今天能做的正好是**两端的确定性步骤**（`results_prepare` / `results_reconcile` /
  `results_synthesis` / `results_resolve` + `value_analysis_engine`），
  **中间那段必须在 agent CLI 里跑**——而今天的界面上**没有任何地方告诉用户这件事**：
  点完「定性管线 · prepare」之后，页面不会说「接下来去 Claude Code 跑 `/business-analysis`」，
  也不告诉你要跑哪个命令、跑完回来点哪个。这是 `REQ-012.2` 要补的洞。

因此 `steps` 里允许出现第五种步骤类型：

```python
HumanStep(                              # 也叫 agent 步骤；不产生子进程
    title="在 Claude Code 里跑模块分析",       # 面向用户
    slash="/business-analysis {ticker}",     # 可一键复制的命令（服务端渲染「复制」按钮）
    hint="模块 Agent 需要 LLM；跑完回到这个页面点「我跑完了」。",
    expects=(                                # 服务端可校验的产物断言（否则无从判断"跑完了"）
        "{run_dir}/modules/*/result.json",
        "{run_dir}/synthesis/result.json",
    ),
)
```

交接的实际行为：

1. 任务执行到 `HumanStep` → 状态置 **`awaiting_agent`**（`JobRunner` 新增的终态之一，
   与 `running` 区分：它不占并发槽、不需要轮询子进程）；
2. 页面显示：步骤说明 + **可复制的 slash 命令** + 已解析好的路径 + 「我跑完了，继续」按钮 +
   「放弃」按钮；
3. 点「继续」→ 服务端**校验 `expects` 里的产物存在且比该步开始时新**
   （用 mtime / sha 对比，防止「没跑就点」）→ 通过则继续后续确定性步骤，
   不通过则留在 `awaiting_agent` 并说明缺哪一项；
4. 整个动作在界面上仍然是**一件事**、一条进度（`3/6 · 等待你在 Claude Code 里跑模块分析`），
   只是它中间有一次合法的暂停。

**这张图也回答了「GUI 支不支持生成完整 value analysis」**：不支持「一键出报告」，
因为最后一步（读 `strategies/value/*` 提示词、结合 `value_computed.md` 写出
`{company}_{code}_价值分析报告.md`）是 agent 步骤。GUI 能做的是一条
**「准备好一切 → 交接给 agent → 校验产物 → 继续收口」**的链。

> ⚠️ `REQ-012` 的 `AC-2.4` 现在写的是「多步流程（如「更新这家公司的分析」）由一次动作编排完成」——
> 这句话与上面的现实冲突（那个例子的中间必须有人/agent 步骤）。需要 owner 定（§17 开放问题 6）：
> ① 修订 `AC-2.4` 措辞，把「人机交接」写成合法形态（留变更记录）；
> ② 或把示例换成纯确定性流程（如「从仓重建派生数据」），另立一条 AC 描述交接；
> ③ 或要求支持程序化调用 agent CLI（那是**新能力**：成本、并发、凭据、审计都要重新设计，必须另开 REQ）。

## 6. 任务中心（`AC-5`）

`#tasks` 页三块：

| 区块 | 内容 |
|------|------|
| 进行中 | 当前 job：动作名、进度 `2/6`、当前步、取消按钮、**折叠的**日志 |
| 队列 | 待执行（`max_concurrent_jobs` 满了以后的排队） |
| 历史 | 倒序，每条：动作名 / 状态 / 退出码 / 用时 / 产出链接；失败时**自动展开**原因与日志尾部 |

- 失败原因：**任务记录里存一份「人话摘要」**（`failure_summary`），规则 = 退出码 + 最后一条
  匹配到的已知错误（`NO_TOKEN` / `BATCH_RUNNING` / 常见 Python 异常的最外层信息）+ 「下一步」；
  原始日志永远在详情里（不丢信息）；
- 产出链接：动作声明 `writes`，任务完成后由服务端解析成实际产物（`#report?company=…&id=…`）；
- 轮询：沿用现有 2s 轮询（`kinds/jobs.js`），但只在**有 running 任务**时轮询
  （今天 `setInterval` 在无 running 时会清掉，保持）。

## 7. 工作台（`AC-2`）

`#home` 页三个面板（全部服务端可断言）：

| 面板 | kind | 内容 |
|------|------|------|
| `home.universe` | `table`（`options.table.search/sort`） | 每行一家公司：显示名 / 数据截止期次 / 数据拉取时间 / 最近分析 run / 状态徽标 / 待办 |
| `home.todo` | `stat` 或新 `list` | 待办汇总：`3 家还没拉数据`、`2 家的分析落后于数据`、`1 家下游过期`、`上次批次有 2 个缺口` |
| `home.actions` | `actions`（新 kind） | 直达动作：拉取全部数据 / 更新这家公司 / 从仓重建派生数据 |

**待办判定规则**（全部本地可判定，不依赖外部日历）：

| 待办 | 判定 |
|------|------|
| 还没拉数据 | 仓内该标的无 `result in ('ok','empty')` 记录（`REQ-012.4` 之前：`output/<公司>/data_pack_market.md` 不存在） |
| 数据有缺口 | 最近批次或仓内该标的的缺口非空 |
| 分析落后于数据 | 仓内最新 `period_report.period` > 公司 `latest.json.primary_period` |
| 下游过期 | `record.json.downstream.stale == true`（既有字段，`REQ-010` 会扩展价值报告的 stale） |
| 上次任务失败 | 该公司的最近一个 job 状态为 `failed` |

空库（全新环境）时 `home.universe` 显示引导卡片：「还没有公司数据 → ① 加自选股 ② 拉取数据」，
不是空表；`output/` 里已有产物而仓为空时，提示「发现 output/ 下已有 N 家公司 → 导入为自选股」。

## 8. 图表与口径（`AC-7`）

### 8.1 口径从哪里来（设计上的关键判断）

走查 `P-1` 的根因是「把年度与单季画在同一根轴上」。修复**不必须**等 `REQ-011`：

| 阶段 | 口径来源 | 适用 |
|------|----------|------|
| 阶段 1（`REQ-012.3`，不依赖 `REQ-011`） | 从**列标签**解析：`2025` → 年度；`2025H1` → 半年；`2025Q1` → 单季 | `data_pack_market.md` 的表头已经无歧义地编码了口径 |
| 阶段 2（`REQ-011.3` 交付后，可选切换） | 从**仓**取 `period_type` / `cumulative`（`REQ-011` 的 `AC-4`） | 不再解析标签；标签解析降级为兼容路径 |

阶段 1 让 `REQ-012.3` **可以独立交付**，不必等数据层；阶段 2 是收口（避免长期维护两套口径判断）。
`REQ-012` 的「## 背景」里「修复它需要 `REQ-011` 的结构化口径」这句据此修正为「需要让口径成为一等信息」。

### 8.2 绘制契约（`chart_core.js`）

| 要求（`AC-7`） | 做法 |
|----------------|------|
| 不混口径 | 数据按口径分组 → 渲染成**多组序列**（年度一组、单季一组），或由 `basis` 参数只画一组；默认 `basis=annual` |
| 单位与口径标注 | 图标题旁标 `单位：百万元`、`口径：累计/单期`（来自数据集 `unit` 与新增的 `basis` 字段） |
| 不模糊 | `canvas.width = cssWidth * devicePixelRatio`，`ctx.scale(dpr, dpr)`；`ResizeObserver` 跟随容器宽度 |
| 标签不截断/不重叠 | 按实测文本宽度抽稀（不是「隔一项画一个」）+ 右侧留出末标签宽度的一半 |
| 图例换行 | 按容器宽度流动布局，不按固定 110px 步进 |
| 缺失值 | 折线断开 + 在悬停提示里写 `—`，并在图注写明「缺失值不连线」（沿用既有语义） |
| 导出 | `canvas.toDataURL('image/png')` 下载 + 「复制数据（TSV）」按钮；无新依赖 |

### 8.3 与 `REQ-009.2` 的关系

`AC-2.4` 要求「≥3 组序列 + 命中缓存 + 格式变化给带原因的错误」——这些**必须继续成立**。
本片只改「怎么画」与「怎么分组」，不改「数据从哪来」（阶段 1 仍读 `data_pack_market.md`）。
收口时重跑 `tests/test_webui_views.py` 与 `scripts/gui_walkthrough.py`（§12）。

## 9. 数据页（`REQ-012.4`，依赖 `REQ-011.1`/`.2`）

| 页面 | 内容 | 数据来源 |
|------|------|----------|
| 数据获取 | 自选股清单（可增删启停）/ 目标与调用量预览 / 「拉取全部」/「只补缺口」/「从仓重建派生数据」 | `REQ-011` 的 `universe` / 预估 / `--pull` / `--rebuild` |
| 数据缺口 | 按标的/期次/数据集下钻；每条的 `result` 与错误原文摘要 | 仓的 `raw_record`（`result` 索引） |
| 存储概览 | 仓规模、各 `result` 计数、最近批次、`fetched_at` 分布、「整体拷走」的说明 | 仓 + `manifest.jsonl` |

**两个动作必须在界面上可分辨**（`AC-6`）：`拉取（联网，会花配额，需确认）` 与
`从仓重建（离线，不花钱，不需要确认）`——文案、按钮位置、确认流程都不同。

**安全**：GUI 通过 `datalayer` 的**读接口**访问仓，不接收任何用户提供的路径；
若最终仍需打开 `archive_root` 下的文件，则在 `core/security.py` **显式新增允许根**
（不是放宽 jail 的判定），并在需求里留痕。

## 10. 文案与错误

### 10.1 文案禁则（`AC-3` 的可判定形式）

**面向用户的文本字段**：`NavItem.title`、`PanelSpec.title/description`、`ActionSpec.title/description`、
按钮文案、`blockers`、错误 `message`/`hint`、表头、空状态文案。

**禁则**：不得出现
`company_dir` / `output_dir` / `run_dir` / `input` / `latest` / `force` / `--only-gaps` / `--yes`
等内部参数名与 CLI 开关，不得出现绝对路径（`/Users/`、`.venv`）与 `python ` 这类解释器调用。

**例外**：`details.argv`（折叠在「详情」里的真实命令行）、`CommandSpec.title` 里保留的
`runs · resolve` 这类**技术别名**（它们只在「技术详情」视图里出现）。

实现：一条测试扫描 `/api/v1/pages/*`、`/api/v1/nav`、`/api/v1/actions` 的响应，
对**除 `details.*` 之外**的所有字符串字段跑禁则正则。这样禁则是**可断言的**，不靠评审人眼。

### 10.2 错误呈现（`AC-10`）

服务端把错误统一成三个字段（信封里已有的 `error{code,message,hint}` 复用）：

| 字段 | 内容 | 展示 |
|------|------|------|
| `message` | 发生了什么（人话，「缺少公司目录参数 company」→「还没选公司」） | 主视觉 |
| `hint` | 怎么办（「先在右上角选一家公司」） | 主视觉（次要样式） |
| `code` | `BAD_REQUEST` 等稳定错误码 | 折叠在「技术详情」 |

面板级降级卡片（`render/panels.py::render_panel_error`）按同一规则改：
标题是「这块内容暂时看不到」而不是「面板渲染失败：报告阅读」，错误码挪进折叠区。

## 11. 前端结构变化

```
static/
├── index.html        顶栏加「当前公司」选择器容器 + 空状态容器
├── app.js            选上下文 → 渲染 nav 树 → 分发面板 → 记录 selection（URL/localStorage）
├── kinds/
│   ├── chart.js      只保留 kind 分发（读 options.chart）→ 用 chart_core
│   ├── chart_core.js 新增：比例尺/刻度/图例/DPR/悬停/导出
│   ├── table.js      新增：筛选/排序/分页（服务端渲染的表格控件）
│   ├── actions.js    新增：动作按钮 + 预检禁用 + 确认层
│   ├── form.js       保留（技术详情视图，§10.1 的例外）
│   ├── jobs.js       改造：进度 / 折叠日志 / 失败摘要 / 产出链接
│   └── fallback.js   不动（未知 kind 仍然降级，`AC-3.3`）
└── style.css         新增：空状态、确认层、选择器、控件、进度条
```

`registerPanelKind` 与新的 `registerActionKind` 保持**可注册**：未知 kind 一律降级为可读卡片，
所以后端可以先上线新 kind、前端后补（`REQ-009.3` 的 `AC-3.3` 的承诺在这里继续生效）。

## 12. 兼容与回退（`REQ-009` 的判据不回退）

| `REQ-009` 的 AC | 2.0 怎么保证 |
|-----------------|--------------|
| `AC-1` 启动/绑定/healthz | 不动 `core/server.py` 的绑定与探针 |
| `AC-2.x` 公司与视图端点 | 路径参数语义改为「公司标识：ticker（规范）+ 目录名（兼容别名）」（§3.3），**已按 §8 在 `REQ-009` 留变更记录**；响应形状与失败路径判据不变，jail 不放宽；既有目录名断言保留为兼容性断言 + 新增 ticker 断言 |
| `AC-3.1`~`AC-3.7` 框架 | 扩展点只**加**字段与注册点；面板协议、信封、错误码、jail、脱敏全部不变 |
| `AC-4` 页面动作 | 页面的面板组合变了，但「从公司页点进图表/报告/迭代」这条路径必须继续可用 |
| `AC-5` 迭代台账 | 不动 `run_history` 插件的数据来源 |
| `AC-6` 安全边界 | 不放宽任何 jail；新增读取走显式允许根或读接口 |
| `AC-7` 测试预算 | `REQ-009` 的 4 个文件是它自己的上限，**本需求不往里加用例**；新开 4 个文件 |
| `AC-8` 实跑 | 每次收口都重跑 `scripts/gui_walkthrough.py`；`AC-12` 的走查是它的超集 |
| `AC-9` 扩展性 | 演示插件仍不改核心；新增的 nav/action 注册点由「演示插件」用例覆盖 |

**回退策略**：每个切片一个 PR，失败即回退该 PR；`REQ-012.1` 的 IA 改动最大，
`core/models.py` 的 `children`/`requires` 是**可选字段**，不填即旧行为——
所以 `.1` 即使回退，`REQ-009` 的页面仍然工作。

## 13. 测试与预算

### 13.1 四个文件的分工（各片自测）

| 片 | 测试文件 | 重点 |
|----|----------|------|
| `.1` | `tests/test_console_context.py` | nav 树序列化与 `requires`；ticker→目录解析；空上下文不降级（`E2` 回归）；旧 `company=目录名` 兼容；默认落地页是工作台 |
| `.2` | `tests/test_console_actions.py` | ActionSpec 契约；预检禁用与理由；确认流程（取消无副作用）；多步编排顺序与失败停止；文案禁则扫描 |
| `.3` | `tests/test_console_views.py` | 口径分组（年度/单季不再同序列）；DPR/标签抽稀的**契约字段**；表格控件标记；新鲜度徽标字段；错误 message/hint 契约 |
| `.4` | `tests/test_console_data_page.py` | 数据页三块的数据来源是仓；拉取与重建两个动作可分辨；缺口下钻 |

### 13.2 预算

`REQ-009` 的 4 个 webui 文件已 **64/64 零余量**，本需求必须新开文件。
仓库总预算已在 2026-09-28 经 owner 批准上调为 **52 文件 / 2000 用例**（变更记录见 `REQ-006` 的
AC-7 与任务 T9），上调前后对照与「本次没做清理」的欠账见
[`docs/DATA_LAYER_PLAN.md`](DATA_LAYER_PLAN.md) §14.1（两条需求当时共用同一份余量）。
本需求的用例预算（草案，登记时以 AC 定稿）：4 个文件，合计 ≤ 60 条——**按预算写，不因为有 347 条余量就随便加**。

### 13.3 走查是必需品，不是可选项

`REQ-009` 的教训（`E1`：**面板级 AC 全绿 ≠ 页面可用**）在这里更重要：
IA 与交互的改动**只有真实浏览器能验证**。因此 `AC-12` 是硬判据，且每个切片收口前都要跑一次
（不只父需求收口）。

## 14. 交付顺序与每片的验收动作

```
REQ-012.1  导航层级 + 公司上下文 + 工作台          ← 动 core/models.py、app.js（留痕）
   │       验收：冷开 #report 无降级；选公司后全站跟随；刷新/前进后退一致；工作台可用
   ▼
REQ-012.2  动作注册点 + 预检 + 任务中心            ← 动 core/registry.py、core/jobs.py（留痕）
   │       验收：意图化动作可跑通「更新分析」（含 §5.5 的人机交接）；未配 token 时禁用并说明；失败有原因+重试
   ▼
REQ-012.3  图表口径 + 绘制质量 + 表格 + 新鲜度 + 文案
   │       验收：600887 的年度/单季不再同线；图清晰、标签不截断；错误是人话
   ▼
REQ-012.4  数据页（**前置**：REQ-011.1 / REQ-011.2 已交付）
           验收：一键拉全有预估与确认；离线重建与拉取是两个动作；缺口可下钻
```

`.2` 与 `.3` 互不依赖，可并行；`.4` 必须等 `REQ-011`。

## 15. 反模式

| 反模式 | 为什么不行 |
|--------|-----------|
| 在前端做业务判断（比如自己算预估、自己判断能不能跑） | 预检与预估必须可被 CI 断言；前端算的东西测不到 |
| 把动作实现写进插件（重写业务） | `REQ-009` 的约束：面板是入口不是副本。动作只能**编排既有命令** |
| 为了「灵活」加一个任意命令输入框 | `REQ-009.1` 的白名单是安全边界 |
| 删掉真实 argv / 日志换「好看」 | 审计价值是这套工具最值钱的部分 |
| 每个面板自己写搜索/排序 | 表格能力要做在通用渲染层，否则 10 个页面 10 套实现 |
| 用前端框架「顺便解决」IA | 技术栈不解决信息架构；且破零依赖 |
| 只跑接口级测试就宣布 IA 改完 | `E1` 的教训：接口级 AC 全绿掩盖了整页不可用 |

## 16. 演进路线（写清触发条件）

| 方向 | 触发条件 | 备注 |
|------|----------|------|
| 估值 / 买卖计划页面 | `REQ-010` 交付后（有权威的「当前价值报告」指针） | 届时它们是公司级页面，**自动继承上下文**，不需要再动框架 |
| 批量分析（跨公司） | 自选股数量稳定且单公司流程已顺 | 动作模型已支持「按一组标的」扩展（`requires` 从单值变多值） |
| 多公司对比视图 | 需要横向比较时 | 上下文从单值变多值（`company` 重复参数）；面板协议不用改 |
| 新图型（K 线 / 热力图） | 有具体分析需求时 | 复用 `chart_core`，加一个渲染器 |
| 视图收藏 / 自定义工作台 | 工作台被真实使用一段时间后 | 页面描述已是纯数据（`AC-3.3`），可存一份面板配置 |
| 报告内搜索 / 批注 | 报告变长到需要检索时 | 需要先定锚点策略（服务端渲染 HTML 的锚点） |
| 定时**提醒**（不是定时拉取） | 使用者希望「数据过期时告诉我」 | 与 `REQ-009.4` 的 `AC-4.1` 不冲突，但要显式确认「提醒 ≠ 拉取」 |

## 17. 开放问题

> **已裁决的三项**（2026-09-28，owner CHU-2002）：① `AC-1` 的页面范围**收窄**为
> 「概览 / 图表 / 报告 / 迭代记录」（变更记录见 `REQ-012` 的「## 变更记录」）；
> ② **端点也改成 ticker**（ticker 规范 + 目录名兼容别名，变更记录见 `REQ-009` 的「## 变更记录」，§3.3）；
> ③ 测试 scope 预算**直接上调**为 52 文件 / 2000 用例（变更记录见 `REQ-006` 的 AC-7 与任务 T9）。
> 下面的第 1、2 条保留原文以便追溯「当时问了什么」。

1. **✅ 已裁决（收窄 `AC-1`）**：`REQ-012` 的 `AC-1` 括号里列了「估值/买卖计划」两个页面，
   但本设计的范围里**这两个页面不在本期**（属 `REQ-010` 之后的演进）。
   两条路：① 把 `AC-1` 的括号收窄为「概览/图表/报告/迭代记录」——**收窄 AC 需要 owner 批准并留变更记录**；
   ② 在 `REQ-012.1` 里补两个最小页面（只显示 `REQ-010` 的指针与状态）。
   倾向 ①（机制是通用的，页面后加会自动继承上下文），但这是需求决策，不由实现者定。
2. **✅ 已裁决（端点也改成 ticker）**：`AC-9` 要求统一显示名，URL 参数改为 ticker 会触及 `REQ-009` 的
   端点语义（`companies/{dir}/report` 用的是目录名）。**结论：本期把端点的路径参数改为「公司标识」，
   ticker 为规范形式、目录名保留为兼容别名**（§3.3），并已按 §8 在 `REQ-009` 留变更记录；
   原设计的「只在 GUI URL 层做映射、不动端点」被 owner 否决。
3. **工作台的「数据过期」怎么判定**：设计里用「仓内最新期次 > 分析期次」这类**本地**判据，
   不引入「最新可用期次」的外部知识（那需要披露源日历）。够不够用要等真实使用反馈。
4. **`actions` 是否算新 kind**：本设计新增 `kind=actions`。旧前端（浏览器缓存）会走降级卡片——
   可接受（`AC-3.3` 的承诺），但要不要给 `actions` 也提供一个服务端渲染的兜底列表？倾向：要（一行 HTML）。
5. **示例动作的边界**：「更新这家公司的分析」按 9 步编排既有命令。若某步的参数绑定需要
   上一步的输出（如 `run_dir` 来自 `runs_new` 的 stdout），绑定语法的表达力要够用——
   可能需要「步骤输出捕获」（`capture: stdout → {run_dir}`）。这在 `.2` 开工时用真实脚本验证，
   必要时收窄为「只编排参数互不依赖的步」。
6. **⚠️ 需要 owner 裁决**：`REQ-012` 的 `AC-2.4` 写的是「多步流程（如「更新这家公司的分析」）
   由一次动作编排完成」，但 §5.5 说明那个例子的中间**必然**有人机交接点（模块 Agent 只能在
   agent CLI 里跑，仓库没有程序化 LLM 入口）。三条路：
   ① **修订 `AC-2.4`**（把「人机交接是合法形态」写进去，留变更记录）——推荐；
   ② 换掉示例（用纯确定性流程），另立一条 AC 专门描述交接；
   ③ 要求支持程序化调用 agent CLI——**新能力**（成本、并发、凭据、审计、以及「谁付钱」都要重新设计），
   必须另开 REQ，不能在 `REQ-012` 里顺手做。
7. **人机交接的产物校验强度**：§5.5 用「产物存在且比该步开始时新」判断 agent 段跑完了。
   这挡得住「没跑就点继续」，挡不住「跑得不完整」。要不要更严（比对 `run_manifest.json` 的
   输入/产物清单，`REQ-003` 已有该能力）？倾向：先用 mtime/sha，实跑一次再决定。
