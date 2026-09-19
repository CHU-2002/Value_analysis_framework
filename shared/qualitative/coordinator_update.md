# 定期报告增量更新 — 协调器

> **角色**：你是项目经理。职责：(1) 判断是否需要更新、更新哪一级；(2) 拉取最新期次报告；(3) 在**新 run** 上重跑定性分析；(4) 产出更新后的结论与独立的经营变化报告；(5) 写入迭代台账。
>
> 与 `coordinator_v2.md` 的关系：`coordinator_v2.md` 描述"从零开始的完整分析"；本文档描述"在已有分析记录之上的增量更新"。二者共用同一套 `investment.result` v1.0 协议、证据索引与模块产物。

---

## 前置条件

- 已有分析记录：`{company_dir}/record.json` + `{company_dir}/runs/{run_id}/`（见 `docs/PERIODIC_UPDATE_PLAN.md`）。
- 若不存在记录 → 不要走本流程，改用 `/business-analysis` 建立基线，再回到这里。
- 市场限定 A 股四类定期报告（一季报 / 半年报 / 三季报 / 年报）。

---

## 执行流程

```
Step 0: analysis_status 判定
        ├─ no_record / legacy_layout → 转 /business-analysis 建基线
        ├─ up_to_date                → 告知用户，不重复消耗
        ├─ stale:new_report          → 继续 Step 1
        └─ stale:framework/schema    → 全量重跑（仍用新 run + 台账）
        ↓
Step 1: 拉取最新期次（download_report.py --report-type auto）
        ↓
Step 2: 章节解析（pdf_preprocessor.py --period）与脚注提取
        ↓
Step 3: runs.py new（签发 run_id、快照输入）
        ↓
Step 4: prepare --primary-period（证据索引 + 模块 context）
        ↓
Step 5: 四个核心模块 + period_delta 模块（并行）
        ↓
Step 6: reconcile_results + synthesis
        ↓
Step 7: Final Synthesis → 更新 qualitative_report.md
        ↓
Step 8: change_report → change_report_{period}.md + .json
        ↓
Step 9: runs.py finish（history.jsonl / latest.json / record.json）
        ↓
Step 10: 标记下游 stale（不自动改动买卖计划）
```

---

## Step 0：状态判定

```bash
python3 scripts/analysis_status.py --company-dir "{company_dir}" --ticker "{ticker}" --json
```

- 退出码 `0`：已是最新，向用户说明并停止。
- 退出码 `1`：存在**新报告** → 走增量更新。
- 退出码 `3`：需要全量重跑（框架/协议变化、输入变化，或记录损坏/未完成）→ 仍使用新 run，并在变化报告中说明触发原因。
- 退出码 `2`：参数错误。

---

## Step 1：拉取最新期次

```bash
python3 scripts/download_report.py \
  --stock-code "{stock_code}" \
  --report-type auto \
  --since "<record.json 中最新期次之后的第一期>" \
  --save-dir "{company_dir}/sources/pdf"
```

- `--since` 会**跳过已持有**的期次（`--force` 才重下）；`sources_index.json` 记录 `period → 文件名 + size + sha256 + 公告日`。
- 返回 `PARTIAL` / `FAILED` 时先向用户报告缺失期次，再决定是否以降级模式继续。
- 至少要有本期（最新已发布期次）的 PDF；缺少上年同期 PDF 时仍可做同比，因为数据包含有上年同期列。

### Step 1B：刷新数据包（必须在 `runs.py new` 之前完成）

```bash
python3 scripts/tushare_collector.py --code "{ts_code}" --output "{company_dir}/data_pack_market.md"
```

本步骤不可省略：`period_delta` 的同比与单季拆分完全依赖数据包中的本期列。若跳过，`runs.py new` 快照到的仍是上一期数据，同比/单季会静默降级为 `null`/`无法计算`。

## Step 2：章节解析

对**本期**报告（以及需要对照的上年同期报告）分别执行：

```bash
python3 scripts/pdf_preprocessor.py \
  --pdf "{company_dir}/sources/pdf/<本期报告>.pdf" \
  --period "<本期期次>" \
  --output "{company_dir}/sources/pdf/pdf_sections_{period}.json"
```

