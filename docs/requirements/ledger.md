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
| [REQ-005](REQ-005-periodic-update-docs.md) | 增量更新文档与下游接线 | `verified` | P2 | #20 | #22, #23, #27 | `tests/test_update_docs_contract.py` `tests/test_two_layout_e2e.py` |
| [REQ-006](REQ-006-requirement-test-dev-flow.md) | 工程化开发流程（建立与持续维护） | `in-progress` | P1 | N/A | #25, #29, #30, #31, #32, #34, #35, #36, #37 | `tests/test_release_gates.py` `tests/test_test_scope.py` `tests/test_update_docs_contract.py` `tests/test_two_layout_e2e.py`（追溯门禁自身即扫描器，按设计排除，故不列入声明） |
| [REQ-007](REQ-007-governance-hardening.md) | 门禁与治理工具加固 | `superseded` | P2 | N/A | #29 | 已并入 REQ-006 任务 T2（文件与报告留作证据） |
| [REQ-008](REQ-008-coverage-debt.md) | 覆盖率洼地补测 | `superseded` | P2 | TBD | TBD | 已并入 REQ-006 任务 T4（本文件即 T4 的规格） |
| [REQ-009](REQ-009-local-gui-console.md) | 本地图形化控制台（可扩展框架 + 按键执行 + 股票图表 + 报告与迭代记录浏览） | `accepted` | P1 | #42 | TBD | `tests/test_webui_framework.py` `tests/test_webui_archive.py` `tests/test_webui_server.py` `tests/test_webui_views.py` |

状态说明：`proposed` 已登记待受理 · `accepted` 已受理 · `in-progress` 实现中 · `implemented` 已合入待验收 · `verified` 已验收 · `deferred` 暂缓 · `rejected` 不做 · `superseded` 被取代。

## 子需求台账

> 子需求是**大特性**（父需求）里可独立交付、独立验收的切片，编号 `REQ-NNN.S`，
> 写在父需求文件的「## 子需求」小节里（`### REQ-NNN.S <标题>`），不单独成文件；
> 规则见 [`README.md`](README.md) §6.1。
> 本表与那些小节必须严格一致，且**父需求的状态不得比它最慢的子需求更靠前**
> （有子需求没验收完，父需求就不能算 `verified`），由
> [`tests/test_requirement_traceability.py`](../../tests/test_requirement_traceability.py) 校验。

| ID | 父需求 | 标题 | 状态 | 实现 PR | 关联测试 |
|----|--------|------|------|---------|----------|
| [REQ-006.1](REQ-006-requirement-test-dev-flow.md) | REQ-006 | 财报分析端到端实跑加固与实跑验收规则 | `implemented` | #36, #37, #46 | `tests/test_release_gates.py` `tests/test_update_docs_contract.py` |
| [REQ-006.2](REQ-006-requirement-test-dev-flow.md) | REQ-006 | 实跑暴露的数据包与证据层缺陷修复 | `in-progress` | TBD | `tests/test_tushare_pack_sections.py`（待建）`tests/test_pdf_preprocessor.py` `tests/test_results_pipeline.py` `tests/test_change_report.py` `tests/test_runs_ledger.py` |
| [REQ-009.1](REQ-009-local-gui-console.md) | REQ-009 | 按键执行器与任务生命周期 | `accepted` | TBD | `tests/test_webui_server.py` |
| [REQ-009.2](REQ-009-local-gui-console.md) | REQ-009 | 报告浏览、图表与迭代台账视图 | `accepted` | TBD | `tests/test_webui_views.py` |
| [REQ-009.3](REQ-009-local-gui-console.md) | REQ-009 | 可扩展框架与本地数据层（微内核 / 插件注册表 / 面板协议 / 数据缓存 / API 契约 / 安全中间件） | `verified` | #44, #45 | `tests/test_webui_framework.py` |
| [REQ-009.4](REQ-009-local-gui-console.md) | REQ-009 | 手动触发的远程采集与长期存档（批次 / 配额档案 / 原始存档 / 断点续跑 / 权限缺口清单） | `accepted` | TBD | `tests/test_webui_archive.py` |

> **编号按登记顺序，交付按「交付顺序」**：`REQ-009` 的交付顺序为
> **`REQ-009.3`（框架，先）→ `REQ-009.4`（采集与长期存档）→ `REQ-009.1`（按键执行器）→ `REQ-009.2`（视图）**，
> 理由见该需求条目的「## 子需求」小结与 [`docs/GUI_CONSOLE_PLAN.md`](../../GUI_CONSOLE_PLAN.md) §14。
> 其余父需求的子需求仍是「编号顺序 = 交付顺序」。


## 里程碑

| 里程碑 | 目标 | 包含需求 | 状态 |
|--------|------|----------|------|
| 定期报告增量更新 v1 | 从「单次全量分析」升级为「按最新期次增量更新 + 可追溯迭代台账」 | REQ-003 → REQ-004 → REQ-005 | REQ-001…005 全部 `verified`，里程碑达成 |

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
| 验收报告与它验收的代码改动可能被并进同一个提交，归属含糊（REQ-007 首轮报告即如此） | 独立验收（REQ-007） | 报告单独成提交（`docs(verification): …`），已在 REQ-007 收尾时纠正；若需强制可加 CI 检查 |
| 测试用例预算已**经 owner 批准上调**（40→48 文件、1600→1800 用例，2026-09-21） | `make scope` 实测（2026-09-21） | 上调留痕：`scripts/test_scope.py` 注释、REQ-006 的 AC-7 变更记录与任务 T7、`docs/TESTING.md` §5。**纪律不变**：下次接近新上限时仍先清理/合并冗余用例，不要习惯性上调；REQ-009 四个切片合计 ≤64 条用例 → 预计 1586/1800、37/48 |
| `--light` 结转模式 | `docs/PERIODIC_UPDATE_PLAN.md` §12.1 | 需先量化「哪些模块可安全结转」 |
| `runs/` 保留策略与 PDF 引用计数回收 | `docs/PERIODIC_UPDATE_PLAN.md` §12.2 | 依赖 REQ-003 落地后再评估 |
| 港股 / 美股定期报告 PDF 通路 | `docs/PERIODIC_UPDATE_PLAN.md` §12.3 | 数据源与披露规则未定 |
