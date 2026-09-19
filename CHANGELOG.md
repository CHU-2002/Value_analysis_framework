# Changelog

本文件记录本仓库（Value Analysis Framework）的变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> 上游龟龟框架 v1 → v3 的架构演进见 [CHANGELOG_V2.md](CHANGELOG_V2.md)。

- 需求管理：`docs/requirements/`（编号规则、状态机、优先级、`TEMPLATE.md`、需求台账 `ledger.md`），
  首批登记 REQ-001…REQ-005，并接入 GitHub Issue / 标签 / 里程碑
- 测试管理：`docs/TESTING.md` 测试策略、`pytest.ini`（严格 marker 与分层）、
  按文件自动分层的 marker（`unit` / `contract` / `e2e` / `integration`）、覆盖率门禁 ≥ 74%（基线 76.44%）
- 需求↔测试追溯门禁 `tests/test_requirement_traceability.py`：台账与需求条目一致性、
  无悬空 `REQ-NNN` 引用、已交付需求必须有测试引用
- 开发流程：`docs/DEVELOPMENT.md`（就绪定义 DoR、完成定义 DoD、拆分与验收流程）、
  `Makefile` 本地校验入口（`make help` / `verify` / `cov` / `unit` / `trace`）
## [Unreleased]

### Added

- 下游接线修复（REQ-005）：`/valuation`、`/portfolio-strategy` 与两个 coordinator 改为先经
  `scripts/runs.py resolve --company-dir ... --latest` 解析 `run_dir`，再把 run 目录交给
  `resolve_qualitative`——此前直接把公司目录传进去，在 run-store 布局下会静默退回
  `source=unavailable`。契约测试由只钉 `/value-analysis` 扩展到 7 份下游文档，
  并新增双布局端到端测试 `tests/test_two_layout_e2e.py`
- 分支模型与三道门：一个特性一条特性分支，子 PR 合入特性分支（CI 跑全量 +
  `pr-body` 校验研发自测栏），特性分支**直接合入 `main`** 且必须带独立验收报告
  （`scripts/acceptance_gate.py`）；`main` 每累积 3 个特性必须补一份批量全量回归记录
  （`scripts/regression_gate.py`），不维护长期集成分支
- 测试 scope 登记与预算：`docs/TEST_SCOPE.md`（33 支文件 / 1461 用例）与 `scripts/test_scope.py`，
  CI 的 `test-scope` 作业校验登记表与上限；控制 CI 成本走整体 scope 维护，不裁剪单个 PR 的范围
- 独立验收报告模板 `docs/verification/TEMPLATE.md`、批量回归记录模板 `docs/regression/TEMPLATE.md`、
  PR 模板新增「需求编号 / 研发自测（手工）/ 验收报告」栏位
- `make` 新增 `scope` / `scope-write` / `scope-check` / `gates` 目标；`verify` 覆盖全部本地门禁
- 可执行买卖计划：基于固定估值基准生成四档分批买入限价、资金比例与极端高估卖出价，并支持行情确认、成交状态和硬退出事件
- 触发式买卖计划命令 `/buy-sell-plan`：主流程只产出报告与冻结估值，用户阅读报告后再决定是否采集当时行情生成计划，不自动生成 `buy_sell_market.json`/`buy_sell_plan.*`
- 成熟的工程化文档：README、CONTRIBUTING、CODE_OF_CONDUCT、SECURITY、PR 与 Issue 模板
- GitHub Actions CI：在 push 与 PR 上运行 pytest
- CI 增强：`lint` 与 `pr-title` 检查、`ci-success` 汇总状态，用于分支保护
- CODEOWNERS 与可配置管理员名单 `.github/admins.yml`，管理员可直接合并 PR
- 一键应用 main 分支保护的工作流 `setup-branch-protection`（必需 CI + 必需 review + 管理员放行）
- 架构文档 `docs/ARCHITECTURE.md`
- 定期报告增量更新分析的设计与实施计划 `docs/PERIODIC_UPDATE_PLAN.md`（季度/半年/年报增量更新、经营变化报告、run-store 迭代台账）
- 定期报告（一季报/半年报/三季报/年报）发现与下载：CNINFO 四类公告分类接入、期次标识 `scripts/periods.py`、最新期次探测 `--latest`/`--report-type auto`、期次补齐 `--since` 与 `sources_index.json`
- 可比期数据与期次章节：数据包财务报表新增「上年同期」可比列（用于同比与单季拆分）、`pdf_preprocessor.py --period` 按期次产出 `pdf_sections_{period}.json`、`prepare --primary-period` 选定主期次证据
- 运行台账与框架指纹：`scripts/version.py`（`FRAMEWORK_VERSION`、提示词/代码指纹、schema 版本）、`scripts/runs.py`（`new`/`resolve`/`finish`/`adopt`/`export`/`downstream`、run 私有输入快照、`history.jsonl`/`latest.json`/`record.json`）、`scripts/analysis_status.py`（更新判定与全仓重跑清单）、manifest 加性 `framework` 块
- 定期报告增量更新：`qualitative.period_delta`（D7）模块与 `shared/qualitative/agents/modules/period_delta.md`、`prior_analysis` 证据源、`scripts/results/change_report.py` 与变化报告提示词、协调器 `shared/qualitative/coordinator_update.md`、命令 `/update-analysis`
- 结构化定性结果管线：`scripts/results/`（schema、manifest、evidence、context、prepare、reconcile、synthesis、resolver）
- 价值分析模块 `strategies/value/` 与预计算引擎 `scripts/value_analysis_engine.py`
- 组合策略模块 `strategies/portfolio/` 与引擎 `scripts/portfolio_engine.py`
- 年报链接发现 `scripts/discover_report.py`

