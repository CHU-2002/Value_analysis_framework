# 架构文档 (Architecture)

本文档描述 Value Analysis Framework 的分层结构、数据流与扩展点。面向希望深入理解或扩展本框架的开发者。

## 设计目标

1. **确定性计算与 LLM 判断分离**：Python 负责可复现的数据采集与数值计算，LLM 只负责定性判断与叙事写作。
2. **证据可追溯**：任何重要判断都必须引用可定位的 `evidence_id`，证据索引在分析开始前固定。
3. **上下文有预算**：每个 Agent 只接收与其职责相关的有界上下文，并记录裁剪状态。
4. **整组原子回退**：结构化结果集不完整时整体回退到旧 Markdown，绝不混用参数。
5. **可复现**：run manifest 记录输入与产物的 SHA-256，消费者校验同源、同主体、同输入。
6. **迭代可追溯**：增量运行（`/update-analysis`）与被 `adopt` 接管的基线运行是不可变 run，带框架指纹与覆盖期次，写入追加式台账；跨 run 只通过 `supersedes` 与变化报告发生关系。

## 分层结构

```
┌─────────────────────────────────────────────────────────────┐
│ 接口层                                                        │
│  .claude/commands/*, .opencode/commands/*                    │
│  Slash commands 描述工作流；不包含计算逻辑                     │
├─────────────────────────────────────────────────────────────┤
│ 策略层 strategies/                                            │
│  value（含 valuation 子模块）/ portfolio                        │
│  coordinator + phase prompts + references                     │
├─────────────────────────────────────────────────────────────┤
│ 共享定性层 shared/qualitative/                                │
│  agents/（模块提示词）、references/（schema、锚点）、templates/ │
│  coordinator_v2.md（完整分析）/ coordinator_update.md（增量更新）│
├─────────────────────────────────────────────────────────────┤
│ 计算层 scripts/                                               │
│  【买数据】datalayer/（原始仓 + DataAccess 收口 + 离线重建）、 │
│  tushare_collector.py、tushare_modules/                       │
│  【算数字】引擎（value/valuation/portfolio）、pdf_preprocessor、│
│  periods（期次）、discover_report、download_report、           │
│  screener_core、报告输出                                      │
├─────────────────────────────────────────────────────────────┤
│ 迭代层 scripts/version.py + runs.py + analysis_status.py      │
│  框架指纹、run-store（runs/{run_id}/）、台账、状态判定          │
├─────────────────────────────────────────────────────────────┤
│ 结果管线 scripts/results/                                     │
│  schema / manifest / evidence / context / prepare /           │
│  reconcile_results / synthesis / change_report /              │
│  resolve_qualitative                                          │
└─────────────────────────────────────────────────────────────┘
```

## GUI 层（`scripts/webui/`，REQ-009 + REQ-012）

控制台是**这一整套能力的本地入口**，不是新的一套计算。它的分层与上面那摞一样严格：

```
┌─────────────────────────────────────────────────────────────────────┐
│ 前端 shell：static/app.js + kinds/*.js                               │
│  选上下文 → 渲染 nav 树 → 分发面板 → 记录 selection（URL/local）      │
│  kinds：chart（+chart_core）/ report / collection / agent-form / table / actions / form / jobs │
├─────────────────────────────────────────────────────────────────────┤
│ 内核 core/：只做传输、路由、信封、安全、调度——不认识业务             │
│  server（127.0.0.1）· router（路径模板）· envelope（统一信封）        │
│  routes（nav / pages / panels）· security（路径 jail + 脱敏）         │
│  jobs（并发/排队/多步/失败摘要）· registry（六类注册点）              │
│  models（NavItem / PanelSpec / CommandSpec / JobTypeSpec / 步骤）     │
├─────────────────────────────────────────────────────────────────────┤
│ 插件 plugins/：一切具体功能都在这层，加功能不改内核                  │
│ 页面：companies（含 report）/ charts / run_history /                 │
│  collect / home / data_page                                          │
│  动作：actions（编排既有按键，不含业务实现）                          │
│  按键：commands（白名单 + argparse 扫描出的参数表）                  │
├─────────────────────────────────────────────────────────────────────┤
│ 渲染与数据：render/（面板 HTML：markdown_safe / panels）             │
│  datastore/（派生缓存：指纹 + parser_version，可失效）                │
│  archive/（采集批次与存档的只读视图）                                 │
└─────────────────────────────────────────────────────────────────────┘
```

