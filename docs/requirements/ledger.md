# 需求台账

> 本文件是全部需求的**单一索引**。规则见 [`README.md`](README.md)。
> `REQ-*.md` 文件集合与下表必须严格一致，由 `tests/test_requirement_traceability.py` 校验。
> 状态、关联 Issue、实现 PR 三者必须与代码同步更新。

## 需求台账

| ID | 标题 | 状态 | 优先级 | Issue | 实现 PR | 关联测试 |
|----|------|------|--------|-------|---------|----------|
| [REQ-001](REQ-001-periodic-report-discovery.md) | 定期报告发现与下载 | `verified` | P1 | N/A | #13 | `tests/test_discover_report.py` `tests/test_download_report.py` `tests/test_periods.py` |
| [REQ-002](REQ-002-comparable-periods.md) | 同比可比期与按期次章节包 | `verified` | P1 | N/A | #15 | `tests/test_comparable_periods.py` `tests/test_prepare_primary_period.py` |
| [REQ-003](REQ-003-run-history-ledger.md) | 分析迭代台账（run-store） | `verified` | P1 | #18 | #16 | `tests/test_version.py` `tests/test_runs_ledger.py` `tests/test_analysis_status.py` |
| [REQ-004](REQ-004-period-delta-analysis.md) | 定期报告增量更新分析与变化报告 | `verified` | P1 | #19 | #17 | `tests/test_period_delta_module.py` `tests/test_change_report.py` `tests/test_prepare_prior_analysis.py` |
| [REQ-005](REQ-005-periodic-update-docs.md) | 增量更新文档与下游接线 | `implemented` | P2 | #20 | #22, #23 | `tests/test_update_docs_contract.py` |
| [REQ-006](REQ-006-requirement-test-dev-flow.md) | 需求-测试-开发流程与三道门 | `in-progress` | P1 | TBD | TBD | `tests/test_release_gates.py` `tests/test_requirement_traceability.py` |

状态说明：`proposed` 已登记待受理 · `accepted` 已受理 · `in-progress` 实现中 · `implemented` 已合入待验收 · `verified` 已验收 · `deferred` 暂缓 · `rejected` 不做 · `superseded` 被取代。

## 里程碑

| 里程碑 | 目标 | 包含需求 | 状态 |
|--------|------|----------|------|
| 定期报告增量更新 v1 | 从「单次全量分析」升级为「按最新期次增量更新 + 可追溯迭代台账」 | REQ-003 → REQ-004 → REQ-005 | REQ-001…004 `verified`；REQ-005 独立验收 **3/5 未通过**，issue #20 保持开启 |

对应 GitHub Milestone：`periodic-update-v1`。

## 已交付基线（不单独立项）

以下能力在需求体系建立前已交付，作为既成事实登记，不回填 `REQ-*.md`；后续对它们的**修改**属于新需求，须走正常流程。

| 能力 | 证据 |
|------|------|
| 数据采集（Tushare / yfinance） | `scripts/tushare_collector.py`、`scripts/tushare_modules/` |
| 年报链接发现与下载 | `scripts/discover_report.py`、`scripts/download_report.py` |
| PDF 章节解析与脚注抽取 | `scripts/pdf_preprocessor.py` |
| 结构化定性结果管线（schema / manifest / evidence / resolver） | `scripts/results/` |
| 价值分析与通用估值引擎 | `scripts/value_analysis_engine.py`、`scripts/valuation_engine.py`、`strategies/value/` |
| 可执行买卖计划 | `scripts/buy_sell_engine.py`、`docs/BUY_SELL_CONTRACT.md` |
| 组合策略与选股器 | `scripts/portfolio_engine.py`、`scripts/screener_core.py` |
| 工程化基线（CI / 模板 / 分支保护 / 架构文档） | `.github/`、`CONTRIBUTING.md`、`docs/ARCHITECTURE.md` |

## 待登记想法（Inbox）

尚未达到 `proposed` 门槛（缺可判定验收标准）的想法先放这里，想清楚后再开 Issue 与 `REQ-*.md`。

| 想法 | 来源 | 备注 |
|------|------|------|
| `--light` 结转模式 | `docs/PERIODIC_UPDATE_PLAN.md` §12.1 | 需先量化「哪些模块可安全结转」 |
| `runs/` 保留策略与 PDF 引用计数回收 | `docs/PERIODIC_UPDATE_PLAN.md` §12.2 | 依赖 REQ-003 落地后再评估 |
| 港股 / 美股定期报告 PDF 通路 | `docs/PERIODIC_UPDATE_PLAN.md` §12.3 | 数据源与披露规则未定 |
| 覆盖率洼地补测（`valuation_engine.py` 34%、`portfolio_engine.py` 47%、`split_data_pack.py` 45%、`value_analysis_engine.py` 65%、`report_to_html.py` / `md_to_mobile_html.py` / `generate_available_fields.py` 0%） | `make cov` 基线 2026-09-20，总覆盖率 76.37% | 需先判定这些模块是否属于「一次性脚本」，再决定补测或标注豁免 |
