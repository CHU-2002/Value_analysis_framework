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
| 测试文件数 | 35 | 48 | 73% |
| 收集到的用例数 | 1609 | 1800 | 89% |

（用例数含 `parametrize` 展开，由 `pytest --collect-only` 统计；函数数见下表末列，仅作参考。）

## 登记表

| 测试文件 | 归属需求 | 层 | 被测对象 | 测试函数数 |
|----------|----------|----|----------|------------|
| `tests/test_analysis_status.py` | REQ-003 | `unit` | `scripts/analysis_status.py` | 24 |
| `tests/test_buy_sell_engine.py` | 基线 | `unit` | `scripts/buy_sell_engine.py` | 45 |
| `tests/test_change_report.py` | REQ-004, REQ-006.1 | `unit` | — | 21 |
| `tests/test_comparable_periods.py` | REQ-002 | `unit` | — | 39 |
| `tests/test_config.py` | 基线 | `unit` | `scripts/config.py` | 43 |
| `tests/test_coordinator.py` | 基线 | `unit` | — | 9 |
| `tests/test_derived_metrics.py` | 基线 | `unit` | — | 83 |
| `tests/test_discover_report.py` | REQ-001, REQ-006.1 | `unit` | `scripts/discover_report.py` | 62 |
| `tests/test_download_report.py` | REQ-001 | `unit` | `scripts/download_report.py` | 85 |
| `tests/test_format_utils.py` | 基线 | `unit` | `scripts/format_utils.py` | 21 |
| `tests/test_integration.py` | 基线 | `integration` | — | 3 |
| `tests/test_output_format.py` | 基线 | `unit` | — | 22 |
| `tests/test_pdf_preprocessor.py` | REQ-006.2 | `unit` | `scripts/pdf_preprocessor.py` | 90 |
| `tests/test_period_delta_module.py` | REQ-004, REQ-006.2 | `unit` | — | 20 |
| `tests/test_periods.py` | REQ-001 | `unit` | `scripts/periods.py` | 24 |
| `tests/test_phase1b_prompt.py` | 基线 | `unit` | — | 23 |
| `tests/test_phase2b_prompt.py` | 基线 | `unit` | — | 20 |
| `tests/test_phase3_prompt.py` | 基线 | `unit` | — | 76 |
| `tests/test_portfolio_engine.py` | 基线 | `unit` | `scripts/portfolio_engine.py` | 9 |
| `tests/test_prepare_primary_period.py` | REQ-002, REQ-006.2 | `unit` | — | 21 |
| `tests/test_prepare_prior_analysis.py` | REQ-004 | `unit` | — | 6 |
| `tests/test_qualitative_consumers.py` | 基线 | `contract` | — | 10 |
| `tests/test_refresh_market.py` | 基线 | `unit` | — | 23 |
| `tests/test_release_gates.py` | REQ-003, REQ-005, REQ-006, REQ-006.1 | `unit` | — | 37 |
| `tests/test_requirement_traceability.py` | 基线 | `unit` | — | 17 |
| `tests/test_results_pipeline.py` | REQ-006.1, REQ-006.2 | `e2e` | — | 85 |
| `tests/test_runs_ledger.py` | REQ-003, REQ-006.1 | `unit` | — | 38 |
| `tests/test_screener.py` | REQ-006.1 | `unit` | — | 99 |
| `tests/test_test_scope.py` | REQ-003, REQ-006 | `unit` | `scripts/test_scope.py` | 16 |
| `tests/test_tushare_client.py` | REQ-006.1, REQ-006.2 | `unit` | — | 212 |
| `tests/test_tushare_pack_sections.py` | REQ-006.2 | `unit` | — | 13 |
| `tests/test_two_layout_e2e.py` | REQ-005, REQ-006 | `unit` | — | 5 |
| `tests/test_update_docs_contract.py` | REQ-005, REQ-006, REQ-006.1 | `unit` | — | 20 |
| `tests/test_version.py` | REQ-003 | `unit` | `scripts/version.py` | 14 |
| `tests/test_webui_framework.py` | REQ-009.3 | `unit` | — | 42 |

归属为「基线」的测试覆盖需求体系建立前就已交付的能力，见
[`docs/requirements/ledger.md`](requirements/ledger.md) 的「已交付基线」小节。
把它们补齐到具体需求属于 Inbox 事项。
归属列可以写父需求 `REQ-NNN`，也可以写子需求 `REQ-NNN.S`，见 [`docs/requirements/README.md`](requirements/README.md) §6.1。
