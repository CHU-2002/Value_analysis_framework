# 测试 Scope 登记表

> 本文件由 `python scripts/test_scope.py --write` 生成，**请勿手工编辑**。
> CI 用 `python scripts/test_scope.py --check` 校验它与实际测试文件一致，并检查预算。

## 为什么要有这张表

CI 对每个 PR 都跑**全量**测试：只有全量才能发现「新功能踩坏别处」。
因此控制 CI 成本的方向不是裁剪单个 PR 的测试范围，而是**长期维护整体 scope**——
每支测试文件都要能说清它为什么存在、归属哪条需求；冗余的合并掉，过时的删掉。
预算见下一节：涨到接近上限时，先清理，而不是直接调高。

## 预算

| 项 | 当前 | 上限 | 使用率 |
|----|------|------|--------|
| 测试文件数 | 32 | 40 | 80% |
| 收集到的用例数 | 1447 | 1600 | 90% |

（用例数含 `parametrize` 展开，由 `pytest --collect-only` 统计；函数数见下表末列，仅作参考。）

## 登记表

| 测试文件 | 归属需求 | 层 | 被测对象 | 测试函数数 |
|----------|----------|----|----------|------------|
| `tests/test_analysis_status.py` | REQ-003 | `unit` | `scripts/analysis_status.py` | 24 |
| `tests/test_buy_sell_engine.py` | 基线 | `unit` | `scripts/buy_sell_engine.py` | 45 |
| `tests/test_change_report.py` | REQ-004 | `unit` | — | 19 |
| `tests/test_comparable_periods.py` | REQ-002 | `unit` | — | 39 |
| `tests/test_config.py` | 基线 | `unit` | `scripts/config.py` | 43 |
| `tests/test_coordinator.py` | 基线 | `unit` | — | 9 |
| `tests/test_derived_metrics.py` | 基线 | `unit` | — | 83 |
| `tests/test_discover_report.py` | REQ-001 | `unit` | `scripts/discover_report.py` | 52 |
| `tests/test_download_report.py` | REQ-001 | `unit` | `scripts/download_report.py` | 85 |
| `tests/test_format_utils.py` | 基线 | `unit` | `scripts/format_utils.py` | 21 |
| `tests/test_integration.py` | 基线 | `integration` | — | 3 |
| `tests/test_output_format.py` | 基线 | `unit` | — | 22 |
| `tests/test_pdf_preprocessor.py` | 基线 | `unit` | `scripts/pdf_preprocessor.py` | 85 |
| `tests/test_period_delta_module.py` | REQ-004 | `unit` | — | 20 |
| `tests/test_periods.py` | REQ-001 | `unit` | `scripts/periods.py` | 24 |
| `tests/test_phase1b_prompt.py` | 基线 | `unit` | — | 23 |
| `tests/test_phase2b_prompt.py` | 基线 | `unit` | — | 19 |
| `tests/test_phase3_prompt.py` | 基线 | `unit` | — | 76 |
| `tests/test_portfolio_engine.py` | 基线 | `unit` | `scripts/portfolio_engine.py` | 9 |
| `tests/test_prepare_primary_period.py` | REQ-002 | `unit` | — | 21 |
| `tests/test_prepare_prior_analysis.py` | REQ-004 | `unit` | — | 6 |
| `tests/test_qualitative_consumers.py` | 基线 | `contract` | — | 10 |
| `tests/test_refresh_market.py` | 基线 | `unit` | — | 23 |
| `tests/test_release_gates.py` | REQ-003, REQ-005, REQ-006, REQ-999 | `unit` | — | 26 |
| `tests/test_requirement_traceability.py` | 基线 | `unit` | — | 8 |
| `tests/test_results_pipeline.py` | 基线 | `e2e` | — | 53 |
| `tests/test_runs_ledger.py` | REQ-003 | `unit` | — | 35 |
| `tests/test_screener.py` | 基线 | `unit` | — | 96 |
| `tests/test_test_scope.py` | REQ-003, REQ-006 | `unit` | `scripts/test_scope.py` | 8 |
| `tests/test_tushare_client.py` | 基线 | `unit` | — | 207 |
| `tests/test_update_docs_contract.py` | REQ-005 | `unit` | — | 14 |
| `tests/test_version.py` | REQ-003 | `unit` | `scripts/version.py` | 14 |

归属为「基线」的测试覆盖需求体系建立前就已交付的能力，见
[`docs/requirements/ledger.md`](requirements/ledger.md) 的「已交付基线」小节。
把它们补齐到具体需求属于 Inbox 事项。