三条边界（`REQ-009`/`REQ-012` 反复用到，改动前先确认没踩）：

1. **动作 = 编排既有按键，不重写业务**。`JobTypeSpec.steps` 引用的全是
   `plugins/commands.py::ENTRIES` 里已注册的按键；参数 → 命令行的转换只有
   `core/jobs.py::build_argv` 一处。所以「界面上的一个动作」与「手敲一串命令」跑的是**同一批实现**，
   不存在两套会漂移的逻辑。
2. **内核不解释业务**。`NavItem.requires` 只是字符串（`"selection.company"`），由前端 shell 比对
   `selection` 决定要不要显示空状态；`JobTypeSpec.requires` 由服务端预检翻译成禁用理由。
   新增一个「需要上下文」的页面或动作，只写插件字段，不改 `core/*`（`REQ-009.3` 的 `AC-9` 判据）。
3. **GUI 不越过数据层**。读原始仓走 `datalayer` 的读接口（`DataStore` / `Universe`），
   GUI 不接收用户给的路径、也不自己拼仓里的路径——因此 `core/security.py` 的允许根
   **没有**因为数据页而放宽；`scripts/datalayer/`（买回来的、不过期）与
   `scripts/webui/datastore/`（算出来的、可失效）仍然不得互相接线。

扩展面与留痕：`core/registry.py` / `router.py` / `routes.py` / `server.py` / `static/index.html` /
`static/app.js` 的哈希被 `tests/fixtures/webui_core_fingerprint.json` 钉住；而
`core/models.py` 及通用form/jobs客户端也在当前清单中（REQ-013的执行隔离复核后加入）；
`core/errors.py`、`config.py` 可随新增错误码与配置项演进。动清单里的文件要同步指纹，并说明这是「扩展面增量」
还是「缺陷修复」。设计与新增扩展点的清单见 [`GUI_CONSOLE_PLAN.md`](GUI_CONSOLE_PLAN.md)（`REQ-009`）
与 [`CONSOLE_V2_PLAN.md`](CONSOLE_V2_PLAN.md)（`REQ-012`，文首 §0 有实现状态与设计偏差）。

### REQ-014 清单维护接线

`plugins/watchlist.py` 声明三个离线动作（沿用 `JobTypeSpec` 的任务编排），为动作卡提供公司输入字段，
提交时在服务端验证字段并绑定配置中的仓与产物根。`scripts/watchlist_action.py` 通过
`datalayer.Universe` 写清单，导入只读取当前已接入的产物，返回逐项跳过原因；移除不触碰产物。
`companies_dataset` 将清单里的新公司与既有产物索引合并，尚未取数的公司页面保持正常空状态。
清单动作结束后刷新选择器与当前页的其它面板，并保留动作结果。此为前端 shell 的增量能力，
同步更新 `webui_core_fingerprint.json` 中 `static/app.js` 的指纹；核心调度、路由与安全边界不变。

## 数据流

### 1. 采集与解析（确定性）

```
远程原始响应 ─▶ ~/turtle_archive/store.db ─▶（离线重建，不联网）─▶ output/<公司>/data_pack_market.md

股票代码 + 自选股清单（universe）
  ├─ datalayer/（DataAccess 唯一取数收口点）─▶ 统一原始仓 store.db + manifest.jsonl
  ├─ datalayer rebuild（离线）─▶ data_pack_market.md（§1–§17，含上年同期可比列）
  ├─ discover_report.py + download_report.py ─▶ 定期报告 PDF（年报/中报/一季报/三季报）
  └─ pdf_preprocessor.py ─▶ pdf_sections_{period}.json（9 个章节）
```

- `scripts/datalayer/` 是「买数据」这一半：`store.py`（SQLite 单文件原始仓 + append-only `manifest.jsonl`）、
  `access.py`（唯一取数收口点 `DataAccess`，三种模式 `online` / `refresh` / `offline`）、
  `universe.py`（自选股清单）、`registry.py`（数据集语义声明：shape / 期次口径 / 累计口径 / 档位 / 变体 / 时间窗口）、
  `endpoints.py`（AST 扫取数调用点得到接口清单与字段并集）、`pull.py`（一次动作全量拉取：预估、确认、断点续跑、只补缺口）、
  `rebuild.py`（**离线**从仓重建产物）、`legacy.py`（一次性幂等导入旧存档与旧缓存）、
  `gaps.py`（结果分类与完备度）、`dataframe_codec.py`（DataFrame ⇄ JSON 往返 dtype 契约）、
  `security.py` / `config.py` / `errors.py` / `cli.py`。
