---
id: REQ-002
title: 同比可比期与按期次章节包
status: verified
priority: P1
owner: CHU-2002
created: 2026-09-20
updated: 2026-09-20
issue: N/A（源自设计文档 PR #12）
design: docs/PERIODIC_UPDATE_PLAN.md
milestone: periodic-update-v1
pr: "#15"
depends-on: REQ-001
supersedes: TBD
---

# REQ-002 同比可比期与按期次章节包

## 背景与问题

数据层只保留「晚于最近年报的非年报期 + 5 年年报」（`scripts/tushare_modules/infrastructure.py`），
所以 `2026H1` 场景拿不到 `2025H1`，**同比算不出来**。
同时 PDF 章节只落一份 `pdf_sections.json`，多期次并存时会互相覆盖，无法为每个期次保留独立证据。

## 目标

- 让数据包补齐「上年同期」列，使半年报 / 季报的同比口径可计算。
- 让章节解析结果按期次独立落盘，为多期次并存（REQ-004）提供证据基础。
- 在不破坏既有 `inputs/` 快照布局的前提下，支持显式指定主期次。

## 验收标准

- **AC-1**：数据包为 `2026H1` 这类期次补齐 `2025H1` 可比列；同比指标可由数据包直接算出，不依赖额外取数。
- **AC-2**：章节结果按期次落盘为 `pdf_sections_{period}.json`，不同期次互不覆盖；同时保留 `pdf_sections.json`
  作为主期次兼容副本，既有消费者行为不变。
- **AC-3**：`prepare --primary-period {period}` 可显式指定主期次；不传该参数时行为与既有调用完全一致。
- **AC-4**：既有 `inputs/` 快照布局仍被支持，既有测试无回归。

## 范围

**包含**

- 数据层可比期选择逻辑修正（保留上年同期）
- per-period `pdf_sections` 落盘与主期次兼容副本
- `prepare --primary-period` 参数

**不包含**

- run-store 台账（→ REQ-003）
- 增量更新与变化报告（→ REQ-004）

## 约束与依赖

- 依赖 REQ-001 的期次模型（`scripts/periods.py`）。
- 「整组原子回退」原则不得被破坏：resolver 仍只认一个 run、一个主期次。

## 追溯

| 项 | 内容 |
|----|------|
| 设计文档 | `docs/PERIODIC_UPDATE_PLAN.md` §6 |
| 实现 PR | #15（`feat(data): 上年同期可比列与按期次章节包`） |
| 测试 | `tests/test_comparable_periods.py`、`tests/test_prepare_primary_period.py` |
| 文档更新 | `CHANGELOG.md` |

## 验收记录

| 日期 | 依据 | 评审者 | 结论 |
|------|------|--------|------|
| 2026-09-19 / 2026-09-20 | 实现 PR ##15（合入 main 后逐条核对验收标准） | 实现者自查 | **全部 AC 成立**。覆盖 AC-1…AC-4（补上年同期可比列、per-period `pdf_sections_{period}.json` 与主期次兼容副本、`prepare --primary-period`、兼容 `inputs/` 快照） |

> 说明：本需求在「独立验收报告」机制（REQ-006）建立**之前**就已交付并核对，
> 因此没有 `docs/verification/` 报告；REQ-003 起才要求独立评审者的报告 + 验收戳。
> 保留这段记录是为了让台账的口径一致，不把「无报告」误读成「未验收」。

## 备注

无。
