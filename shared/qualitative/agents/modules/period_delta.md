# Module Agent: Periodic Report Delta (定期报告经营变化)

Follow `../module_output_contract.md`.

## Scope

- D7: what a newly published regular report (一季报 / 半年报 / 三季报 / 年报) changed relative to the previous recorded analysis, and whether the previous conclusions still hold.

This module only runs inside the `/update-analysis` incremental pipeline. In a fresh `/business-analysis` run it is not produced.

## Method

Read `contexts/period_delta.json`. It contains three groups of material:

1. **This period's financials** (`Market data:` blocks) — the latest cumulative period plus its prior-year comparable period. `Q1` / `H1` / `Q3` are year-to-date cumulative, so:
   - 同比 = current cumulative vs the same cumulative period one year earlier;
   - 单季 = current cumulative minus the previous cumulative period of the same fiscal year (`H1−Q1`, `Q3−H1`, `FY−Q3`) — **only when the pack actually carries that previous cumulative column**. For an annual primary period the pack normally carries annual columns only, so the single-quarter figure cannot be derived: report `无法计算` and use `null` instead of inventing a quarter. Never present a single-quarter figure without stating the subtraction.
   - Do not compare an interim period against a full year.
2. **This period's report text** (`PDF` blocks) — MD&A and, when the period discloses them, 重要事项 and footnotes. Quarterly reports usually omit governance and footnotes; when a section is absent, say so instead of inferring.
3. **The previous analysis** (`prior_analysis` evidence) — the prior run's synthesis summary, parameters, claims, risks, watchlist and quality warnings. These are the conclusions you are updating.

Separate `fact`, `inference` and `judgement` in `claims`. Every change you assert must cite evidence: this period's data/PDF for the new fact, and `prior_analysis:*` for what the previous conclusion was.

## Required parameters

```text
report_period, comparable_period, business_trend, conclusion_change,
change_significance, guidance_delivery, requires_full_rerun,
revenue_yoy_pct, net_profit_yoy_pct, gross_margin_change_pct,
operating_cashflow_to_profit
```

Rules:

- `report_period` / `comparable_period` are period identifiers such as `2026H1` and `2025H1`. The context has no dedicated period field: derive them from the `Market data:` column headers in `context_text` rather than inventing them, and state them in `metrics`.
- `business_trend` describes the operating trajectory, not the share price.
  **基准（必须遵守，REQ-006.2 发现 F30）**：它比较的是**同一财报口径下的上年同期**，
  即 `report_period` vs `comparable_period`（本期累计 vs 上年同期累计，例如
  `2026H1` vs `2025H1`）。**不是**本轮结论与上一轮结论之间的相对变化（那是
  `conclusion_change` 的语义），也**不是**与上一次 run 相比「变好了还是变差了」。
  同一份 2026H1 数据、不管跑几次，`business_trend` 的基准都必须一样；若上年同期列不可得,
  写 `不确定` 并在 `quality.missing_inputs` 说明，不要退化成「跟上一次 run 比」。
  判定时要写出依据（同比了哪两个期次、看的是哪些指标），跨期或跨口径的比较不得作为依据。
- `conclusion_change` is your judgement about the *previous* recorded conclusions: `维持` (unchanged), `上调` (more favourable), `下调` (less favourable), `证据不足` (cannot tell from this period).
- `guidance_delivery` compares the previous run's `mda_forward_guidance` / promises / watchlist against what this period actually shows. Use `无指引` when the previous run recorded no guidance, and `无法验证` when this period cannot confirm or refute it.
- `requires_full_rerun` is `true` when the delta invalidates the basis of the previous analysis rather than merely updating numbers: non-standard audit opinion, material M&A / equity issuance / restructuring, accounting policy change or restatement, a change in moat or integrity rating, or a change in the dominant business mix. Then say why in `quality.warnings` and in a claim.
- Numeric parameters must come from the supplied data. Use `null` when the input cannot support them and record the reason in `quality.missing_inputs` (the module contract reserves that field for omitted parameters); never estimate.

## Required output

Beyond the standard contract, include in `metrics` at least: the current and comparable period labels, revenue / net profit 同比百分数, gross-margin change in percentage points, and operating-cash-flow-to-net-profit. Include the single-quarter figures and their subtraction only when the required previous cumulative column exists; otherwise state `无法计算` in `quality.missing_inputs`. Keep units and periods attached to every metric.

Put the most decision-relevant change first in `claims` and order `risks` by materiality. Add to `watchlist` the items that the next period should verify.

Write `modules/period_delta/report.md` and `modules/period_delta/result.json`.
