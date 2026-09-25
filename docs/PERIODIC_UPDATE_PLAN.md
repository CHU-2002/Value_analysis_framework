# 定期报告增量更新分析 · 设计与实施计划

本文档定义一项新能力的最终设计：**根据最新一期定期报告（一季报 / 半年报 / 三季报 / 年报）做增量更新分析**——若某公司已有分析记录，则拉取最新期次、分析最近一段时间的经营状况、更新已有结论，并额外输出一份独立的「经营变化报告」；同时定义**分析迭代的管理机制**（财报更新与框架更新两类触发）。

> 状态：**已实现**（PR1–PR4 已合入 `main`；PR5 文档随对应 PR 合入）。
> 已确认决策：
> 1. **迭代管理采用彻底 run-store**：所有 run 产物落在 `runs/{run_id}/`，公司目录只留指针、台账与原始输入。
> 2. **增量更新默认四模块 + `period_delta` 全量重跑**，保留「同 run / 同主体 / 同输入」不变量。
> 3. **范围限定 A 股四类定期报告**，触发方式为手动 `/update-analysis`。
> 4. 交付顺序：先合本文档，再从 PR1（季度报下载）开始实现。

### 实现记录

| PR | 分支 | 内容 | 主要位置 |
|----|------|------|----------|
| 1 | `feat/periodic-report-discovery` | CNINFO 四类定期报告发现、期次工具、`--latest`/`--since` 下载、`sources_index.json` | `scripts/discover_report.py`、`scripts/periods.py`、`scripts/download_report.py` |
| 2 | `feat/comparable-periods` | 上年同期可比列、每期次 `pdf_sections_{period}.json`、`prepare --primary-period` | `scripts/tushare_modules/infrastructure.py`、`scripts/pdf_preprocessor.py`、`scripts/results/prepare.py` |
| 3 | `feat/run-history-ledger` | `version.py` 框架指纹、`runs.py` run-store 与台账、`analysis_status.py` 状态判定 | `scripts/runs.py`、`docs/ARCHITECTURE.md` |
| 4 | `feat/period-delta-analysis` | `qualitative.period_delta`（D7）、`prior_analysis` 证据源、变化报告与 `/update-analysis` | `shared/qualitative/coordinator_update.md` |
| 5 | `docs/periodic-update` | 架构/README/CHANGELOG 与下游新鲜度接线 | `docs/ARCHITECTURE.md`、`README.md`、`CHANGELOG.md`、`.claude/commands/value-analysis.md`、`.opencode/commands/value-analysis.md` |

> 各 PR 在合入前都经过**无上下文独立子 agent 的对抗式评审**；评审发现的问题已复现并修复，逐条记录在各 PR 描述中。
>
> 相对原设计的偏差：`prepare` 的两个新选项分别为 `--primary-period`（主期次证据）与 `--prior-analysis`（上次结论证据源）；`sources_index.json` 额外记录 `size_bytes` 与 `last_download_status`（可选、向后兼容）。

---

## 1. 现状核查

| 能力 | 现状 | 证据 | 缺口 |
|------|------|------|------|
| 年报下载 | ✅ CNINFO 主源 + 10jqka 兜底 | `scripts/discover_report.py:143-155,223-224` | 无 |
| 季度 / 半年报下载 | ⚠️ 仅形似 | `get_cninfo_category()` 对非年报返回 `""`；`should_prefer_cninfo()` 仅年报为真 | 非年报从不查询 CNINFO，只剩 `stockpage.10jqka.com.cn`（基本只列年报），事实上拿不到季度报 |
| 期次（period）模型 | ❌ 无 | 仅靠文件名约定 `{code}_{year}_{type}.pdf` | 无法表达 `2026Q1` / `2026H1`，无法判断「已覆盖哪些期次」 |
| 同比可比期 | ⚠️ 部分 | `scripts/tushare_modules/infrastructure.py:95-145` 只保留「晚于最近年报的非年报期 + 5 年年报」 | `2026H1` 场景缺 `2025H1` 列，同比算不出来 |
| 分析记录 | ⚠️ 单次覆盖 | `scripts/results/prepare.py:48-58,123`；`run_manifest.json` 每次覆盖写，`status` 永远停在 `prepared` | 无历史、无台账，无法回答「上次结论、哪期、哪个框架版本」 |
| 结论更新 | ❌ 无 | — | 无法把新一期财报与旧结论对齐 |
| 变化报告 | ❌ 无 | — | 无 |
| 原子回退原则 | ✅ 已实现 | `scripts/results/resolve_qualitative.py:310-340` | 需在不破坏它的前提下支持多 run |