- 仓在**仓库之外**的 `~/turtle_archive/store.db`，资产语义：不过期、不主动删、可整体拷走；
  删除要显式动作 + 二次确认。旧文件缓存 `output/.collector_cache/` **停写**（只读保留）。
- 取数收口：`scripts/tushare_collector.py` 的 `_safe_call` 只有一行转调 `DataAccess`；
  重试 / 限流 / VIP 路由都搬进了 `DataAccess`，`_cached_basic_call` / `_cached_us_daily` 不再写文件缓存。
- **重建只换数据源，不换模具**：`data_pack_market.md` 的小节与表头契约不变，
  `scripts/results/prepare.py` 与 `scripts/value_analysis_engine.py` 的输入契约不变。
- 期次口径：`scripts/periods.py` 是唯一权威，本次新增 `end_date_to_period` / `period_to_end_date` /
  `end_date_to_period_type` / `end_date_to_label`（最后一个由 `tushare_modules/assembly.py` 原先私有的
  `_yoy_period_label` 提升而来，assembly 改为调用它）；仓内记录带结构化 `shape` / `period_type`
  （annual/half/quarter/point/series）/ `cumulative`，图表不必再靠 Markdown 小节标题与列名猜口径。
- 边界：`scripts/datalayer/`（买回来的、不过期）与 `scripts/webui/datastore/`（算出来的、可失效）
  是两件事，**不得互相接线**（判据是「删了要不要重新花钱」）。手动边界不变：不引入任何定时 / 自动拉取，
  联网只能由显式动作触发；零新增第三方依赖（只用标准库 + 仓库既有依赖）。
- `data_pack_market.md` 覆盖基本信息、三大报表（合并 + 母公司）、分红、周线、财务指标、风险、无风险利率、回购、质押与 §17 衍生指标。
- 报表列包含**最新非年报期次 + 其上一年同期 + 近 5 年年报**，用于同比与单季拆分；`Q1`/`H1`/`Q3` 为年内累计口径，单季由累计相减得到。
- 期次标识由 `scripts/periods.py` 统一定义：`2026Q1` / `2026H1` / `2026Q3` / `2026FY`，提供解析、互转、同比期与单季减项。
- `discover_report.py` 以 CNINFO 四类公告分类为主源（年报 `category_ndbg_szsh`、半年报 `category_bndbg_szsh`、一季报 `category_yjdbg_szsh`、三季报 `category_sjdbg_szsh`），按 `secCode` 过滤发行人、翻页并解析期次；10jqka 仅作兜底。
- `download_report.py` 支持 `--report-type auto` / `--latest` / `--since`；已持有期次默认跳过（`--force` 重下），并写 `sources_index.json`（`period → 文件名 + size + sha256 + 公告日`）。
- `pdf_preprocessor.py --period` 按期次产出 `pdf_sections_{period}.json`（期次可从文件名推断）。
- 所有金额统一为**百万**单位（RMB/HKD/USD），由采集层完成换算与格式化。

### 2. 证据优先的定性分析（LLM + 确定性脚手架）

```
prepare
  ├─ 固定输入：data_pack_market.md / pdf_sections.json / data_pack_report.md / *.pdf
  ├─ 构建 evidence/index.json（可定位、可引用）
  ├─ 记录 run_manifest.json（输入与产物 SHA-256）
  └─ 生成 contexts/{module}.json（有字符预算）
        │
        ▼
  模块 Agent 并行（只读自己的 context）
        │  modules/{module}/result.json + report.md
        ▼
  reconcile_results ─▶ synthesis/reconciliation.json（冲突/时点/交叉影响）
        │
        ▼
  synthesis ─▶ synthesis/context.json（有预算的汇总卡片 + 精选证据）
        │
        ▼
  Final Synthesis Agent ─▶ qualitative_report.md + synthesis/result.json
        │
        ▼
  resolve_qualitative ─▶ qualitative_input.json（供下游消费）
```