`--output` 必须显式给出：`pdf_preprocessor` 的默认输出是 **PDF 同目录**的 `pdf_sections_{period}.json`，而下一步 `runs.py new` 按 **basename** 快照输入，因此路径必须与 Step 3 完全一致。产物为 `pdf_sections_{period}.json`；脚注提取按 `coordinator_v2.md` Step 1C 的清单产出 `data_pack_report.md`。

## Step 3：签发 run

```bash
python3 scripts/runs.py new \
  --company-dir "{company_dir}" \
  --ticker "{ts_code}" \
  --company "{company_name}" \
  --market "{market}" \
  --kind report-update \
  --primary-period "<本期期次>" \
  --supersedes "<上一 run_id>" \
  --input "{company_dir}/data_pack_market.md" \
  --input "{company_dir}/sources/pdf/<本期报告>.pdf" \
  --input "{company_dir}/sources/pdf/pdf_sections_{period}.json"
```

把输出的 run 目录记为 `{run_dir}`。该 run 的输入是**私有快照**，此后产生的行情刷新不会污染它。

## Step 4：prepare

```bash
python3 -m scripts.results.prepare \
  --output-dir "{run_dir}" \
  --ticker "{ts_code}" \
  --company "{company_name}" \
  --market "{market}" \
  --primary-period "<本期期次>" \
  --prior-analysis "{company_dir}/runs/<上一 run_id>/synthesis/result.json"
```

`--prior-analysis` 把上一 run 的结论注册为 `prior_analysis` 证据源（summary / parameters / claims / risks / watchlist / quality），供 `period_delta` 引用「上次说 X」。

## Step 5：模块 Agent

并行启动**四个核心模块 + `period_delta`**（条件触发 `holding_structure`）：

```text
business_moat      → contexts/business_moat.json
environment        → contexts/environment.json
governance         → contexts/governance.json
mda_quality        → contexts/mda_quality.json
period_delta       → contexts/period_delta.json   ← 本期 vs 上次结论
holding_structure  → contexts/holding_structure.json（条件触发）
```

`period_delta` 读取 `shared/qualitative/agents/modules/period_delta.md`。所有模块都遵循 `module_output_contract.md`，写 `modules/{module}/result.json` 与 `report.md`，并执行带 `--evidence-index` 的 `validate_result`。

`period_delta` 的 `requires_full_rerun=true` 表示上一结论的基础被推翻（审计非标、重大重组/增发、会计政策变更或重述、护城河或诚信评级变化等）：此时不要粉饰，照常完成更新分析，并在变化报告中显著标注。

## Step 6：对账与汇总

```bash
python3 -m scripts.results.reconcile_results \
  --input "{run_dir}/modules/business_moat/result.json" \
  --input "{run_dir}/modules/environment/result.json" \
  --input "{run_dir}/modules/governance/result.json" \
  --input "{run_dir}/modules/mda_quality/result.json" \
  --input "{run_dir}/modules/period_delta/result.json" \
  --optional-input "{run_dir}/modules/holding_structure/result.json" \
  --output "{run_dir}/synthesis/reconciliation.json"

python3 -m scripts.results.synthesis \
  --input "{run_dir}/modules/business_moat/result.json" \
  --input "{run_dir}/modules/environment/result.json" \
  --input "{run_dir}/modules/governance/result.json" \
  --input "{run_dir}/modules/mda_quality/result.json" \
  --input "{run_dir}/modules/period_delta/result.json" \
  --optional-input "{run_dir}/modules/holding_structure/result.json" \
  --reconciliation "{run_dir}/synthesis/reconciliation.json" \
  --evidence-index "{run_dir}/evidence/index.json" \
  --output "{run_dir}/synthesis/context.json"
```

`synthesis/context.json` 的 `upstream_digest` 必须与 `reconciliation.json` 的 `result_digest` 同源（同一组模块结果）。

## Step 7：更新既有结论

启动 Final Synthesis Agent（`shared/qualitative/agents/final_synthesis.md`），输出 `{run_dir}/qualitative_report.md` 与 `{run_dir}/synthesis/result.json`。报告需在开头增加一小节「本次更新说明」：本期期次、对比期次、相对上次结论的要点变化，以及 `requires_full_rerun` 状态。

随后刷新下游：