现有 `output/600887_伊利`、`output/600941_中国移动` 已有结构化记录（`run_manifest.json` + `evidence/ + modules/ + synthesis/`），其余目录为 legacy 扁平布局；两类都必须能平滑接管。

> 结论：**季度报下载不是「完全没工具」，而是「壳有、通路没接」**——`download_report.py` 已接受 `--report-type 中报/一季报/三季报`（连文件名规则与测试都存在，见 `tests/test_download_report.py:80-92`），但发现层从未为非年报查询 CNINFO。

---

## 2. 目标与范围

**目标**

1. 能稳定发现并下载 A 股四类定期报告，并自动确定「最新已发布期次」。
2. 新增 `/update-analysis {ticker} [period]`：无记录 → 全量基线；有记录 → 拉最新期次、增量分析、更新结论、输出独立变化报告。
3. 新增迭代管理：不可变 run、追加式台账、框架指纹、状态判定器与批量重跑清单。

**范围**

- 市场：A 股（`6xx/0xx/3xx`）。港股 / 美股本期不做，`analysis_status` 明确返回 `unsupported_market`。
- 报告期次：`一季报 / 半年报 / 三季报 / 年报`。
- 触发：手动命令。不做定时任务。

**不做**

- 不自动改写买卖计划。新期次只把 `value_computed` / `buy_sell_basis` 标记为 stale，买卖计划仍由 `/buy-sell-plan` 显式触发。
- 不引入外部调度、数据库或 git-annex 等重型依赖，全部基于标准库 + 现有 Python 依赖。

---

## 3. 总体架构（run-store）

```
公司目录 output/{code}_{company}/
├── latest.json           # 指针：当前生效 run、主期次、产物路径、发布时间
├── record.json           # 分析记录卡：覆盖期次、最近 run、下游新鲜度（给人看，也给工具读）
├── history.jsonl         # 追加式台账，每个 run 一行
├── sources/              # 原始输入，追加式，按期次/内容命名，只增不改名
│   ├── pdf/{period}.pdf
│   ├── pdf_sections/{period}.json
│   ├── data_pack_market_{asof}.md
│   └── data_pack_report_{period}.md
├── published/            # 最新 run 的人类可读镜像（派生、可再生，不作为任何 run 的输入）
│   ├── qualitative_report.md
│   ├── qualitative_input.json
│   ├── change_report_{period}.md / .json
│   └── {company}_{code}_价值分析报告.md
└── runs/
    ├── {run_id}/
    │   ├── run.json                 # run 元数据：kind / periods / framework / supersedes
    │   ├── inputs/                  # run 私有输入快照（默认真实复制；--hardlink 才用硬链接）
    │   │   ├── sources_manifest.json
    │   │   ├── data_pack_market.md
    │   │   ├── pdf_sections.json    # 主期次兼容副本
    │   │   ├── pdf_sections_{period}.json
    │   │   ├── data_pack_report.md
    │   │   └── {code}_{year}_{type}.pdf
    │   ├── run_manifest.json        # inputs 一律指向本 run 的 inputs/ 快照
    │   ├── evidence/index.json
    │   ├── contexts/{module}.json
    │   ├── modules/{module}/result.json + report.md
    │   ├── synthesis/{reconciliation,context,result}.json
    │   ├── qualitative_report.md
    │   └── change_report_{period}.md / .json
    └── {older_run_id}/ ...
```

**为什么要 run 私有输入快照。** 当前 `prepare.py` 把共享目录里的可变文件（`data_pack_market.md`、`pdf_sections.json`）写进 manifest 并记 SHA-256；行情一刷新、章节包一覆盖，旧 run 立即校验失败，只能整体重跑。快照后每个 run 自证其说、永久可复核，新报告不会污染旧 run——这正是迭代管理的地基。