核心模块与 scope：

| 模块 | 维度 | 触发 |
|------|------|------|
| `business_moat` | D1 商业模式 + D2 护城河 | 始终 |
| `environment` | D3 外部环境 | 始终 |
| `governance` | D4 管理层与治理 | 始终 |
| `mda_quality` | D5 MD&A 解读 | 始终 |
| `holding_structure` | D6 控股结构 | 条件（`d6_trigger.json`） |
| `period_delta` | D7 定期报告经营变化 | 仅 `/update-analysis` 增量 run 的模块 Agent（`prepare` 会为每个 run 生成 `contexts/period_delta.json`，基线 run 不跑该 Agent） |

### 2B. 定期报告增量更新（`/update-analysis`）

当最新一期定期报告发布时，不重写历史，而是新开一个 run：

```
analysis_status ─▶ no_record | legacy_layout | up_to_date | stale(new_report|framework_changed|schema_changed|inputs_changed|run_failed|downstream_stale) | broken | unsupported_market
      │
      ▼
download_report（--report-type auto / --since，跳过已持有期次）
      │
      ▼
runs.py new ─▶ runs/{run_id}/inputs/（run 私有输入快照）
      │
      ▼
prepare --primary-period ... --prior-analysis {上一 run 的 synthesis/result.json}
      │        └─ 注册 prior_analysis 证据源（上次结论可被逐条引用）
      ▼
四个核心模块 + period_delta（本期 vs 上次结论）并行
      │
      ▼
reconcile_results + synthesis ─▶ 更新 qualitative_report.md（含「本次更新说明」）
      │
      ▼
change_report ─▶ change_report_{period}.md + .json（独立交付物，说明经营变化与结论差异）
      │
      ▼
runs.py finish ─▶ history.jsonl + latest.json + record.json；标记下游 stale
```

- `period_delta`（D7）区分累计与单季口径，核对上次指引/承诺/watchlist 的兑现情况，并在 `requires_full_rerun=true` 时提示上一结论的基础已被推翻。
- 变化报告是**独立于更新后结论**的交付物：只讲清了什么变化，不重复完整分析。
- 下游新鲜度：增量 run 完成后 `value_computed.json` / `buy_sell_basis.json` 仍属旧财报期，`record.json:downstream.stale=true`；`analysis_status` 据此返回 `stale:downstream_stale`（退出码 1）。重跑 `/value-analysis`、`/buy-sell-plan` 后用 `python3 scripts/runs.py downstream --company-dir <dir> --fresh value_computed,buy_sell_basis`（或 `--fresh all`）清除标记，状态才会回到 `up_to_date`。买卖计划本身不会被自动改写。价值报告一侧另有自己的新鲜度与发布指针，见 §2D。

### 2C. 运行台账与框架指纹（run-store）

```
company_dir/
  latest.json      # 当前生效 run 指针
  record.json      # 分析记录卡（覆盖期次、框架、下游新鲜度）
  history.jsonl    # 追加式台账，每个 run 一行
  value_report.json          # 当前生效价值报告指针（REQ-010）
  value_reports/             # 不可变价值报告历史 + 失败记录（REQ-010）
  sources/pdf/     # 原始输入（PDF、`pdf_sections_{period}.json`、`sources_index.json`）
  runs/{run_id}/
    run.json       # kind / primary_period / supersedes / framework
    inputs/        # run 私有输入快照（默认真实复制；--hardlink 才用硬链接）
    evidence/ contexts/ modules/ synthesis/ + 报告
```

- `scripts/version.py` 提供 `FRAMEWORK_VERSION`、`prompt_fingerprint`（策略与提示词文件哈希）、`code_fingerprint`（git commit + 仅指纹相关路径的 dirty 状态，含 `docs/BUY_SELL_CONTRACT.md`）与 `schema_versions`，写入 manifest 的 `framework` 块与台账。
- `scripts/runs.py` 提供 `new` / `resolve` / `finish` / `adopt` / `export` / `downstream`；输入快照**默认真实复制**（`--hardlink` 仅在确认源文件永不被原地改写时才使用），因此行情刷新与新报告不会污染历史 run，旧 run 永久可校验。
- `scripts/runs.py adopt` 把既有的扁平目录接管为基线 run（默认非破坏，`--prune` 才清理旧布局），并同步重写 manifest / `evidence/index.json` / `contexts/*.json` 中的输入摘要、仅对被改写的产物重盖哈希；若源目录的产物与 manifest 记录不一致则**拒绝接管**（不洗白既有篡改）。接管后仍可被 `resolve_qualitative` 消费。
- `scripts/analysis_status.py` 是「要不要重跑、跑哪一级」的唯一决策点：退出码 `0` 最新、`1` 需增量更新、`3` 需全量重跑、`2` 参数错误；`--root --all --json` 输出全仓重跑清单。
- `scripts/runs.py downstream --fresh` 是唯一能清除 `downstream.stale` 的入口；缺少它时增量工作流会永久停留在退出码 1（该缺口由实现期评会发现并补齐）。

