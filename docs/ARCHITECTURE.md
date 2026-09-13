# 架构文档 (Architecture)

本文档描述 Value Analysis Framework 的分层结构、数据流与扩展点。面向希望深入理解或扩展本框架的开发者。

## 设计目标

1. **确定性计算与 LLM 判断分离**：Python 负责可复现的数据采集与数值计算，LLM 只负责定性判断与叙事写作。
2. **证据可追溯**：任何重要判断都必须引用可定位的 `evidence_id`，证据索引在分析开始前固定。
3. **上下文有预算**：每个 Agent 只接收与其职责相关的有界上下文，并记录裁剪状态。
4. **整组原子回退**：结构化结果集不完整时整体回退到旧 Markdown，绝不混用参数。
5. **可复现**：run manifest 记录输入与产物的 SHA-256，消费者校验同源、同主体、同输入。

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
├─────────────────────────────────────────────────────────────┤
│ 计算层 scripts/                                               │
│  tushare_modules/（采集）、引擎（value/valuation/portfolio）、 │
│  pdf_preprocessor、screener_core、报告输出                    │
├─────────────────────────────────────────────────────────────┤
│ 结果管线 scripts/results/                                     │
│  schema / manifest / evidence / context / prepare /           │
│  reconcile_results / synthesis / resolve_qualitative          │
└─────────────────────────────────────────────────────────────┘
```

## 数据流

### 1. 采集与解析（确定性）

```
股票代码
  ├─ tushare_collector.py ─▶ data_pack_market.md（§1–§17）
  ├─ discover_report.py + download_report.py ─▶ 年报 PDF
  └─ pdf_preprocessor.py ─▶ pdf_sections.json（9 个章节）
```

- `data_pack_market.md` 覆盖基本信息、三大报表（合并 + 母公司）、分红、周线、财务指标、风险、无风险利率、回购、质押与 §17 衍生指标。
- `pdf_sections.json` 提取 MDA、GOV、MATTERS、P2、P3、P4、P6、P13、SUB。
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

### 3. 下游消费

价值分析、通用估值（价值分析子模块）、组合配置统一通过 `resolve_qualitative` 获取 `qualitative_input.json`：

- `source=structured`：完整、同 run、同主体、输入未变更的结构化结果集。
- `source=legacy`：仅当目录没有 `run_manifest.json` 时，原子回退到 `qualitative_report.md`。
- `source=unavailable`：无法消费；CLI 退出码 `3`（`2` 为参数错误）。

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
- `input_digest` 作为整组输入的稳定摘要。

`resolve_qualitative` 在消费前校验：

- 模块、证据索引、reconciliation、synthesis 的 run/subject 一致；
- 输入文件自 prepare 后未变更、未新增；
- prepare 产物未被篡改；
- reconciliation 的 `result_digest` 与模块结果集一致。

## 扩展点

### 新增定性模块

1. 在 `scripts/results/schema.py` 增加 `RESULT_TYPE_CONTRACTS` 条目。
2. 在 `scripts/results/context.py` 的 `MODULE_CONFIG` 定义数据段、PDF 段与关键词。
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

## 目录约定

| 路径 | 用途 |
|------|------|
| `output/{code}_{company}/` | 单标的运行目录（gitignored） |
| `output/portfolio_{timestamp}/` | 组合运行目录 |
| `output/.collector_cache/` | Tushare 采集缓存 |
| `contexts/` `modules/` `synthesis/` `evidence/` | 定性 run 的标准产物 |

## 测试策略

- 全部测试基于 mock 数据，不依赖网络与 Token。
- 合同测试锁定 schema、命令、解析器与策略接口，防止协议漂移。
- 数值测试覆盖组合引擎的风险贡献与情景分析。
- 失败路径测试覆盖输入变更、产物篡改、过期摘要与非法枚举。
