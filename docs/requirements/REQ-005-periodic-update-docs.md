---
id: REQ-005
title: 增量更新文档与下游接线
status: implemented
priority: P2
owner: CHU-2002
created: 2026-09-20
updated: 2026-09-20
issue: "#20"
design: docs/PERIODIC_UPDATE_PLAN.md
milestone: periodic-update-v1
pr: "#22, #23"
depends-on: REQ-003, REQ-004
supersedes: TBD
---

# REQ-005 增量更新文档与下游接线

## 背景与问题

REQ-003 引入了 run-store、REQ-004 引入了增量流程，但**既有命令仍按旧的扁平路径读取产物**，
文档也没有描述新的数据流与 run 生命周期。若不同步，用户会看到「新能力已存在但命令用不上」，
或更糟：下游读到过期结论却不自知。

## 目标

- 让所有既有命令在 **legacy 与 run-store 两种布局**下都能正确工作。
- 让下游（估值结果、买卖计划）能判断自己基于哪一期、是否已过期。
- 把新数据流写入架构与用户文档。

## 验收标准

- **AC-1**：下游 staleness 接线——命令通过 `latest.json` / `record.json` 解析产物，不硬编码扁平路径；
  新期次到达后 `value_computed` / `buy_sell_basis` 被标记为 stale，且该标记对用户可见。
- **AC-2**：legacy 与 run-store 两种布局下，既有命令（价值分析、买卖计划、组合策略、结果解析）均可运行，
  行为与合入前一致；`prepare` 透传 `framework` 块。
- **AC-3**：`docs/ARCHITECTURE.md` 记录 run-store 数据流、run 生命周期与「跨 run 不混用参数」约束。
- **AC-4**：`README.md` 增加 `/update-analysis` 用法与期次说明；`CHANGELOG.md` 的 `[Unreleased]` 记录本期能力。
- **AC-5**：双布局端到端测试（mock baseline run → 注入新期次 → 增量 run → 校验旧命令仍可解析），
  作为本需求的验收证据。

## 范围

**包含**

- 命令路径解析接线与 staleness 标记
- ARCHITECTURE / README / CHANGELOG 更新
- 双布局兼容的端到端验证

**不包含**

- run-store 与增量分析本体（→ REQ-003、REQ-004）
- `runs/` 保留策略与 PDF 回收（Inbox：`docs/requirements/ledger.md`）

## 约束与依赖

- 依赖 REQ-003 与 REQ-004 合入；本需求只做接线与文档，不新增分析能力。
- 「要么全用新结果，要么整体退回旧报告」的产品承诺不得被接线破坏。

## 追溯

| 项 | 内容 |
|----|------|
| 设计文档 | `docs/PERIODIC_UPDATE_PLAN.md` §10（PR5）、§8.6 |
| 实现 PR | #22（`docs(update): 定期报告增量更新的文档与下游接线`）、#23（`docs(update): 补齐 run-store 门禁与 PR5 复核残留`） |
| 测试 | `tests/test_update_docs_contract.py`（契约：命令必须经 `runs.py resolve` 取 run_dir，不得把公司目录直接交给 resolver） |
| 文档更新 | `docs/ARCHITECTURE.md`、`README.md`、`CHANGELOG.md` |

## 验收记录

| 日期 | 基线 sha | 评审者 | 报告 | 结论 |
|------|----------|--------|------|------|
| 2026-09-20 | `397687f` | 独立 agent（无上下文，未参与实现） | [`docs/verification/2026-09-20-REQ-003-004-005.md`](../verification/2026-09-20-REQ-003-004-005.md) | **3/5 通过；AC-1、AC-2、AC-5 不成立**：`/valuation` 与 `/portfolio-strategy` 及其 coordinator 仍把公司目录交给不跟随 `latest.json` 的 resolver（run-store 布局下 `unavailable`），且缺双布局端到端测试。issue #20 保持开启，不置 `verified` |

## 备注

与本需求相关的两批改动已合入 `main`：`scripts/results/prepare.py` 透传 `framework`（#16/#17），
`docs/ARCHITECTURE.md` / `README.md` / `CHANGELOG.md` 补齐 run-store 数据流与 `/update-analysis` 用法，
并新增契约测试固定「命令必须经 `runs.py resolve` 取 run_dir」（#22/#23）。

验收时仍需逐条核对，特别是 AC-1 的 `value_computed` / `buy_sell_basis` staleness 标记是否对用户可见，
以及 AC-5 的双布局端到端（mock baseline → 注入新期次 → 增量 run → 旧命令仍可解析）。