### 2D. 当前价值报告发布（`/value-analysis` 的收尾，REQ-010）

`latest.json` 只描述分析 run，不描述价值报告：公司目录里可能同时存在一份基于旧财报期的价值报告和一份新 run 的分析结论。`scripts/value_publication.py` 把两者分开，并提供「当前价值报告」的唯一入口：

```
company_dir/
  value_report.json              # 唯一指针：来源 run / 财报期 / 报告 sha256 / 完整性摘要
  value_reports/
    {run_id}/
      revisions.jsonl            # 该 run 的发布日志（追加）
      {sha12}/
        report.md                # 组装好的价值报告（不可变副本，指针指向它）
        manifest.json            # 来源 run、财报期、完整性摘要、发布时刻
        artifacts/               # value_computed.md + value_computed.json 的不可变副本
    failures.jsonl               # 失败的刷新尝试（追加，永不覆盖成功版本）
```

- 发布（`value_publication.py publish`）：先把输入解析到**最新成功且可消费**的分析 run（`complete` + 存在 `run_manifest.json`，否则退出码 3），再校验产物（报告存在且非占位、`value_computed.{md,json}` 存在、`value_computed.json` 可解析且 `schema=investment.value_snapshot` 带 `values.V_base`）。校验不通过则记 `failures.jsonl` 并退出码 2，**指针、报告字节、历史全部不动**。
- 读取（`value_publication.py read`）：唯一入口，返回 `state`（`fresh` / `stale` / `unavailable`）+ 来源 run + 财报期 + 报告路径与摘要；退出码 `0` / `1` / `3`。`stale` 的判据是「最新成功分析 run 更新了、最新分析财报期晚于价值基准、或 `record.json:downstream.value_computed=true`」；`unavailable` 覆盖无指针、报告丢失与摘要不一致（被就地改写）。
- 追溯（`value_publication.py resolve --run-id`）：按 run id 解析历史版本；同一 run 重算产生新摘要时**另存一份**（`{run_id}/{sha12}/`），旧版本字节不变，指针摘要始终等于对应历史产物。
- `scripts/analysis_status.py` 的输出新增 `latest_successful_run` 与 `value` 两个块（REQ-010 AC-1）：`latest_run` 可能失败，`latest_successful_run` 取台账里最后一个 `complete` 的 run；`value` 就是上面那份新鲜度状态。
- 失败记录（`value_publication.py fail`）：engine 崩溃、没有产物可校验时使用；只追加一条 `failures.jsonl`，指针不变。

### 3. 下游消费

价值分析、通用估值（价值分析子模块）、组合配置统一通过 `resolve_qualitative` 获取 `qualitative_input.json`：

- `source=structured`：完整、同 run、同主体、输入未变更的结构化结果集。
- `source=legacy`：仅当目录没有 `run_manifest.json` 时，原子回退到 `qualitative_report.md`。
- `source=unavailable`：无法消费；CLI 退出码 `3`（`2` 为参数错误）。

### 4. 确定性买卖计划（触发式）

买卖计划默认不生成。`value_analysis_engine` 保留原 Markdown，同时通过 `buy_sell_inputs` 只导出冻结的 `value_computed.json`（复用原三情景与 `valuation_engine` 独立方法），不写 `buy_sell_market.json`。用户阅读报告后运行 `/buy-sell-plan`（`scripts/buy_sell_plan.py`）才采集当时行情、写出 `buy_sell_market.json`，再对冻结基准离线运算；`buy_sell_engine` 只使用标准库、离线输入：

