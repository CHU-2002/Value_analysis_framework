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
| [REQ-006](REQ-006-requirement-test-dev-flow.md) | 工程化开发流程（建立与持续维护） | `verified` | P1 | N/A | #25, #29, #30, #31, #32, #34, #35, #36, #37, #60, #89, #91 | `tests/test_release_gates.py` `tests/test_test_scope.py` `tests/test_update_docs_contract.py` `tests/test_two_layout_e2e.py`（追溯门禁自身即扫描器，按设计排除，故不列入声明） |
| [REQ-007](REQ-007-governance-hardening.md) | 门禁与治理工具加固 | `superseded` | P2 | N/A | #29 | 已并入 REQ-006 任务 T2（文件与报告留作证据） |
| [REQ-008](REQ-008-coverage-debt.md) | 覆盖率洼地补测 | `superseded` | P2 | TBD | TBD | 已并入 REQ-006 任务 T4（本文件即 T4 的规格） |
| [REQ-009](REQ-009-local-gui-console.md) | 本地图形化控制台（可扩展框架 + 按键执行 + 股票图表 + 报告与迭代记录浏览） | `verified` | P1 | #42 | #44, #45, #63, #64, #66, #67, #70, #71, #72 | `tests/test_webui_framework.py` `tests/test_webui_archive.py` `tests/test_webui_server.py` `tests/test_webui_views.py` |
| [REQ-010](REQ-010-latest-valuation-publication.md) | 最新价值分析报告发布与历史版本保留 | `verified` | P1 | #61 | #78, #79, #80, #81 | `tests/test_latest_valuation_publication.py` |
| [REQ-011](REQ-011-unified-data-acquisition.md) | 一次性全量数据获取与统一原始数据仓 | `verified` | P1 | #73 | #83 | `tests/test_data_store.py` `tests/test_data_pull.py` `tests/test_offline_rebuild.py` |
| [REQ-012](REQ-012-console-v2.md) | 控制台 2.0（公司上下文 + 任务式交互 + 视图质量） | `verified` | P1 | #74 | #92, #95, #96, #97, #98, #99, #100, #101, #104, #106, #109 | `tests/test_console_context.py` `tests/test_console_actions.py` `tests/test_console_views.py` `tests/test_console_data_page.py` |
| [REQ-013](REQ-013-agent-cli-report.md) | 一键生成分析报告（程序化调用 agent CLI） | `accepted` | P1 | #75 | #77 | `tests/test_agent_action.py` |
| [REQ-014](REQ-014-watchlist-maintenance-ui.md) | 控制台里的自选股清单维护（添加 / 从既有产物导入 / 移除） | `verified` | P1 | #102 | #112, #113 | `tests/test_watchlist_ui.py` |

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
| [REQ-006.1](REQ-006-requirement-test-dev-flow.md) | REQ-006 | 财报分析端到端实跑加固与实跑验收规则 | `verified` | #36, #37, #46, #48 | `tests/test_release_gates.py` `tests/test_update_docs_contract.py` |
| [REQ-006.2](REQ-006-requirement-test-dev-flow.md) | REQ-006 | 实跑暴露的数据包与证据层缺陷修复 | `verified` | #49, #60 | `tests/test_tushare_pack_sections.py` `tests/test_pdf_preprocessor.py` `tests/test_results_pipeline.py` `tests/test_change_report.py` `tests/test_runs_ledger.py` |
| [REQ-009.1](REQ-009-local-gui-console.md) | REQ-009 | 按键执行器与任务生命周期 | `verified` | #66 | `tests/test_webui_server.py` |
| [REQ-009.2](REQ-009-local-gui-console.md) | REQ-009 | 报告浏览、图表与迭代台账视图 | `verified` | #66, #67 | `tests/test_webui_views.py` |
| [REQ-009.3](REQ-009-local-gui-console.md) | REQ-009 | 可扩展框架与本地数据层（微内核 / 插件注册表 / 面板协议 / 数据缓存 / API 契约 / 安全中间件） | `verified` | #44, #45 | `tests/test_webui_framework.py` |
| [REQ-009.4](REQ-009-local-gui-console.md) | REQ-009 | 手动触发的远程采集与长期存档（批次 / 配额档案 / 原始存档 / 断点续跑 / 权限缺口清单） | `verified` | #63, #64, #67 | `tests/test_webui_archive.py` |
| [REQ-011.1](REQ-011-unified-data-acquisition.md) | REQ-011 | 自选股清单与统一原始仓 | `verified` | #83 | `tests/test_data_store.py` |
| [REQ-011.2](REQ-011-unified-data-acquisition.md) | REQ-011 | 一次动作全量拉取与缺口补齐 | `verified` | #83 | `tests/test_data_pull.py` |
| [REQ-011.3](REQ-011-unified-data-acquisition.md) | REQ-011 | 离线重建派生产物 | `verified` | #83 | `tests/test_offline_rebuild.py` |
| [REQ-012.1](REQ-012-console-v2.md) | REQ-012 | 公司上下文与信息架构 | `verified` | #92, #104, #109 | `tests/test_console_context.py` |
| [REQ-012.2](REQ-012-console-v2.md) | REQ-012 | 任务式动作层 | `verified` | #92, #104, #109 | `tests/test_console_actions.py` |
| [REQ-012.3](REQ-012-console-v2.md) | REQ-012 | 视图质量与口径修正 | `verified` | #92, #104, #109 | `tests/test_console_views.py` |
| [REQ-012.4](REQ-012-console-v2.md) | REQ-012 | 数据页（依赖 REQ-011） | `verified` | #92, #104, #109 | `tests/test_console_data_page.py` |
| [REQ-013.1](REQ-013-agent-cli-report.md) | REQ-013 | 包装脚本与动作白名单 | `accepted` | #77 | `tests/test_agent_action.py` |
| [REQ-013.2](REQ-013-agent-cli-report.md) | REQ-013 | 最小界面（一键页） | `accepted` | #77 | `tests/test_agent_action.py` |
| [REQ-013.3](REQ-013-agent-cli-report.md) | REQ-013 | 提交前预检、完整命令行与产出链接 | `accepted` | TBD | `tests/test_agent_action.py` |