### Fixed

- 定期报告发现：只保留 `secCode` 与目标公司一致的公告（全文检索会返回同行报告，原先会把别家公司年报登记为本公司期次）
- 定期报告发现：按 `totalpages` 翻页并过滤非 PDF 附件，避免第一页截断静默漏期
- `--latest` 不带 `--report-type` 时改为「任意类型的最新期次」，不再静默退化为最新年报；`--report-type` 可省略，默认年报
- `--since` 早于默认回看窗口时自动拓宽查询窗口，避免「补齐」却静默漏期
- 周期模式拒绝 `--url` 与 `--latest`/`--since`/`auto` 同时使用，避免参数被静默忽略
- `sources_index.json`：已有期次自动跳过（`--force` 重下）；下载失败不再留下指向已删除文件的陈旧条目；兼容畸形索引并自动创建目录
- 单个非法上游链接记入 `periods_failed` 而非中断整批；非 JSON 响应按网络失败处理而非参数错误；CNINFO 公告日期按北京时间解释
- 定期报告年份路径与期次路径共用同一套标题关键词（`三季度报告`/`半年报`/`中期报告`），消除两条路径结论不一致
- 年份路径不再把「半年度报告/半年报」当作年报：命中判定加入与期次路径相同的解析校验
- 分页上限告警不再依赖 `totalpages` 类型，`totalRecordNum`/满页推断下同样会告警
- 周期模式跳过逻辑：下载失败标记为 `failed` 的期次下次自动重试，不会自述 SUCCESS；索引按 `stock_code` 归属校验；期次文件需非空且与记录大小一致才算已持有
- 周期模式参数冲突校验补齐：`--year`、`--recent-years`、`--latest`+`--since`、非周期模式的 `--force`、未来期次的 `--since` 均明确报错
- CNINFO 分页实测校准：服务端把 `pageSize` 截断为 30（`totalpages` 还向下取整），改为按 30 请求并优先用 `totalRecordNum`/`hasMore` 判定翻页，空页但仍有待扫记录时告警
- CNINFO 关键词回退改为「直到出现可评分候选」：修复中报因英文版公告先返回而放弃后续关键词、导致按年份下载失败的问题（实盘验证 `discover_report("000858","2024","中报")` 已可命中）
- 年份路径按「实际请求的年份」校验解析结果，避免把别年份年报存成目标年份文件
- 跨股票共用 `sources_index.json` 时不再合并期次；失败标注的相对路径按索引所在目录解析

- 台账指纹范围补齐 `prompts/**`、`.claude/skills/**` 与 `docs/BUY_SELL_CONTRACT.md`，避免未提交的提示词/合同改动不被判定为框架变更
- `runs.py adopt` 现在只对被改写的产物重盖哈希，并在源目录产物与 manifest 不符时拒绝接管；新增 `runs.py downstream --fresh` 用于清除 `downstream.stale`
- 修复 `test_discover_report` 未 mock `requests.post` 导致 CNINFO 真实请求逃逸、CI 因 403 失败的问题

- 修正 HK 式年报章节识别：当无 A 股 `第X节` 分区标记时，优先独立成行的真实标题，避免 MDA 误取「董事会报告书」、GOV 误取业务回顾中的泛词「公司治理」；补充董事长报告书、企业管治报告、受限资产等关键词，P2 恢复可识别
- 定性模块上下文按来源和章节分配预算，保证业务、治理附注及财务证据覆盖可见，避免长财务表挤占年报内容
- 汇总按完整 JSON 预算选取条目，保留参数、主判断及关键风险，记录省略项；传递模块精准引文及同 ID 的不同摘录
- 模块与最终 sidecar 校验增加 `--evidence-index`，核验 run、subject、来源、定位和连续原文；同步语义复核与附注交叉检查要求
- 在 `requirements.txt` 中补充缺失的运行时依赖 `markdown` 与 `numpy`
- 行情刷新改写为独立副本，避免破坏定性分析的原始证据快照
- 分支保护工作流区分用户仓库与组织仓库，避免用户仓库因 push restrictions 报错

### Changed

- 买卖计划改为触发式：`value_analysis_engine.py` 不再自动导出 `buy_sell_market.json`，报告默认不含买卖章节，未触发时明确标注而非写占位计划

- 消费者统一通过 `resolve_qualitative` 获取定性输入
- `main` 分支启用保护，所有改动必须经 Pull Request 合入
- README 改为更直白的说明，并把来源与致谢统一放在文末
- CI 升级到 `actions/checkout@v5` 与 `actions/setup-python@v6`，适配 Node.js 24
- 通用估值（`/valuation`）降为价值分析的子模块，物理路径调整为 `strategies/value/valuation/`，仍可单独调用
- 共享数据层与选股器去除 "Turtle/龟龟" 命名
- 组合策略（`/portfolio-strategy`）的候选池改为以价值分析为主、选股器补充，不再依赖龟龟策略

### Removed

- 移除龟龟策略模块：`strategies/turtle/`、`/turtle-analysis` 命令与 skill；其能力由价值分析覆盖，上游项目仍保留在致谢中

## [0.1.0] - 2026-09-13

### Added

- 基于上游龟龟投资框架建立独立的 Value Analysis Framework 仓库
- 保留 MIT 许可证与上游版权声明，并在 README 中注明来源

[Unreleased]: https://github.com/CHU-2002/Value_analysis_framework/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/CHU-2002/Value_analysis_framework/releases/tag/v0.1.0