```
value_computed.json（主流程冻结） -> buy_sell_basis.json（按财报期/明确复核周期固定）
buy_sell_plan.py 采集当时行情     + buy_sell_market.json
                                  + qualitative_input.json（已验证诚信评级）
                                  + buy_sell_state.json / buy_sell_risk.json（可选）
                                  -> buy_sell_plan.json + buy_sell_plan.md
                                  -> 最终价值报告原样引用（仅当已触发）
```

买价/卖价为每股原币价格，不能套用财务报表的百万单位。固定基准包含来源 SHA-256、三价值锚、方法 CV、安全边际、四档价格和卖出价。更新行情不改变基准；执行状态只读，不推定成交。完整协议、审查修正及 `/valuation` 接入边界见 [BUY_SELL_CONTRACT.md](BUY_SELL_CONTRACT.md)。

## 结构化结果协议

### `investment.result` v1.0

每个模块与最终汇总输出一个 `result.json`：

| 字段 | 说明 |
|------|------|
| `schema` / `schema_version` | 固定为 `investment.result` / `1.0` |
| `result_type` | 如 `qualitative.business_moat`、`qualitative.synthesis` |
| `run` | `run_id`、`generated_at`、`as_of`、`status` |
| `subject` | `ticker`、`company`、`market` |
| `scope` | 维度列表，如 `["D1", "D2"]` |
| `summary` | 论点与置信度 |
| `parameters` | 受参数合同约束的标准化值 |
| `metrics` | 数值指标 |
| `claims` / `risks` / `watchlist` | 判断、风险与监控项 |
| `evidence` | 引用证据（`source_id`、`locator`、`quote`） |
| `quality` | 完整度、缺失输入、警告与未决问题 |

`parameters` 的取值域与所有权由 `scripts/results/schema.py` 的 `RESULT_TYPE_CONTRACTS` 定义，并与 `shared/qualitative/references/output_schema.md` 保持一致；`tests/test_qualitative_consumers.py` 会校验两者不漂移。

### 血缘与完整性

`run_manifest.json` 记录：

- 所有原始输入的路径、存在性与 SHA-256；
- 所有 prepare 产物（evidence index、context bundle、routing decision）的 SHA-256；
- `input_digest` 作为整组输入的稳定摘要；
- `primary_period`（增量 run 的主期次，加性字段）与 `framework` 块（版本、提示词指纹、代码指纹）。

`resolve_qualitative` 在消费前校验：

- 模块、证据索引、reconciliation、synthesis 的 run/subject 一致；
- 输入文件自 prepare 后未变更、未新增；
- prepare 产物未被篡改；
- reconciliation 的 `result_digest` 与模块结果集一致。

跨 run 的硬约束：只通过 `history.jsonl` 的 `supersedes` 与变化报告发生关系，**绝不混用两个 run 的参数**。

## 扩展点

### 新增定性模块

1. 在 `scripts/results/schema.py` 增加 `RESULT_TYPE_CONTRACTS` 条目。
2. 在 `scripts/results/context.py` 的 `MODULE_CONFIG` 定义数据段、PDF 段、关键词（如需引用上次结论，加 `prior_analysis` 段）。
3. 在 `shared/qualitative/agents/modules/` 添加提示词。
4. 在 `prepare.py` 的模块循环与下游命令中接入。
5. 更新 `shared/qualitative/references/output_schema.md` 并补充合同测试。

### 新增策略

1. 在 `strategies/{name}/` 添加 `coordinator.md`、阶段提示词与 `references/`。
2. 在 `.claude/commands/` 与 `.opencode/commands/` 添加同名命令。
3. 通过 `resolve_qualitative` 读取定性输入，不要直接解析 `qualitative_report.md`。
4. 在 `tests/test_qualitative_consumers.py` 增加命令合同测试。

### 新增确定性引擎

- 保持纯函数式、可离线测试、输出 Markdown/JSON 供 LLM 复用。
- 将新依赖写入 `requirements.txt`。
- 为数值逻辑补充边界与失败路径测试。

### 变更框架/提示词后的重跑

修改 `strategies/**`、`shared/qualitative/**` 或命令文档会改变 `prompt_fingerprint`；`analysis_status` 会把受影响的公司标记为 `stale:framework_changed`，并建议全量重跑。批量清单：

```bash
python3 scripts/analysis_status.py --root output --all --json
```

## 目录约定

