# 架构文档 (Architecture)

本文档描述 Value Analysis Framework 的分层结构、数据流与扩展点。面向希望深入理解或扩展本框架的开发者。

## 设计目标

1. **确定性计算与 LLM 判断分离**：Python 负责可复现的数据采集与数值计算，LLM 只负责定性判断与叙事写作。
2. **证据可追溯**：任何重要判断都必须引用可定位的 `evidence_id`，证据索引在分析开始前固定。
3. **上下文有预算**：每个 Agent 只接收与其职责相关的有界上下文，并记录裁剪状态。
4. **整组原子回退**：结构化结果集不完整时整体回退到旧 Markdown，绝不混用参数。
5. **可复现**：run manifest 记录输入与产物的 SHA-256，消费者校验同源、同主体、同输入。
6. **迭代可追溯**：每次运行是不可变的 run，带框架指纹与覆盖期次，写入追加式台账；跨 run 只通过 `supersedes` 与变化报告发生关系。

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
│  tushare_modules/（采集）、引擎（value/valuation/portfolio）、 │
│  pdf_preprocessor、periods（期次）、discover_report、          │
│  download_report、screener_core、报告输出                     │
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

## 数据流

### 1. 采集与解析（确定性）

```
股票代码
  ├─ tushare_collector.py ─▶ data_pack_market.md（§1–§17，含上年同期可比列）
  ├─ discover_report.py + download_report.py ─▶ 定期报告 PDF（年报/中报/一季报/三季报）
  └─ pdf_preprocessor.py ─▶ pdf_sections_{period}.json（9 个章节）
```

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
| `period_delta` | D7 定期报告经营变化 | 仅 `/update-analysis` 增量 run |

### 2B. 定期报告增量更新（`/update-analysis`）

当最新一期定期报告发布时，不重写历史，而是新开一个 run：

```
analysis_status ─▶ no_record | up_to_date | stale(new_report|framework_changed|inputs_changed) | broken
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
- 下游新鲜度：增量 run 完成后 `value_computed.json` / `buy_sell_basis.json` 仍属旧财报期，需用户显式重跑 `/value-analysis`；买卖计划不自动改写。

### 2C. 运行台账与框架指纹（run-store）

```
company_dir/
  latest.json      # 当前生效 run 指针
  record.json      # 分析记录卡（覆盖期次、框架、下游新鲜度）
  history.jsonl    # 追加式台账，每个 run 一行
  sources/         # 原始输入（PDF / 期次章节 / sources_index.json）
  runs/{run_id}/
    run.json       # kind / primary_period / supersedes / framework
    inputs/        # run 私有输入快照（默认真实复制；--hardlink 才用硬链接）
    evidence/ contexts/ modules/ synthesis/ + 报告
```

- `scripts/version.py` 提供 `FRAMEWORK_VERSION`、`prompt_fingerprint`（策略与提示词文件哈希）、`code_fingerprint`（git commit + 仅指纹相关路径的 dirty 状态）与 `schema_versions`，写入 manifest 的 `framework` 块与台账。
- `scripts/runs.py` 提供 `new` / `resolve` / `finish` / `adopt` / `export`；输入快照**默认真实复制**（`--hardlink` 仅在确认源文件永不被原地改写时才使用），因此行情刷新与新报告不会污染历史 run，旧 run 永久可校验。
- `scripts/runs.py adopt` 把既有的扁平目录接管为基线 run（默认非破坏，`--prune` 才清理旧布局），并同步重写 manifest / `evidence/index.json` / `contexts/*.json` 中的输入摘要并重盖产物哈希，接管后仍可被 `resolve_qualitative` 消费。
- `scripts/analysis_status.py` 是「要不要重跑、跑哪一级」的唯一决策点：退出码 `0` 最新、`1` 需增量更新、`3` 需全量重跑、`2` 参数错误；`--root --all --json` 输出全仓重跑清单。

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
| `output/{code}_{company}/` | 单标的公司目录（gitignored） |
| `output/{code}_{company}/latest.json` | 当前生效 run 指针 |
| `output/{code}_{company}/record.json` | 分析记录卡（覆盖期次、框架、下游新鲜度） |
| `output/{code}_{company}/history.jsonl` | 追加式运行台账 |
| `output/{code}_{company}/runs/{run_id}/` | 不可变 run（含 `inputs/` 快照） |
| `output/{code}_{company}/sources/` | 原始输入（PDF、期次章节、`sources_index.json`） |
| `output/portfolio_{timestamp}/` | 组合运行目录 |
| `output/.collector_cache/` | Tushare 采集缓存 |
| `contexts/` `modules/` `synthesis/` `evidence/` | 单个 run 内的标准产物 |

## 测试策略

- 全部测试基于 mock 数据，不依赖网络与 Token。
- 合同测试锁定 schema、命令、解析器与策略接口，防止协议漂移。
- 数值测试覆盖组合引擎的风险贡献与情景分析。
- 失败路径测试覆盖输入变更、产物篡改、过期摘要与非法枚举。
- 迭代测试覆盖台账写入与重复 run 拒绝、`adopt` 非破坏接管、状态判定的各分支与退出码、`covered_periods` 的失败/大小/归属校验、变化报告上下文的预算降级。