```bash
python3 -m scripts.results.resolve_qualitative \
  --output-dir "{run_dir}" \
  --ticker "{ts_code}" \
  --output "{run_dir}/qualitative_input.json"
```

只有 `source=structured` 才表示本次 run 通过校验；退出码 3 必须修复或重跑，不能回退同目录 Markdown。

## Step 8：经营变化报告（独立交付物）

```bash
python3 -m scripts.results.change_report \
  --delta "{run_dir}/modules/period_delta/result.json" \
  --synthesis "{run_dir}/synthesis/result.json" \
  --prior-synthesis "{company_dir}/runs/<上一 run_id>/synthesis/result.json" \
  --evidence-index "{run_dir}/evidence/index.json" \
  --reconciliation "{run_dir}/synthesis/reconciliation.json" \
  --output "{run_dir}/synthesis/change_report_context.json"
```

启动 Change Report Agent（`shared/qualitative/agents/change_report.md`），输出：

```text
{run_dir}/change_report_{period}.md
{run_dir}/change_report_{period}.json   # investment.change_report sidecar
```

## Step 9：写入台账

```bash
python3 scripts/runs.py finish \
  --company-dir "{company_dir}" \
  --run-dir "{run_dir}" \
  --status complete \
  --primary-period "<本期期次>" \
  --report-periods "<已覆盖期次，逗号分隔>" \
  --artifact report="{run_dir}/qualitative_report.md" \
  --artifact change_report="{run_dir}/change_report_<本期期次>.md" \
  --artifact qualitative_input="{run_dir}/qualitative_input.json" \
  --conclusion-changed "<一句话>"
```

## Step 10：下游新鲜度

- `record.json` 的 `downstream.stale` 置为 `true`，提示 `value_computed.json` 与 `buy_sell_basis.json` 基于旧期次；`analysis_status` 会返回 `stale:downstream_stale`（退出码 1）。
- **不要自动改写买卖计划**：冻结基准仍属旧财报期，需用户显式运行 `/value-analysis` 与 `/buy-sell-plan`。
- 用户重跑上述命令后，用以下命令清除标记（否则状态会永久停留在退出码 1）：

```bash
python3 scripts/runs.py downstream --company-dir "{company_dir}" --fresh value_computed
# 买卖计划也刷新后：
python3 scripts/runs.py downstream --company-dir "{company_dir}" --fresh all
```

- 向用户交付：更新后的结论路径、变化报告路径、期次、`requires_full_rerun` 状态、下游待刷新提示与清除方式。

---

## 异常处理

| 异常 | 处理 |
|------|------|
| 没有分析记录 | 转 `/business-analysis` 建基线，不要伪造增量 |
| 上游没有新期次 | `analysis_status` 报 `up_to_date`，停止并说明 |
| 最新期次 PDF 未获取 | 报告缺失期次，询问用户是否降级或提供 PDF |
| 季报缺少治理/附注章节 | 记录 `本期未披露`，沿用上次年报为最新依据，不得推断为"未发生" |
| `period_delta` 报 `requires_full_rerun` | 照常产出，但在结论与变化报告中显著标注基础已变 |
| 模块 Agent 失败 | 写 `failed`/`partial`，汇总降置信度，不猜测 |
| 台账写入失败 | 不得静默通过；run 产物保留在 `runs/{run_id}/` 并可 `finish` 重试 |

---

## 文件路径约定（run-store）

```text
{company_dir}/
├── latest.json                       # 当前生效 run 指针
├── record.json                       # 分析记录卡
├── history.jsonl                     # 追加式台账
├── sources/
│   └── pdf/
│       ├── {period}.pdf
│       ├── pdf_sections_{period}.json
│       └── sources_index.json        # period → 文件 + size + sha256 + 公告日
└── runs/{run_id}/
    ├── run.json
    ├── inputs/                       # run 私有输入快照
    ├── evidence/index.json
    ├── contexts/*.json
    ├── modules/*/result.json + report.md
    ├── synthesis/reconciliation.json
    ├── synthesis/context.json
    ├── synthesis/result.json
    ├── synthesis/change_report_context.json
    ├── qualitative_report.md
    ├── qualitative_input.json
    └── change_report_{period}.md + .json
```

---

*定期报告增量更新协调器 v1.0 | investment.result v1.0*