| 路径 | 用途 |
|------|------|
| `~/turtle_archive/store.db` | 统一原始数据仓（SQLite 单文件 + append-only `manifest.jsonl`）：不过期、不主动删、可整体拷走 |
| `output/{code}_{company}/` | 单标的公司目录（gitignored） |
| `output/{code}_{company}/latest.json` | 当前生效 run 指针 |
| `output/{code}_{company}/record.json` | 分析记录卡（覆盖期次、框架、下游新鲜度） |
| `output/{code}_{company}/history.jsonl` | 追加式运行台账 |
| `output/{code}_{company}/runs/{run_id}/` | 不可变 run（含 `inputs/` 快照） |
| `output/{code}_{company}/sources/pdf/` | 原始输入（PDF、`pdf_sections_{period}.json`、`sources_index.json`） |
| `output/portfolio_{timestamp}/` | 组合运行目录 |
| `output/.collector_cache/` | Tushare 采集文件缓存（REQ-011 起**停写**，只读保留） |
| `contexts/` `modules/` `synthesis/` `evidence/` | 单个 run 内的标准产物 |

## 测试策略

- 全部测试基于 mock 数据，不依赖网络与 Token。
- 合同测试锁定 schema、命令、解析器与策略接口，防止协议漂移。
- 数值测试覆盖组合引擎的风险贡献与情景分析。
- 失败路径测试覆盖输入变更、产物篡改、过期摘要与非法枚举。
- 迭代测试覆盖台账写入与重复 run 拒绝、`adopt` 非破坏接管、状态判定的各分支与退出码、`covered_periods` 的失败/大小/归属校验、变化报告上下文的预算降级。

## REQ-013 程序化 agent 入口

`agent_report` 插件声明固定报告按键，`agent_action.py` 把动作映射到 Codex `exec` 或 Claude `-p`。
Codex 读取既有命令文档执行完整流程；agent 仍负责分析段，Python 仍负责确定性计算。
CommandSpec 的可选 validate/exclusive/exclusive_group/exclusive_key 声明由通用任务运行器在提交/入队锁内执行；注册与路由分发不识别业务。
前端 form 通过插件下发的预检/产物接口展示完整命令、确认额度、跟踪任务与链接产物；
原有按键未声明这些可选字段时继续使用既有行为。取消/超时通过包装脚本终止其隔离进程组。

REQ-013 的产物链接只采用成功启动子进程后记录的 execution_started_at→finished_at 区间；未启动即取消的排队任务没有新产物。三个 agent 动作按解析后的真实公司目录共享互斥组（覆盖市场后缀别名），避免并发写公司产物而互相误认。


### REQ-015 研究工作区接线

`NavItem`可选`placement/actions`描述全局入口、公司页签与就近操作；shell只解释通用声明，
公司解析、模型白名单、历史聚合与报告版本判断仍在插件。这项通用扩展已单列owner决策，批准与独立QA复核前不收口。
既有无placement外部插件继续使用group/children，六类注册点不变，URL保存上下文并拒绝过时异步面板响应。

时间轴从配置的原始仓以SQLite只读事务读取版本元数据；新revision才解码原始行并原子投影为
`output/.research_sources/…/raw.json`不可变源。`DatasetSpec`声明此源，统一`DataStore`按原始revision、
解析器版本、周期/范围/分位窗口/复权及观察日期缓存派生结果；源路径jail仍限定output，未扩大核心读权限。
这只是本地可再生成副本，仓与派生缓存保持各自生命周期。财务fallback同层处理，损坏既有数据包仍显示诊断。

`report_reader.catalog/document`将指针、history、run/manifest与报告正文作为sha256输入，
元信息与安全Markdown结果复用统一缓存；公司jail和索引id在命中前验证，材料不送入Markdown。
新客户端kind负责目录/版本/查找/差异与采集解释，业务计算不放到浏览器。

agent选择使用本地模型目录与服务端预检，单次argv携带模型，不写全局配置、不重试/降级。
包装脚本流式捕获CLI事件，实际模型来自结构化事件或本次Codex thread运行记录；缺证据显示未知。
执行前后报告内容摘要决定本次产物；GUI要求产物时退出0而无本动作报告转为失败。
采集用不可变计划摘要绑定确认，工作线程各有SQLite连接；批次统计包含未尝试目标，实际请求与逻辑目标分别持久化。
