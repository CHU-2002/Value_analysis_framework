---
id: REQ-004
title: 定期报告增量更新分析与变化报告
status: implemented
priority: P1
owner: CHU-2002
created: 2026-09-20
updated: 2026-09-20
issue: "#19"
design: docs/PERIODIC_UPDATE_PLAN.md
milestone: periodic-update-v1
pr: "#17（已合入 f5317f3）"
depends-on: REQ-002, REQ-003
supersedes: TBD
---

# REQ-004 定期报告增量更新分析与变化报告

## 背景与问题

拿到新一期财报后，系统只能重跑一次全量分析，**无法把新一期与既有结论对齐**：
既不知道哪些判断变了、变多少，也产不出一份「经营变化报告」。
用户需要的是「这家公司比上次分析时发生了什么变化」，而不是又一份从头写起的报告。

## 目标

- 新增 `/update-analysis {ticker} [period]`：无记录 → 全量基线；有记录 → 拉最新期次、增量分析、更新结论。
- 新增 `qualitative.period_delta` 结果类型与独立「经营变化报告」交付物。
- 让上一 run 的结论可作为本次分析的对照基准（`prior_analysis` 证据源）。

## 验收标准

- **AC-1**：新增 `qualitative.period_delta` 契约（scope `D7`，11 个参数：期次 / 可比期、经营趋势、结论调整、
  变化重要性、指引兑现、`requires_full_rerun`、收入与净利同比、毛利率变化、现金含量）；
  `MODULE_CONFIG` ↔ `RESULT_TYPE_CONTRACTS` ↔ `shared/qualitative/references/output_schema.md` 三方一致（合同测试）。
- **AC-2**：`context.py` 支持 `prior_analysis` 证据源，且在宽数据包场景下**优先选入**，
  不被 `market_data` / `pdf_sections` 挤占名额；缺失时通过 `selection.missing_prior_analysis` 可见。
- **AC-3**：`prepare --prior-analysis` 可把上一 run 的 `synthesis/result.json` 注册为证据源；
  该文件缺失时记 warning 而非失败。
- **AC-4**：变化报告对上一 run 的证据**降级而非拒绝**——上一 run 的 locator 在新数据包下必然失效，
  须逐条丢弃不可校验摘录并计数，在 `degraded.prior_evidence_unavailable` 与提示词中显式披露。
- **AC-5**：产出独立变化报告 `change_report_{period}.md` / `.json`（含 `investment.change_report` sidecar），
  并显式标注本期未披露的维度沿用上次证据。
- **AC-6**：`/update-analysis` 在 `.claude/commands/` 与 `.opencode/commands/` 中**逐字节一致**。
- **AC-7**：增量 run **不自动改写买卖计划**；`value_computed` / `buy_sell_basis` 仅被标记 stale；
  `period_delta` 只用于增量 run，全量 `/business-analysis` 不产出该模块。
- **AC-8**：全部测试使用 mock，不依赖网络与 Token；全量 pytest 相对 `main` 无回归。

## 范围

**包含**

- `period_delta` 契约、模块提示词、`prior_analysis` 证据源
- 变化报告上下文构建器 `scripts/results/change_report.py` 与变化报告提示词
- `/update-analysis` 命令（双份）与增量更新协调流程

**不包含**

- run-store 本身（→ REQ-003，本需求依赖）
- 文档与下游接线（→ REQ-005）
- `--light` 结转模式（本期全量重跑四模块）

## 约束与依赖

- 依赖 REQ-002（可比期 + `prepare --primary-period`）与 REQ-003（`runs.py` / `analysis_status.py`）。
- **合并顺序**（已按此执行）：REQ-002 #15 → REQ-003 #16（`ab635c8`）→ REQ-004 #17（`f5317f3`）。
- 不得放宽 resolver 的原子回退约束来换取增量便利。

## 追溯

| 项 | 内容 |
|----|------|
| 设计文档 | `docs/PERIODIC_UPDATE_PLAN.md` §7 |
| 实现 PR | #17（`feat(update): period_delta 模块、变化报告与 /update-analysis`，已合入 `f5317f3`） |
| 测试 | `tests/test_period_delta_module.py`、`tests/test_change_report.py`、`tests/test_prepare_prior_analysis.py` |
| 文档更新 | 待 REQ-005 |

## 备注

开发中已用真实数据暴露并修复两个缺陷：`prior_analysis` 被宽数据包饿死；变化报告要求上一 run 证据逐字匹配导致必然失败。
两条都已固化为 AC-2 与 AC-4。

本需求实现已随 PR #17 合入 `main`（`f5317f3`），状态推进到 `implemented`；
待逐条核对 AC-1…AC-8 后转为 `verified` 并关闭 #19。