**真相来源。** `latest.json` 是唯一权威指针；`published/` 只是便于阅读与外部工具的镜像。合同测试断言镜像产物哈希等于最新 run 对应产物哈希。

---

## 4. 期次模型

### 4.1 period 规范

```
2026Q1 | 2026H1 | 2026Q3 | 2026FY
```

A 股语义：`Q1` / `H1` / `Q3` 为**年内累计**，`FY` 为全年。

- **同比**：与上年同期累计比（`2026H1` vs `2025H1`）。
- **单季**：本期累计减上一累计（`H1−Q1`、`Q3−H1`、`FY−Q3`）。
- **环比**：仅在显式标注后使用，避免季节性误导。

### 4.2 新增 `scripts/periods.py`（纯函数，可离线测）

- `parse_period_from_title(title, report_type)`：`2026年半年度报告` → `2026H1`。
- `period_to_filename(code, period)` / `filename_to_period(name)`。
- `period_sort_key(period)`、`comparable_period(period)`（上年同期）、`previous_period(period)`。
- `single_quarter_span(period)`：给出减项，供 delta 计算。

---

## 5. 定期报告发现与下载（PR1）

### 5.1 接上 CNINFO 四类定期报告

分类码已核对（[alwqx/sec cninfo provider](https://pkg.go.dev/github.com/alwqx/sec/provider/cninfo)、[Lester Zhang 爬取实践](https://lester-zhang.com/posts/%E4%B8%8A%E5%B8%82%E5%85%AC%E5%8F%B8%E5%AE%9A%E6%9C%9F%E6%8A%A5%E5%91%8A%E7%88%AC%E5%8F%96/)）：

| report_type | period 后缀 | CNINFO category | 发布窗口 |
|---|---|---|---|
| 年报 | `FY` | `category_ndbg_szsh` | 次年 3–4 月 |
| 中报 / 半年报 | `H1` | `category_bndbg_szsh` | 当年 8–9 月 |
| 一季报 | `Q1` | `category_yjdbg_szsh` | 当年 4 月 |
| 三季报 | `Q3` | `category_sjdbg_szsh` | 当年 10 月 |

改动集中在 `scripts/discover_report.py`：

1. `get_cninfo_category()` 返回上表映射（当前非年报返回空）。
2. `should_prefer_cninfo()` 四类均返回真。
3. `is_excluded_title()` 增补排除词：`英文`、`取消`、`提示性公告`、`业绩说明会`、`问询函`、`H股`。
4. `build_cninfo_date_range()`：非年报保持 `{Y}-01-01~{Y+1}-12-31`；年报保持现有窗口。

### 5.2 新增「最新期次」探测

`discover_latest_period(stock_code)`：

- 一次 POST，`category=category_ndbg_szsh;category_bndbg_szsh;category_yjdbg_szsh;category_sjdbg_szsh;`（接口支持多类拼接），`seDate` 取滚动 18 个月，按 `announcementTime` 倒序；
- 用 `periods.parse_period_from_title()` 解析期次，返回最新一期完整报告；
- 期次未发布时返回 `published=false`，而不是靠 `today.year` 猜测回退。

### 5.3 CLI 扩展

```bash
# 单期次
python3 scripts/download_report.py --stock-code 600887 --report-type 中报 --year 2026 --save-dir output/600887_伊利/sources/pdf
# 最新已发布期次
python3 scripts/download_report.py --stock-code 600887 --report-type auto --latest --save-dir ...
# 补齐：对比 sources/ 已有期次，拉取所有缺失且已发布的期次
python3 scripts/download_report.py --stock-code 600887 --report-type auto --since 2026Q1 --save-dir ...
```

- 结构化 `---RESULT---` 块新增 `latest_period / periods_requested / periods_completed / periods_failed`，旧字段保持。
- 下载后写 `sources/pdf/sources_index.json`：`period → 文件名 + size + sha256 + 公告日 + 标题 + source`（新增字段均为可选、向后兼容）。

### 5.4 测试

`tests/test_discover_report.py`、`tests/test_download_report.py` 新增：四类 category 命中 CNINFO、排除词、多 category 最新期次探测、未发布期次优雅失败、`auto` 补齐顺序、文件名与期次互转。全部 mock `requests.post`（该测试文件历史上曾真连网导致 CI 403，必须守住）。

---

## 6. 数据层：补齐同比可比期（PR2）

`scripts/tushare_modules/infrastructure.py:_prepare_display_periods()` 现只保留「晚于最近年报的 interim + 5 年年报」，导致 `2026H1` 场景没有 `2025H1`。改法：

- interim 保留最近 **2 年**同口径期次（`2026H1/Q1` + `2025H1/Q1`）；
- 列序为「最新 interim → 其上年的同口径期次 → 5 年年报」，标签沿用 `{year}H1/Q1/Q3/FY`；
- 兼容现有 §3 / §3P / §4 等表格解析（按列名取值）。

`pdf_preprocessor.py` 产物按期次命名 `pdf_sections_{period}.json`，写进 `sources/pdf_sections/`；`pdf_sections.json` 作为主期次兼容副本。

---

## 7. 增量更新流程 `/update-analysis`（PR4）

### 7.1 分支逻辑

```
/update-analysis {ticker} [period]
  ├─ analysis_status 判定
  │    ├─ no_record         → 转 /business-analysis 建基线 run，并写入台账
  │    ├─ stale:new_report  → 增量 run（本文档主体）
  │    ├─ stale:framework   → 全量重跑（新 run，supersedes 旧 run）
  │    └─ up_to_date        → 明确告知，不重复消耗
  └─ 增量 run 步骤（脚本 + Agent）
       1. runs.py new：归档指针、签发 run_id、创建 runs/{run_id}/inputs/
          （`--input` 必须含 `data_pack_market.md`、主期次章节包与报告 PDF，**以及附注源**
          `data_pack_report.md`——中报是 `data_pack_report_interim.md`。漏了它 `prepare`
          会给出显式 warning，并在 `run_manifest.unavailable_inputs` 里登记
          `pdf_footnotes / not_applicable`，不再静默；REQ-006.2 AC-2.6）
       2. 拉最新期次（PR1）+ 章节解析 + 脚注抽取
       3. 刷新 data_pack_market.md 并快照进 inputs/
       4. prepare --run-id {run_id} --primary-period：生成 evidence / contexts
          （inputs 全部来自快照；`--run-id` 必须用第 1 步 `runs.py new` 签发的 id，
           否则台账 id 与 run.json 分叉——`prepare` 现在会直接报错）
       5. 四个核心模块重跑
       6. period_delta 模块：本期 vs 上次结论
       7. reconcile + synthesis：更新 qualitative_report.md（含「本次更新说明」）
       8. 产出 change_report_{period}.md + .json
       9. runs.py finish：写 history.jsonl、latest.json、record.json，刷新 published/
      10. 标记下游 stale（value_computed / buy_sell_basis），不自动改买卖计划
```

#### 7.1.1 Agent 专属段落（§7 / §8 / §10 / §13.2）的填充策略

`data_pack_market.md` 里有一类段落**采集侧拿不到**、只能由 Agent 补充，采集时只写占位符
`*[§N 待Agent WebSearch补充]*`：

| 段落 | 内容 | 证据来源 |
|------|------|----------|
| §7（部分） | 控股股东、管理层变更、违规记录等定性信息 | D4 `governance` |
| §8 | 行业与竞争格局 | D3 `environment` |
| §10 | 管理层讨论与分析（外部视角） | D3 `environment` / D5 `mda_quality` |
| §13.2 | 风险警示的网络补充 | D4 `governance` |

**策略（2026-09-25 owner 决定，REQ-006.2 AC-2.3）：**

1. **只有全量分析填**（首建基线、`stale:framework` 全量重跑、`/business-analysis`）。
2. **增量更新流程不填**：增量 run 的 Step 1~10 **不含** WebSearch 补段，这三节保持占位符。
   理由：行业/政策信息变化慢，而增量更新的目标是快与可复现；每次联网搜索会让同一份财报
   跑出不同的行业结论。
3. **占位符不得当证据**：`build_evidence_index` 会把「只含占位符」的段落记进
   `evidence_index.unfilled_sections` 并从 `entries` 里剔除；模块 bundle 对这些槽位给出
   `evidence_coverage: unavailable` 与 `unavailable_inputs`（带原因），而不是一段假的 quote。
4. **证据时点要写清楚**：增量 run 里行业/治理类判断若沿用上一轮，必须在报告里标明
   「沿用 <日期> 那轮的行业证据」，不得写成本期新证据。
5. 增量 run 若发现行业/政策出现**实质变化**，按既有升级规则走 `stale:framework` 全量重跑，
   而不是在增量流程里临时补段。

### 7.2 新模块 `period_delta`

- 契约：`scripts/results/schema.py:RESULT_TYPE_CONTRACTS` 增 `qualitative.period_delta`，scope `["D7"]`。
- 提示词：`shared/qualitative/agents/modules/period_delta.md`。
- Context：`scripts/results/context.py:MODULE_CONFIG` 增条目，数据段取三大报表 + §17 衍生指标；**新增证据源 `prior_analysis`**（上次 run 的 `synthesis/result.json`、参数、watchlist、risks），使「上次说 X、本期变 Y」逐条可引 `evidence_id`。
- `shared/qualitative/references/output_schema.md` 同步，并纳入 `tests/test_qualitative_consumers.py` 的防漂移合同测试。
- 输出要点：
  - `metrics`：单季拆分、同比、毛利率 / 费用率 / 现金含量 / 应收存货 / 有息负债 / 分红回购；
  - `expectation_vs_actual`：上次的 `mda_forward_guidance`、承诺、`watchlist`、`risks` → 本期兑现 / 未兑现 / 无法验证；
  - `conclusion_changes`：逐参数 维持 / 上调 / 下调 / 证据不足 + 证据；
  - `requires_full_rerun`：审计非标、重大并购 / 增发 / 重组、会计政策变更或重述、护城河或诚信评级变化 → 升级为全量重跑。

### 7.3 「更新已有结论」的方式

- 默认：四个核心模块 + `period_delta` **全量重跑**，新 run 覆盖 `latest.json` 指向的结论；旧 run 完整归档。`resolve_qualitative` 的「同 run / 同主体 / 同输入」不变量完全不动，价值分析与估值照常消费。
- 历史上曾讨论的 `--light`（仅 delta + digest 结转）本期不做，避免放宽 resolver 约束。

### 7.4 独立变化报告

`runs/{run_id}/change_report_{period}.md`（另有 `.json` sidecar 供机器 diff），镜像到 `published/`：

1. 执行摘要：本期最重要的 3–5 个变化 + 结论是否改变
2. 关键财务：收入 / 利润 / 毛利率 / 现金流 的同比与单季（标注可比期）
3. 经营质量：现金含量、应收 / 存货 / 周转、费用率、负债与商誉
4. 业务与分部（来自本期 MDA）
5. 治理与股东：质押 / 减持 / 回购 / 诉讼 / 关联交易（一季报缺失时显式标注沿用上次年报）
6. **结论差异清单**：上次结论 → 本次结论 → 是否改变 → 证据 → 置信度
7. 监控项兑现情况与风险更新
8. 数据缺口与降级说明

> **上下文预算（2026-09-20 实跑实测，REQ-006.1 AC-1.5）**：`synthesis` 默认 40,000 字符
> （四个核心模块实测 30,124，含 D7 是 38,070）；`change_report` 默认 160,000 字符
> （D7 单卡就要 26,141，「本期 + 上次」两卡俱全实测约 150k）。装不下时**不静默**：
> `synthesis` 在 `budget.dropped` / `dropped_count` 里列出被丢模块与字段样本，
> `change_report` 在 `degraded` 里记降级项（含结论条数），两者都会在 CLI 打印出来。

---

## 8. 迭代管理（PR3）

### 8.1 框架版本指纹

新增 `scripts/version.py`：

- `FRAMEWORK_VERSION`（与 CHANGELOG / 语义化版本一致）；
- `prompt_fingerprint`：对 `strategies/**`、`shared/qualitative/**`、`.claude/commands/**`、`.opencode/commands/**` 排序后取 sha256；
- `code_fingerprint`：git commit + dirty 标记，退化时哈希 `scripts/**/*.py`；
- `schema_versions`：`investment.result` / `investment.manifest` / `investment.evidence_index` 的版本。

`manifest.build_manifest()` 增加可选 `framework` 块（**additive**，`schema_version` 仍为 `1.0`，老消费者不受影响）。

### 8.2 台账 `history.jsonl`（每行一个 run）

```json
{
  "run_id": "20260919T221000000Z",
  "kind": "baseline|report-update|framework-update|market-refresh|rerun",
  "created_at": "2026-09-19T22:10:00Z",
  "supersedes": "20260913T152947378594Z",
  "trigger": {"type": "new_report", "periods": ["2026H1"], "detected_by": "analysis_status"},
  "subject": {"ticker": "600887.SH", "company": "伊利股份", "market": "CN"},
  "report_periods": ["2021FY", "2022FY", "2023FY", "2024FY", "2025FY", "2026Q1", "2026H1"],
  "primary_period": "2026H1",
  "framework": {
    "version": "0.2.0", "git_commit": "8adf944", "dirty": false,
    "prompt_fingerprint": "sha256:...", "schema_versions": {"result": "1.0"}
  },
  "input_digest": "...",
  "status": "complete|partial|failed",
  "conclusions_changed": ["moat_rating 强→较强"],
  "artifacts": {"report": "...", "change_report": "...", "qualitative_input": "..."}
}
```

`latest.json`：`{run_id, primary_period, published_at, run_dir, artifacts{}}`。

### 8.3 状态判定器 `scripts/analysis_status.py`

```
状态：no_record | up_to_date | stale(new_report | inputs_changed | framework_changed | schema_changed) | broken | unsupported_market
输出：recommended_action(none | report-update | full-rerun) + required_scope + blocking + reasons(JSON)
退出码：0 最新 / 1 需增量更新 / 2 参数错误 / 3 需全量重跑（沿用 resolve_qualitative 的 3=需处理约定）
批量：--root output --all --json → 全仓 worklist（框架升级后的重跑清单）
```

判定输入：`record.json` / `history.jsonl`、当前 `sources/` 期次、上游是否有新期次（可选联网）、框架指纹、schema 版本。这是「要不要重跑、跑哪一级」的唯一决策点。

### 8.4 消费方解析

- 新增 `scripts/runs.py resolve --company-dir X [--latest | --run-id ID]` → 输出 run 目录绝对路径。
- `resolve_qualitative --output-dir` **只接受 run 目录**（含 `run_manifest.json`）或 legacy 扁平目录；它不解析 `latest.json`。公司目录必须先 `runs.py resolve --latest` 得到 run 目录再传入。
- `/value-analysis` 已按上述方式接线（先 `runs.py resolve --latest`，再对 `{run_dir}` 调用 resolver）；`/valuation` 与 `/buy-sell-plan` **尚未接线**，仍按 legacy/run 目录约定读取，属已知未完成项。

### 8.5 迁移既有目录

`scripts/runs.py adopt --company-dir output/600887_伊利`：

- 从 `run_manifest.json` + PDF 文件名 + `pdf_sections.json:metadata.pdf_file` 推断 `report_periods`；
- 把现有产物复制进 `runs/{baseline_run_id}/`，写 `latest.json` / `record.json` / 台账；
- 默认保留原文件；`--prune` 才清理旧布局（在复制逐项校验通过后执行，随后仍会写 `latest.json`/`record.json`）。
- 接管时会同步重写 `input_digest` 并重盖被改写产物的哈希；若源目录的产物与 manifest 记录不一致则**拒绝接管**（避免洗白篡改）。

legacy 扁平目录（无 manifest）标 `legacy_layout`；`/business-analysis` 不建 run，转正需显式运行 `runs.py adopt`（或走 `/update-analysis` 的增量流程）。

### 8.6 与既有原则的兼容

- 「整组原子回退」不变：`resolve_qualitative` 仍只认一个 run，`source=legacy/unavailable` 与退出码不变。
- 新增硬约束：**跨 run 只通过 `supersedes` 台账与变化报告发生关系，绝不混用两个 run 的参数**。
- `output/` 已 gitignore，台账随输出留本地；如需跨机共享，`scripts/runs.py export --format md` 只导出轻量摘要（不含 PDF 与报告全文）。

---

## 9. 测试与契约

- 单测：period 解析与互转、CNINFO 四类发现、`auto` 补齐、`analysis_status` 状态机、`runs.py`（new / resolve / finish / adopt / 指针 / 台账）。
- 合同测试：新 `qualitative.period_delta` 与 `output_schema.md` 不漂移；保证 `MODULE_CONFIG` ↔ `RESULT_TYPE_CONTRACTS` ↔ 提示词三方一致（沿用 `tests/test_qualitative_consumers.py` 模式）。
- 端到端（mock）：baseline run → 注入 `2026H1` PDF → 增量 run → 校验旧 run 仍可解析、新 run `source=structured`、`change_report_2026H1.md` 生成、台账追加记录、`latest.json`/`record.json` 指针更新并可用 `analysis_status` 复判。
- 失败路径：期次未发布、PDF 部分失败、manifest 篡改、框架指纹变化触发全量、新增 PDF **不影响**旧 run 的可校验性。
- 快照测试：默认真实复制（覆盖「原地改写源文件后快照不变」）、`--hardlink` 显式 opt-in、`os.link` 失败退回复制、复制失败时不留半成品 run。

---

## 10. 分批 PR 计划

遵循仓库约定：`main` 受保护、PR-only、Conventional Commits、测试全 mock 不依赖 Token。

| PR | 分支 | 内容 | 依赖 |
|----|------|------|------|
| 1 | `feat/periodic-report-discovery` | CNINFO 四类 category + 最新期次探测 + `scripts/periods.py` + CLI `--latest/auto` + `sources_index.json` + 测试 | 无（独立收益即可先合） |
| 2 | `feat/comparable-periods` | 数据包补上年同期列 + per-period `pdf_sections` + `prepare --primary-period`（兼容 `inputs/` 快照） | PR1 |
| 3 | `feat/run-history-ledger` | `scripts/version.py` + `scripts/runs.py`（new/resolve/finish/adopt/export） + `analysis_status.py` + manifest `framework` 块 + 测试 | 无（可与 1/2 并行） |
| 4 | `feat/period-delta-analysis` | `period_delta` 契约/提示词/context + `prior_analysis` 证据源 + 更新合成 + 变化报告模板 + `/update-analysis` 命令（`.claude/` 与 `.opencode/` 双份） | PR2 + PR3 |
| 5 | `docs/periodic-update` | ARCHITECTURE / README / CHANGELOG 更新 + 下游 staleness 标记与命令接线 | PR4 |

---

## 11. 风险与取舍

| 风险 | 应对 |
|------|------|
| CNINFO 改版或限流 | 保留 10jqka 兜底；指数退避重试；全部 mock 测试；失败明确报「期次未获取」，不静默降级 |
| 一季报 / 三季报内容单薄，强行分析会失真 | 变化不大的维度沿用上次年报证据并标注；变化报告显式写「本期未披露」 |
| 四模块全量重跑成本高 | 一期正确性优先；`--light` 结转模式留作二期可选，不在一期放宽 resolver 约束 |
| 快照磁盘占用（默认真实复制） | 用复制保证旧 run 不被原地刷新污染（实现期评审发现硬链接会被 `pdf_sections.json` / `data_pack_market.md` 的原地覆盖写击穿）；`--hardlink` 仅作显式 opt-in |
| run-store 重构触及全部命令路径 | 用 `runs.py resolve` 统一解析出 run 目录；`resolve_qualitative` 只接受 run 目录或 legacy 扁平目录（**不解析 `latest.json`**）。`/value-analysis` 已接线；`/valuation` 与 `/buy-sell-plan` 尚未接线，期间旧布局仍可读 |
| 根目录镜像 `published/` 与 run 产物不一致 | 合同测试断言哈希一致；`published/` 明确标注为派生、不作为输入 |

---

## 12. 开放问题

1. `--light` 结转模式是否在二期实现（当前默认全量重跑）。
2. `runs/` 是否需要在保留策略上做上限（例如仅保留最近 N 个 run 的输入快照，PDF 用引用计数回收）。
3. 港股 / 美股定期报告 PDF 的通路（yfinance 财报、港交所披露易）是否在后续版本纳入。