> **编号按登记顺序，交付按「交付顺序」**：`REQ-009` 的交付顺序为
> **`REQ-009.3`（框架，先）→ `REQ-009.4`（采集与长期存档）→ `REQ-009.1`（按键执行器）→ `REQ-009.2`（视图）**，
> 理由见该需求条目的「## 子需求」小结与 [`docs/GUI_CONSOLE_PLAN.md`](../GUI_CONSOLE_PLAN.md) §14。
> 其余父需求的子需求仍是「编号顺序 = 交付顺序」。
> **`REQ-011` / `REQ-012` 的交付顺序**：`REQ-011.1` → `REQ-011.2` → `REQ-011.3`（数据层先立）；
> `REQ-012.1`~`.3` 与 `REQ-011` 互不阻塞、可并行，`REQ-012.4`（数据页）必须等 `REQ-011.1`/`.2` 交付。
>
> **子需求的跟踪 Issue**（本表无 Issue 列，故在此登记；父需求的 Issue 见上表）：
> `REQ-006.2` → [#50](https://github.com/CHU-2002/Value_analysis_framework/issues/50)、
> `REQ-009.4` → [#51](https://github.com/CHU-2002/Value_analysis_framework/issues/51)、
> `REQ-009.1` → [#52](https://github.com/CHU-2002/Value_analysis_framework/issues/52)、
> `REQ-009.2` → [#53](https://github.com/CHU-2002/Value_analysis_framework/issues/53)。
> `REQ-011.1`~`.3` 与 `REQ-012.1`~`.4` **暂不各开跟踪 Issue**，随父 Issue
> [#73](https://github.com/CHU-2002/Value_analysis_framework/issues/73) /
> [#74](https://github.com/CHU-2002/Value_analysis_framework/issues/74) 跟踪；
> 若后续需要独立跟踪再补开并回填本行。
> 按 [`README.md`](README.md) §9，Issue 在**逐条验收通过后**关闭，不在 PR 合并时自动关闭。


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
| **用例清理欠账**：2026-09-28 第二次上调（48→52 文件、1800→2000 用例）时**没有先做清理**，owner 在被告知两条路后选择直接上调 | REQ-011 / REQ-012 受理时的预算实测（38/48、1653/1800，余量 147 条） | 上调留痕：`scripts/test_scope.py` 注释、REQ-006 的 AC-7 变更记录与任务 T9、`docs/TESTING.md` §5。**这笔欠账要在下一次接近上限之前还掉**（合并重复用例、删掉只复述实现的测试），不得用「已经上调过两次」当作第三次上调的理由 |
| 归档/测试文件要不要按需求编号分子目录（如 `tests/req011/`） | REQ-011 设计评审（2026-09-28） | 现在 38 支文件平铺在 `tests/`；到 52 支时检索成本上升。缺可判定判据，先记着 |
| `--light` 结转模式 | `docs/PERIODIC_UPDATE_PLAN.md` §12.1 | 需先量化「哪些模块可安全结转」 |
| `runs/` 保留策略与 PDF 引用计数回收 | `docs/PERIODIC_UPDATE_PLAN.md` §12.2 | 依赖 REQ-003 落地后再评估 |
| 港股 / 美股定期报告 PDF 通路 | `docs/PERIODIC_UPDATE_PLAN.md` §12.3 | 数据源与披露规则未定 |
| **离线重建的 §14 少了「无权限」的原因文字**：联网路径写「数据缺失（Tushare yc_cb 接口未授权；当前账号权限不足）」，重建只写「数据缺失」（原因仍在产物末尾的缺口行与 `no_permission` 计数里） | `REQ-011` 的 `AC-9` 真实 token 实跑（2026-09-29） | **已实现（2026-09-30）**：[#87](https://github.com/CHU-2002/Value_analysis_framework/pull/87) 离线未命中时从仓里读回最近一次失败原因，用与联网路径同一种方式渲染；产物末尾缺口清单也带原因标签。见 `REQ-011` 的「## 维护记录」 |
| **「零 socket 调用」在冷进程里不成立**：`rebuild.offline_client()` 的导入链会触发 CPython `multiprocessing.connection._has_ipv6()` 建一个 `AF_INET6` 探测 socket（**不出站**）；现有用例 `test_rebuild_offline_does_not_open_a_socket` 只因为跑它时该模块已被导入过才绿 | `REQ-011` 的 `AC-9` 真实 token 实跑（2026-09-29） | **已实现（2026-09-30）**：[#87](https://github.com/CHU-2002/Value_analysis_framework/pull/87) 用例改为打桩 `DataAccess._invoke`（唯一远程调用点）抛错 + socket 兜底，不再依赖导入顺序。见 `REQ-011` 的「## 维护记录」 |
| **`DataUnavailable` 的文案与实际尝试次数不符**：权限类错误立即放弃时仍写 `failed after 5 retries`，实际只尝试 1 次 | `REQ-011` 的 `AC-9` 真实 token 实跑（2026-09-29，`yc_cb` 的 `error_excerpt`） | 既有（`REQ-009.4` 搬过来的语义），只影响 `error_excerpt` 可读性；建议改成实际尝试次数 |
| **`docs/requirements/README.md` §11 的「需求条目一览」表没有门禁覆盖**：它列的状态与 `ledger.md` 必须一致，但 `tests/test_update_docs_contract.py` 只看仓库根的 `README.md`，推进状态时容易漏更新（`REQ-011` 收口时就漏了一次，靠人工发现） | `REQ-011` 收口（2026-09-29） | **已实现（2026-09-29）**：`tests/test_requirement_traceability.py::test_readme_index_status_matches_the_ledger` 断言 §11 与台账状态一致、且 §11 的编号都在台账里 |
| **`REQ-009` 里 `.3` 子需求的验收轮次写作「三轮」，与它自己的报告不符**：`.3` 报告有「第四轮（基线 `1ef5cda`）逐条复核」小节，`:145-146` 也提到第四轮；而需求条 `:155-156` 与 `:530` 的 `.3` 分句仍写「三轮」 | 2026-09-30 校正**父需求**轮次（三轮→五轮，见 `REQ-009:530` / `:623`）时顺带发现 | **未改**（属历史描述文本；要改需逐处核对 `.3` 报告的全部轮次）。父需求的计数已校正；这里只登记，留待下一次碰 `REQ-009` 时一并处理 |
