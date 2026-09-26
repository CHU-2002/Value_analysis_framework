---
id: REQ-006
title: 工程化开发流程（建立与持续维护）
status: verified
priority: P1
owner: CHU-2002
created: 2026-09-20
updated: 2026-09-20
issue: N/A（以 PR 跟踪）
design: docs/DEVELOPMENT.md
milestone: 工程化流程
pr: "#25"
depends-on: REQ-001, REQ-002, REQ-003, REQ-004, REQ-005
supersedes: TBD
---

# REQ-006 工程化开发流程（建立与持续维护）

## 背景与问题

需求台账（REQ-001…REQ-005）建立后，需求、测试、开发三者有了编号上的联系，
但**合入路径仍是一条直线**：任何 PR 都直接进 `main`，CI 只在合并前跑一次测试。
结果是：新功能没有人手工验过就进了正式分支；一次合并只证明「这次没踩坏」，
无法回答「积攒了这么多改动之后整体还好不好」。

## 目标

- 把合入路径分成**开发分支**与**正式分支**两段，每段有各自的验收要求。
- 让新功能的**手工自测**成为必填留痕，而不是靠自觉。
- 让「特性分支 → `main`」必须经过**独立验收**（独立 agent 跑全量测试 + 逐条核对验收标准）。
- 让 `main` 在**累积若干需求后**必须补一次全量回归，而不是永远不跑。
- 控制 CI 总量的方向是**维护整体测试 scope**，不是裁剪单个 PR 的测试范围。
- **本需求是常设的**：这套流程自身的持续维护（门禁加固、测试覆盖补强、模板与文档校正）
  都算它的工作，以「## 任务清单」记录，**不新开需求编号**。

## 任务清单

本需求下的工作包。子项是**任务**，不是需求：编号只给新能力（见
[`README.md`](README.md) §6 粒度规则）。

两条硬约定（与 AC-7 对齐）：

1. **完成 = 有独立验收链接**：状态为「完成」的任务，证据列必须指向一份独立验收报告；
   没有独立验收的只能算「进行中」。
2. **证据要能直接读到交付内容**：证据列指向的路径必须包含该项的具体交付物；
   交付 PR 号在合入后回填。

| # | 工作包 | 状态 | 证据 |
|---|--------|------|------|
| T1 | 建立需求台账、测试策略与三道门（分支模型、CI 作业、PR/验收/回归模板） | 完成 | PR #25；验收 [`2026-09-20-REQ-006.md`](../verification/2026-09-20-REQ-006.md) |
| T2 | 门禁加固 8 项：需求编号小节必填、scope 归属编号校验、失败路径单测、回归记录内容校验、扁平路径守卫扫全仓、增量 run 跑通 `prepare --prior-analysis`、prose 去脆化、夹具去 `develop` | 完成 | PR #29；验收 [`2026-09-20-REQ-007.md`](../verification/2026-09-20-REQ-007.md)（原 REQ-007，已并入本需求） |
| T3 | 验收报告容忍举例（扫描前剔除引用块/代码块/行内代码）；`test_scope --check` 比对归属列**内容** | 完成 | PR #32（已合入 `0d669c0`）；独立验收见 [`2026-09-20-REQ-006.md`](../verification/2026-09-20-REQ-006.md) 的「本次维护验证（T3）」一节 |
| T5 | CI 提速与简化：8 个 job 收敛为 `ci` + `ci-success`；测试只装 `requirements-test.txt`；`pytest -n auto` 并行；默认只跑 3.12，3.10 改手动核验 | 完成 | PR #34；独立验收见 [`2026-09-20-REQ-006.md`](../verification/2026-09-20-REQ-006.md) 的「本次维护验证（T5）」一节 |
| T6 | 大特性拆子需求：`REQ-NNN.S` 编号与 `AC-S.n`、「父需求状态不得先于子需求」不变量、台账「子需求台账」表，并把子需求编号接进验收/追溯/scope 三个门禁；评审改为按子需求收口（AC-3 变更） | 完成 | PR #35；独立验收见 [`2026-09-20-REQ-006.md`](../verification/2026-09-20-REQ-006.md) 的「本次维护验证（T6）」一节。**诚实说明**：该报告在 `d3d769e` 上给出「有条件通过」，提出 3 项条件；实现者随后在 `fix(governance)` 里逐条修复（新增 4 个单测），但**按新的评审粒度没有为这次修复再拉一轮独立复核**——复核安排在下一个子需求/大特性收口时进行 |
| T4 | 覆盖深度补强：`valuation_engine` 34%→60%、`portfolio_engine` 47%→70%、三个 0% 脚本逐个判定、总覆盖率→80% 并把门禁提到 ≥78% | 不做（2026-09-20 使用者决定：工程化流程到此为止，不补测） | 任务说明见 [`REQ-008`](REQ-008-coverage-debt.md)（该编号已并入本需求，文件保留作规格；覆盖率维持 76.8%、门禁维持 ≥74%） |
| T7 | 测试 scope 预算上调（40→48 文件、1600→1800 用例），为图形化控制台（REQ-009）及其后续 GUI 需求留出余量；同步 `scripts/test_scope.py`、`docs/TESTING.md` §5、`docs/DEVELOPMENT.md` §14 与本需求 AC-7 的例外通道 | 完成 | **PR #48**（收口 PR 回填）。本项是**门禁阈值调整**，按 AC-3 的评审粒度在 REQ-006.1 收口时由独立评审者复核：见 [`docs/verification/2026-09-25-REQ-006.1.md`](../verification/2026-09-25-REQ-006.1.md) 的 **T7 复核**小节（48/1800 预算、owner 批准留痕、AC-7 例外通道均成立），不为它单独拉一轮评审 |
| T8 | **REQ-006.1 AC-1.5 的收口前置**：`synthesis` 默认预算按真实载荷设定（40,000 → 120,000）与丢弃记账修正（列表条目逐条计数、标签可读）；补真实规模回归测试 | 完成 | **PR #46**（已合入 `b23270b`）。依据两轮实跑实测（完整载荷 96,496 / 97,359 字符；旧默认下丢 148 项，含每个模块的 `quality.missing_inputs`；同预算下丢弃计数 148 → 220）。代码 `scripts/results/synthesis.py`（`DEFAULT_MAX_CHARS`、`_list_item_label`），测试 `tests/test_results_pipeline.py` 的 `test_default_budget_*` 与 `test_dropped_accounting_counts_every_list_item` |

## 子需求

### REQ-006.1 财报分析端到端实跑加固与实跑验收规则

- 状态：`verified`
- 状态说明：`AC-1.1`~`AC-1.10` 全部落地；`AC-1.9` 于 2026-09-25 完成**两轮**真实复跑
  （第二轮在 `main b23270b` 上**全程默认参数、零覆盖**），命令与观察见
  [`docs/run-records/2026-09-25-REQ-006.1-AC-1.9-实跑记录.md`](../../run-records/2026-09-25-REQ-006.1-AC-1.9-实跑记录.md)，
  发现清单见[同目录的发现清单](../../run-records/2026-09-25-REQ-006.1-AC-1.9-实跑发现清单.md)。
  该次实跑暴露的 `AC-1.5` 缺口已由任务 T8（PR #46）修复；其余发现登记为 `REQ-006.2`。
  **独立验收通过（2026-09-25）**：由未参与实现的独立评审者在 `main 56a7d48` 上逐条核对
  `AC-1.1`~`AC-1.10`（全量门禁 `1564 passed, 3 skipped`、覆盖率 78.31%），并独立复核两轮实跑产物，
  报告见 [`docs/verification/2026-09-25-REQ-006.1.md`](../verification/2026-09-25-REQ-006.1.md)（含「## 实跑记录」）；
  未通过项 0，报告另列 7 条不影响判定的观察与记录质量问题（如实跑记录 §8 的 owner 签署框未勾选），
  在收口 PR 里一并处理。
- 目标：把「用真实数据完整跑一次财报分析迭代」暴露的问题一次性收干净，并让这类**只有实跑才能发现**的问题在流程里有固定去处（登记 → 修复 → 复跑），不再靠对话里的临时结论或运行时补丁。
- 背景：2026-09-20 用真实数据（Tushare + CNINFO + 本地 PDF）完整跑了一次迭代，**未改任何代码**、只用运行时补丁绕过，暴露 6 个问题 + 1 个流程观察。全部是 mock 测试发现不了的：

  | # | 现象（实测） | 证据 |
  |---|--------------|------|
  | 1 | CNINFO 只认 `http`：https 查询 → 403，http → 200；脚本硬编码 https | `scripts/discover_report.py:33-34,59` |
  | 2 | `ts.set_token()` 要写 `~/tk.csv`，HOME 只读的沙箱里 `PermissionError` | `scripts/tushare_collector.py:74`、`scripts/screener_core.py:176` |
  | 3 | 文档 Step 4 漏传 `--run-id`，`prepare` 自己另生成一个 → 台账 id 与 run 内部 id 分叉 | `scripts/results/prepare.py:112,170,267` |
  | 4 | `resolve_qualitative` 不认 `period_delta`，但文档 Step 6 要求把它喂给 reconcile/synthesis：一加就 `result_digest does not match`、exit 3 | `scripts/results/resolve_qualitative.py:33-34,273` |
  | 5 | 默认预算装不下真实载荷：synthesis 30,000（4 核心实测 30,124，含 D7 38,070）；change_report 24,000 而 D7 单卡 26,141；中间区间**静默丢卡** | `scripts/results/synthesis.py:131`、`scripts/results/change_report.py:276` |
  | 6 | `runs.py finish --artifact name=path` 不校验存在性、不区分绝对/相对路径：相对路径被静默记成双写路径 | `scripts/runs.py:379-395` |
  | 7 | 流程观察：模块 agent 大量使用**超出自己有界 bundle** 的证据（environment 16 条里 9 条、business_moat 26 条里 21 条来自直接读 `evidence/index.json`），quote 可验证但绕过了契约与预算设计 | — |

- 验收标准：
  - **AC-1.1**：CNINFO 查询协议不再硬编码：可配置，且 https 返回 403 时回退 http 并保持请求头正确；mock 单测覆盖「https 403 → 回退 http 200」与「显式指定协议」两条路径。
  - **AC-1.2**：Tushare token 以不写 HOME 的方式注入，在 HOME 只读的沙箱里采集不再 `PermissionError`。
  - **AC-1.3**：文档 Step 4 的命令与 `prepare` 的参数表一致（带 `--run-id`）；run 目录 id 与传入/生成的 `run_id` 不一致时 `prepare` **报错**，不再静默分叉；有契约测试钉住。
  - **AC-1.4**：`period_delta` 被 `resolve_qualitative` 接受为**可选模块**并参与 digest：提供时不再报 `result_digest does not match`；不提供时 digest 与当前基线**逐字节一致**。
  - **AC-1.5**：`synthesis` 与 `change_report` 的默认预算按本次实跑的真实载荷设定，使其「4 核心 + D7 全卡片」不丢卡；任何卡片被丢弃时 payload 与 CLI 输出都必须列出被丢模块与原因，不得静默。
  - **AC-1.6**：`runs.py finish --artifact name=path` 对不存在的文件报错且不写台账；相对路径按调用者 cwd 解析并在台账里存绝对路径；重复 name 报错；三条路径都有单测。
  - **AC-1.7**：模块结果引用的每条 evidence id 都属于该模块 bundle 的 id 集合，越界可由检查手段检出（校验器或 bundle 生成侧裁剪）。
  - **AC-1.8**：**流程闭环**：验收标准里写了「实跑」的编号，其收口报告必须带「## 实跑记录」（含可复制命令、环境与观察），由 `scripts/acceptance_gate.py` 强制；实跑发现的问题当次登记（子需求或任务），不允许只用运行时补丁绕过。
  - **AC-1.10**：`TushareScreener._safe_call` 的重试必须作用在**重建后**的客户端上（原实现把 `pro` 取在循环外，三次尝试都打在同一个坏客户端上，重试等于没做）；有回归测试证明「第一次失败、第二次成功」。
  - **AC-1.9**：在只读 HOME + 真实 token 下**复跑**一次迭代，不使用任何运行时补丁，产出完整 run（台账、manifest、结构化结果、两篇报告），命令与观察写进实跑记录。
- 追溯：`tests/test_discover_report.py`、`tests/test_tushare_client.py`、`tests/test_screener.py`、`tests/test_runs_ledger.py`、`tests/test_results_pipeline.py`、`tests/test_change_report.py`、`tests/test_update_docs_contract.py`、`tests/test_release_gates.py`；PR #36（登记与规则）、PR #37（6 个实跑问题的修复，`4450863`）、PR #46（任务 T8）、PR #48（独立验收收口，`verified`）；实跑记录 [`docs/run-records/2026-09-25-REQ-006.1-AC-1.9-实跑记录.md`](../../run-records/2026-09-25-REQ-006.1-AC-1.9-实跑记录.md)；独立验收报告 [`docs/verification/2026-09-25-REQ-006.1.md`](../verification/2026-09-25-REQ-006.1.md)

### REQ-006.2 实跑暴露的数据包与证据层缺陷修复

- 状态：`verified`
- 状态说明：2026-09-25 由使用者受理，发现全部来自 REQ-006.1 的 AC-1.9 实跑（不另立 Issue，随父需求 REQ-006）。
  本子需求已由独立 agent 按 `AC-2.1`~`AC-2.8` 完成收口验收；实际完成度以真实 run、验收报告与 PR 为准。
- 历史进度（2026-09-25，尚未收口；最终收口见下方 2026-09-27 记录）：
  - **`F29` 已修（2026-09-25，本切片）**：附注源的**期次**从「完全没有标记」变成
    「登记 + 判定 + 对模块可见」三层落地——
    ① 附注包头部新增机器可读的 `> 报告期：YYYYH1` 契约（`prompts/phase2_PDF解析.md`
    输出模板，中报写 `2026H1`、年报写 `2025FY`，并保留 `> 资料截止日：`）；
    ② `prepare.py` 读该声明登记为 `pdf_footnotes` 源的 `period`（`period_basis=declared`），
    缺失时按「文件名 + 资料截止日」推断并标注 `basis=filename+cutoff`（真实 run
    `20260925T091012981574Z` 的年报包即由此判出 `2025FY`）；与 `--primary_period` 不一致时
    给出 `period mismatch` warning 并写入 `run_manifest.period_mismatches` 与
    `evidence/index.json`，声明存在但不可解析时走 `period unverified`（`basis=invalid`）；
    旧格式包（连 `资料截止日` 都没有）不追溯判红；
    ③ 中报附注包优先于年报包（`FOOTNOTE_SOURCE_NAMES` 顺序），未选中的候选仍登记为
    `pdf_footnotes:<filename>` 监测项（事后补上附注包会让 `validate_manifest_inputs`
    判定输入已变），且不进 `unavailable_inputs`；模块 bundle 新增 `source_periods`
    与 `selection.period_mismatches`，让读到附注证据的模块看见「这份证据属于哪一期」。
    **复现对照**：真实 run 的 `run_manifest.json` 原本 `warnings` 为空、附注源无 `period`；
    修复后在 run 目录副本上重跑得到 `primary_period=2026H1` / 附注源 `2025FY` 的
    period mismatch 告警（原 run 未改动，见 PR）。
  - **`F28` 已修（2026-09-25，本切片）**：`code_fingerprint` 由「HEAD sha（可带 `-dirty` 后缀）」
    改成**内容摘要**——对 `DIRTY_TRACKED_PATHS`（`scripts` / `strategies` / `shared` / `prompts` /
    `.claude/commands` / `.claude/skills` / `.opencode/commands` / `docs/BUY_SELL_CONTRACT.md`）
    下的文件内容做 sha256（`sha256:` 前缀），并排除 `__pycache__` / `*.pyc` / `.DS_Store`
    这类随运行出现或消失的产物（否则同一份代码的指纹会被字节码搅乱）。
    `git_commit` 与 `dirty` 仍是 `framework_block` 的独立溯源字段，`dirty` 继续用同一套
    pathspec 判定（与内容摘要各司其职，不再互相冒充）。
    **真实提交复现**：新建仓库 → 提交框架（`fp=sha256:e406eaf8…`）→ 只改 `docs/**` 再提交
    （HEAD 变了、`fp` **不变** → `analysis_status` 不再报 `framework_changed`）→ 改
    `scripts/**` 再提交（`fp` 变、`prompt_fingerprint` 不变）。测试：
    `tests/test_version.py` 的 `test_docs_only_commit_does_not_invalidate_a_recorded_run`、
    `test_code_fingerprint_is_a_content_digest_not_the_head_sha`、
    `test_code_fingerprint_ignores_bytecode_and_cache_artifacts`；同时把 3 条旧断言
    （`== "abc1234"` / `.endswith("-dirty")`）改写成内容口径——**不是放宽**：仍然逐条钉住
    「改什么会变、改什么不会变」。
  - **`F30` 已修（2026-09-25，本切片）**：`business_trend` 的基准写进两处规格并加契约测试——
    `shared/qualitative/agents/modules/period_delta.md` 明确「比较**同一财报口径下的上年同期**
    （`report_period` vs `comparable_period`），**不是**与上一次 run 的相对变化（那是
    `conclusion_change`）、也**不是**与上一轮结论比好坏；上年同期列不可得时写 `不确定` 并记
    `quality.missing_inputs`」，`shared/qualitative/references/output_schema.md` 的
    `business_trend` 行同步；测试见 `tests/test_period_delta_module.py::
    test_business_trend_basis_is_the_year_ago_period`。
  - **`F25` 已修（2026-09-25，本切片）**：同一 `evidence_id` 的多段具名子段可以分别引用——
    新增 `scripts/results/evidence_ref.py`（`split_evidence_reference` /
    `evidence_reference_base` / `format_evidence_reference`，单独成模块以免 `evidence.py`
    与 `schema.py` 循环 import），`validate_result` 的唯一性判在**引用**上而不是块 id 上
    （`base#1` / `base#2` 可以各占一条），`validate_result_evidence` 校验子段序号
    （越界报 `sub-excerpt #n ... out of range`）且**裸块引用命中该块的任意一条摘录**，
    模块级 bundle 边界检查按 base 归属；`synthesis` 的 instructions 新增 `sub_excerpts`
    规则、`final_synthesis.md` 与 `output_schema.md` 写明「同一块的不同数值分别引用，
    不得拼接」，`synthesis.py` 的窗口选择指导与之一致。
    测试：`tests/test_results_pipeline.py` +7（解析规则、两条子段各自可引用、越界被拒、
    未知块被拒、裸引用命中任意摘录、合成上下文声明规则、端到端「同一块两条摘录分别进
    sidecar 且引文必须落在对应子段」）。
    **副产物（本切片实测暴露的真实缺陷）**：`synthesis` 的预算收尾循环原先把被让出的条目
    统一记成笼统标签 `budget:disclosure`，多条被让出时会被 `set()` 去重成 1，于是
    `dropped_count` 可能**小于**各卡片 `omitted` 之和；把 `sub_excerpts` 说明文字加进
    instructions 后推过阈值即复现。现改为逐条带自己的标签，并加回归测试
    `test_budget_eviction_labels_every_dropped_item`（扫描 12k/11k/10k/9k 四档预算，
    断言「丢弃计数 ≥ 省略之和」且标签不含笼统项）。
  - `AC-2.2` 已实现：`scripts/tushare_modules/other_data.py:get_pledge_stat` 去掉三个股数字段的
    `divider=1e4`（接口本就是万股），并新增「§1 总市值 ÷ 现价 反推股数 ≈ §16 总股本（相对误差 <1%）」
    与「质押比例 == 质押股数 / 总股本」两条一致性校验；`pledge_stat.json` 夹具换成 2026-09-18 真实响应。
  - `AC-2.1` 已实现：`§12 营收同比增长率` 改请求真实字段 `or_yoy`（`tr_yoy` 兜底），
    此前请求的 `revenue_yoy` 不是 `fina_indicator` 字段、被静默丢弃导致整行永远为「—」；
    `§9 主营业务构成` 合并同值重复行（实测「冷饮产品系列」≡「冷饮产品」、「其他主营业务」≡「其他」）、
    成本缺失时毛利率写「—」而不是字面 `nan`，并在表后给出「各分部合计 + 合计特别调整 = 产品」
    的口径说明（`other_data.py:_segment_reconciliation_note`）。
  - `AC-2.3` 已实现：`§13.1` 的异常检测窗口改为「最新财报期 vs 上年同期」+「最新年报 vs 上一年报」
    （`assembly.py:_yoy_period_pairs`，并把 `assets_impair_loss` 纳入被检字段），不再拿 2007 年的
    历史逐对比较；占位段落的填充策略按 owner 决定落成**「只由全量分析填、增量 run 不填」**
    （需求内 AC-2.3 的策略引用块 + [`docs/PERIODIC_UPDATE_PLAN.md` §7.1.1](../../PERIODIC_UPDATE_PLAN.md)），
    并由 `evidence.py`（占位符段落进 `unfilled_sections`、不进 `entries`；混合段落只删占位符行）与
    `context.py`（`evidence_coverage: unavailable` + `unavailable_inputs`，原始上下文也不塞占位符）落地。
  - `AC-2.6` 已实现：附注源两个文件名都认（`data_pack_report.md` / 中报
    `data_pack_report_interim.md`）；都不在 `inputs/` 快照里时 `prepare` 给出**显式 warning**
    并在 `run_manifest.unavailable_inputs` 里登记 `pdf_footnotes / not_applicable`（不再是
    模块里一个无原因的 `missing`）；`update-analysis.md` 的 Step 3 与 `PERIODIC_UPDATE_PLAN.md`
    §7.1 第 1 步把附注源写进 `--input` 清单（`.claude` 与 `.opencode` 两份镜像同步）。
  - `AC-2.5` 已实现（含 `F25`，见下）：① 摘录长度契约显式化——索引条目自带
    `quote_contract`（`index_window_chars` / `module_quote_max_chars`），写明「索引窗口是检索单位、
    模块引文是窗口内 ≤300 字逐字子段」，`context.py` 保证同一 evidence id 的引文**逐字落在
    `context_text` 展示的范围内**（F5/F13）；② 利润表**必选行**（营业收入/营业成本/财务费用/净利润/
    归母净利润）在截断时先保（F14），`section_states` 逐段给出 `full/truncated/omitted`；
    ③ bundle 增 `coverage_states`，把五态翻译成对本模块的可用性说明（F20）；
    ④ 同一段落可给最多 2 条摘录（`MAX_EVIDENCE_PER_SECTION`），采用**两遍分配**——先保证每个
    段落各 1 条（维持「大表不能饿死其他段落」的不变量），剩余预算再补第二块；真实数据包实测
    `environment` 由 7 条增至 9 条，MDA/P13/§3/§12 各拿到 2 块（F22）。
    **`F25` 已修（2026-09-25，本切片）**：引入具名子段引用 `<evidence_id>#<n>`
    （`n` 即该摘录的 `quote_index`，裸 id 等价 `#0`），见下面单独一条进度。
  - `AC-2.5` 的 **`max_evidence`/字符预算保底已实现（2026-09-25，本切片）**：
    ① `_module_evidence` 改成**三档轮转**的第一遍——必选节（§3 利润表 / §4 资产负债表 /
    §5 现金流量表 / §12 关键财务指标，`REQUIRED_MARKET_SECTIONS`）→ `prior_analysis` →
    其余来源，每轮每档最多 1 条，剩余槽位第二遍按组轮流补；旧顺序是 `prior_analysis`
    先拿满 6 条，实测把 `period_delta` 的 8 个 `market_data` 槽位全挤成 `omitted`
    （F29/AC-2.5 记的正是这一条）。
    ② 含必选行的段落（利润表）在字符预算里有下限 `REQUIRED_INCOME_SECTION_MIN_CHARS=900`，
    从**有余量**的段落按「最多让出一半份额」扣出来补给，池子总量不变；行首识别用正则，
    避免「归母净利润」行把资产负债表也误判成利润表。
    ③ `period_delta` 显式声明自己的预算（`max_evidence=16`、`max_chars=32000`）：
    它要同时装下必选节、6 段上一版结论与 PDF 正文，缺省 12 / 24,000 下必选节只剩
    97~170 字（实测 §5 现金流量表只剩表头）。`build_module_context` / `prepare` /
    CLI 的 `max_chars`/`max_evidence` 默认值改为 `None` = 「用模块自己的预算」，显式传参
    仍然优先；其余模块的缺省不变（12 / 24,000）。
    **真实 run 实测对照**（`output/600887_伊利/runs/20260925T091012981574Z`，只读重算）：
    修复前 `period_delta` 只有 12 条证据（6 prior + 5 pdf_sections + 1 附注）、
    `market_data` 8 个槽位全 `omitted`、context_text 6,255 字、§3 只剩 168 字（表头两行）；
    修复后 16 条证据（§3/§3P/§4/§4P/§5/§6/§12/§17 各 1 + 6 prior + MDA + MATTERS）、
    利润表 5 条必选行全部在 `context_text` 里、§3 = 816 字、§5/§12 = 168/454 字、
    `actual_chars=31,287 ≤ 32,000`。
  - `AC-2.4` **部分实现**：① `MDA` 的前置 buffer 页不再排在正文前面——`extract_section_context`
    改成「正文（best_page 起）在前、前置页作为带标注的『前置上下文』附在后面」，且截断只在正文里做，
    代表块必然落在正文（F12）；`SECTION_EXTRACT_CONFIG` 的 MDA/GOV/MATTERS `max_chars` 由 8,000 提到
    20,000。**真实 PDF 实测**（`output/600887_伊利/sources/pdf/600887_2026_中报.pdf`，本地文件、不联网）：
    `MDA` 由 7,999 字（开头是上一节的非经常性损益表）变为 9,376 字、开头即 p.10 的真实 MD&A 正文。
    ② 双栏串行：新增 `reflow_two_column_words`（按中缝判定，只对明显两栏页重排；否则交回
    `extract_text()`），带合成单测；**实测该中报未触发**（输出与改动前逐字节一致），所以真实数据上
    未获验证。③ **当时未做到**（后续已在下一进度条补做）：`MATTERS` 重大担保表的「列与值一一对应、
    担保逾期可判定」——
    用真实 PDF 定位了根因：pdfplumber 默认 `extract_tables()` 对这一页把多行列头堆叠在一起，
    换 `vertical_strategy=text / horizontal_strategy=lines` 也只能把「担保逾期金额 / 是否逾期 /
    反担保」并进同一个 cell（值 `是 4,811.72` 无法自动拆开），需要版面层或人工处理，
    留作后续切片。
  - `AC-2.4` 的**重大担保表**部分已实现（2026-09-25，本切片，三层落地）：
    ① **原文去重补全**（`scripts/pdf_preprocessor.py`）：`_drop_raw_duplicates_of_tables` 的判据
    从「整行等于某个单元格」扩到四条（整行 / 连续子串 / 片段全覆盖 / 折行拼接），把原文里
    **折行后的多行列头与错位数值**也认成表格复述。真实中报 `MATTERS` 由 8,012 字降到 6,739 字：
    p.37 原文那份错位的 16 列列头与「值 `4,811.72` 落在孤立行」的复述全部消失，进入索引的
    只有结构化表格（`| 担保是否 已经履行 完毕 | 担保 是否 逾期 | 担保逾期 金额 | ... |` 与
    `| 内蒙古 惠商融 资担保 有限公 司 | ... | 否 | 是 | 4,811.72 | 是 | 否 |` 列值对齐）。
    ② **索引窗口不劈表格**（`scripts/results/evidence.py:chunk_text`）：窗口尾部还落在 markdown
    表格里、且表格起点在本窗口后半段时，把边界提前到表格起点，让表头与首个数据行落在同一窗口。
    真实中报实测 `pdf_sections:MATTERS:003` = 16 列列头 + 内蒙古惠商融明细行，
    `MATTERS:004` = `担保总额（A+B） 907,629.75 / 占净资产比例 16.90` 汇总段。
    ③ **按证据槽位分预算 + 覆盖指向交付块**（`scripts/results/context.py:build_module_context`）：
    担保明细块与汇总块同属 `pdf_sections:MATTERS`，旧实现按真实段落分组会把第 2 块降级成
    「额外块」并按 <160 字丢弃，而 `evidence_coverage` 仍指向它。现按**槽位**分组
    （汇总块伪键 `pdf_sections:MATTERS:guarantee`），两块各拿一个「第一块」额度；
    `evidence_coverage` 改为指向真正渲染出来的块。**真实 PDF 实测**（同一中报，缺省预算）：
    `governance` 两块都在、`actual_chars=23,999/24,000`；`period_delta` 两块都在、
    `actual_chars=31,348/32,000`；两者的 `evidence_coverage["pdf_sections:MATTERS"]` 都指向
    已交付的 `MATTERS:004`。缺省 24,000 已够装两块，故**撤掉了本切片早先给 `governance`
    抬到 30,000 的临时预算**（保持 AC-2.5 的「只有 period_delta 显式抬高」不变量）。
    测试：`tests/test_pdf_preprocessor.py` 增 2 例（合成多行列头去重、正文不误删）、
    `tests/test_results_pipeline.py` 增 5 例（`chunk_text` 表格不劈窗、合成担保明细 + 汇总
    两块进 bundle ×2 模块、真实中报 p.37 端到端 ×2 模块，后者 `skipif` 于本地 PDF 存在）。
  - `AC-2.7` **已完成并通过独立验收**：① `run.as_of` 有了明确规则并由 `validate_result` 校验——
    必须是 `YYYY-MM-DD` 且不得晚于 `run.generated_at`（`schema.py:_validate_as_of`）；
    ② 输入指纹不再受重解析的易变字段影响：`describe_input` 对 JSON 输入按「去掉
    `extract_time` / `generated_at` 后」的规范化内容哈希（`manifest.py:input_content_sha256`），
    非 JSON 仍按原始字节（F1）；③ 永久性权限类错误不再走满重试——`is_permanent_api_error`
    命中「没有接口/无权限/permission denied」等标志词时立即放弃，采集器与选股器的
    `_safe_call` 都已接入（F3）；④ 规格与 schema 的枚举一致：environment 的
    `cyclicality` / `cycle_position` / `regulatory_risk` 增加 `unknown`，
    `shared/qualitative/references/output_schema.md` 同步（契约测试逐字比较）（F24）。
    F26 的「同输入同判断」伴随字段已由 `reconcile_results --prior-input` 产出
    `same_input_judgement` 冲突；跨 run 评级对账、卡片压缩与旧引用剥离也已分别写入
    `cross_dimension_findings`、`compaction` / `evidence_status` 元数据。F28 的内容指纹
    修复已完成，见上文。独立验收报告 [`2026-09-27-REQ-006.2-acceptance.md`](../verification/2026-09-27-REQ-006.2-acceptance.md)
    逐条确认通过。
  - 测试：新增 `tests/test_tushare_pack_sections.py`（13 例，夹具取真实响应、不做网络调用）；
    `tests/test_results_pipeline.py` 增 2 例（AC-2.6）+ 4 例（AC-2.5）+ 3 例（AC-2.7：
    指纹不受 extract_time 影响、非 JSON 仍按字节、as_of 规则）；`tests/test_tushare_client.py`
    增 2 例（权限错误只调一次 / 普通错误仍重试）；`tests/test_pdf_preprocessor.py` 增 5 例；
    `tests/test_prepare_primary_period.py` / `tests/test_prepare_prior_analysis.py`
    的夹具补上附注源，让「干净 run 无 warning」的断言继续成立。
  - **AC-2.8 实跑（2026-09-27 收口）**：真实 token 重新采集
    （`tushare_collector.py --code 600887.SH`，exit 0，日志首行即
    `yc_cb: permanent error …; not retrying`）→ 新数据包实测修复：
    §16 `总股本 632,536.07` / `无限售质押 39,775.10` 万股（原 63.25 / 3.98）；
    §12 营收同比增长率逐列有值（原整行 `—`）；§9 重复行消失、`合计特别调整` 毛利率为 `—`、
    口径说明带真实数字；§13.1 唯一告警是**本期** `2025H1→2026H1 资产减值 +628%`（原 2007/2008 噪声）。
    真实 PDF 重解析 → run `20260925T091012981574Z`（`runs.py new` + `prepare`，四个输入齐全）：
    `warnings` 空、`unavailable_inputs` 空（附注源按新清单传入）；证据索引 `unfilled_sections`
    只含 §8/§10、`quote_contract` 就位、最长摘录 1,199 ≤ 1,200 窗口；四个模块 bundle 都带
    `coverage_states`/`section_states`，`market_data:8/10` 为 `unavailable`。另用同一 PDF 两次解析
    验证 F1：原始字节不同（`extract_time`）、内容指纹完全相同。
    **LLM 半程（Step 5~9）随后由独立 agent 会话在同一个 run 上跑完**：6 模块
    `validate_result` 全 exit 0（未用 `--allow-legacy`）、`synthesis` 默认预算、
    `resolve_qualitative` `source=structured`、两篇报告落盘并写了台账。
    但本轮实跑暴露了下面必须登记的新缺口，故 **`AC-2.8` 仍不能判成立**。
    **产物处置（2026-09-25 owner 决定）**：正式半程 run `20260925T091012981574Z`
    **保留在 `output/600887_伊利/runs/` 作为 `AC-2.8` 的半程实跑证据**（不写台账、不影响
    `latest.json`，因为它是未完成的 run，写台账会误导）；另一个演示用 run
    `20260925T091003039909Z`（故意用错误章节包文件名，用于验证「缺失输入被登记」的披露路径）
    已**移出仓库**到 `/tmp/req0062-run/removed/`（未删除，按护栏不擅自删数据）。
    两轮真跑都写到 `/tmp/req0062-run/`，**公司级 `data_pack_market.md` 未被覆盖**，
    `latest.json` / `history.jsonl` 未变。
  - **本轮真跑同时暴露并已修的缺口**：`F13` 的「引文可在 context_text 里核对」在
    附注源与「同一段落第 2+ 块」上并不成立（真跑实测 environment 2 条、business_moat 5 条、
    mda_quality 4 条、governance 8 条）。现在每条 evidence 带 `in_context` 布尔与
    `selection.quotes_not_in_context` 清单——不可核对的引文**显式标出**（回索引核对），
    不再含糊；完全包含需要重构 context_text，留作后续。
  - **AC-2.8 实跑复核暴露的新缺口（2026-09-25，run `20260925T091012981574Z`）**：
    - **F29（新，中~高）附注源期次混用**：`pdf_footnotes` 输入是公司目录里的
      `data_pack_report.md` = **2025 年年报**附注包（资料截止 2025-12-31），而同一 run 的
      `pdf_sections` 是 **2026H1 中报**，bundle/报告里没有任何期次标记。后果：担保 A+B
      9,076.30 百万元 / 16.90%（中报）与 5,732.95 百万元 / 10.49%（2025 年报）、关联采购
      16,381.59 百万元、非经常性损益 496.84 百万元被当同期数据引用（agent 已在产物里改成
      显式年份并撤回跨口径比率，但那是**运行时人工纠正**）。**`AC-2.6` 只覆盖「源缺失」，
      不覆盖「源存在但期次不对」**：公司目录缺 `data_pack_report_interim.md` 时
      `prepare` 无 warning、`unavailable_inputs` 为空。需补「附注源期次必须与
      `primary_period` 一致」的校验与告警。**→ 已在本切片修复，证据见上面进度第一条。**
    - **`AC-2.5` 在真实载荷下仍未满足**：`period_delta` bundle 的 10 个 `market_data` 段落
      `selected_chars` 只剩 168–271，8 条 `market_data` 证据槽位**全部 `omitted`**
      （`max_evidence=12` 被 prior_analysis 6 + pdf_sections 5 + pdf_footnotes 1 占满），
      必填行（营业收入 / 归母净利润）不在 bundle 内 → D7 的 `revenue_yoy` / `net_profit_yoy` /
      `gross_margin_change` / `ocf_to_profit` 只能为 null、`status=partial`。
      即「必选节 + 每节首块」的保底在 `max_evidence` 分配层面仍会被打穿，需把
      `max_evidence` 也改成「先保结构（必选节 + 每节首块）再给 prior_analysis」。
      **→ 已在本切片修复，证据与实测对照见上面进度里 `AC-2.5` 那一条。**
    - **`AC-2.7` 元数据可信仍不成立**：`history.jsonl`/`record.json` 的 `framework.git_commit`
      = `a8b6147`、`run.json` = `eb84fc3`（squash 合入后都不可达 main）；
      `run.json` `dirty=false` 而 `run_manifest.json` `dirty=true`、
      `code_fingerprint=eb84fc3-dirty`；`latest.json` 没有 `framework` 字段。F28 同时复现：
      `prompt_fingerprint` 未变，仅因 squash 合并的 HEAD 变化就让 `analysis_status` 由
      exit 1 变 exit 3/`framework_changed`。
    - **F26 / F23 复议**：同一 2026H1 输入下本轮 vs 上轮给出 `business_trend` 稳定→恶化、
      `change_significance` 一般→轻微、`moat_evidence_strength` 强→中等、`pricing_power` 弱→中、
      `promise_delivery` 中→高、`mda_credibility` 低→中，而 `reconcile_results` 0 冲突；
      双栏串行错乱仍在 `evidence/index.json` 的 `pdf_sections:P3:001/002/004`，并已进入
      `business_moat`、`mda_quality` 的 `context_text`。
    - **F30（新，中）D7 的 `business_trend` 相对基准未定义**：同一 2026H1 数据，上一版按
      「run 间相对口径」记 `稳定`，本轮按 D7 定义（2026H1 vs 2025H1）记 `恶化`，规格没写
      「同期重跑」时该以谁为基准 → 需在 `shared/qualitative/agents/modules/period_delta.md`
      写明基准，否则跨 run 比对必然误判（本轮已在 `quality.warnings` 两种口径都说明）。
      **→ 已在本切片修复（基准写进规格 + schema 参考 + 契约测试）。**
    - **F31（新，低）bundle 元数据开销挤压正文**：`contexts/period_delta.json` 共 22,254 字符，
      `context_text` 只占 6,255，其余是 `selection`/`coverage_states`/`section_states`；
      预算循环为容纳这些元数据把 `content_budget` 压到约 9k，是上面「必选行丢失」的放大器。
      **→ 已在本切片按「量化 + 不变量」收口**：元数据开销的**危害**（挤掉必选节/必选行）
      由 AC-2.5 的结构保底解决，本切片给出实测口径（`period_delta`
      total 31,533 字符中 `context_text` 12,335（39%）、引文 7,193、其余元数据 ≈12,005（38%）；
      `governance` 分别为 23,935 / 8,437（35%）/ 6,818 / ≈8,680（36%）），
      并用测试钉住不变量「元数据再怎么占，必选节与必选行仍交付、总字数不超预算、
      正文+引文合计不低于总字数 40%」。**未做**：进一步压缩元数据本身（如
      `evidence[].locator` 每条重复完整绝对路径，实测占 5%）——收益有限且要动 schema，
      留作后续。
  - PR #49（进度切片，squash 合入 `0a5beac`）、**F29 修复切片（`F29` 的期次登记/判定/可见性三层）**；
    跟踪 Issue [#50](https://github.com/CHU-2002/Value_analysis_framework/issues/50)。

  - **2026-09-27 收口**：PR #60 的修复版本使用真实 token 重采集并完成完整增量闭环；独立验收报告
    [`2026-09-27-REQ-006.2-acceptance.md`](../verification/2026-09-27-REQ-006.2-acceptance.md)
    逐条通过 AC-2.1～AC-2.8，子需求推进为 `verified`。
- 目标：把 2026-09-25 的 AC-1.9 复跑（run `20260925T042255414048Z`）暴露的**数据包生成、PDF 抽取、
  证据索引与 bundle 预算、输入接线、跨 run 一致性**五类缺陷一次性收干净，并让每一类都留下**可判定**的回归判据
  ——这些缺陷全部是 mock 测试看不见的（真实载荷规模、真实接口字段、真实表格脏值、真实权限错误）。
- 背景：该次实跑共产出 21 条发现（原始记录见该编号的实跑记录）。
  其中 **F6（`synthesis` 默认预算装不下真实载荷）与 F15（丢弃计数少算）作为 REQ-006.1 AC-1.5 的
  直接判据归任务 T8** 处理，**F2（完备度把「无权限」算成成功）**已在 `REQ-009.4` 的 AC-4.5 里
  补为现状证据；其余按下列分组收敛：

  | 组 | 发现 | 现象（真实 run 实测） |
  |----|------|----------------------|
  | 数据包·分部与关键指标 | F8 / F9 / F10 | §9 分部表有重复行（`冷饮产品系列`≡`冷饮产品`、`其他主营业务`≡`其他`）且毛利率列写着字面 `nan`（按行朴素汇总 74,829.08，比真实合计高 16%）；§12「营收同比增长率」整行为 `—`（请求了 Tushare 不存在的 `revenue_yoy`，正确字段实测为 `or_yoy`/`tr_yoy`）；§9「产品」合计 64,489.72 与 §3 营业收入 64,330.94 差 158.78 且数据包未说明口径 |
  | 数据包·单位与量级 | F11 | §16「总股本 (万股) 63.25」实为**亿股**（接口本就返回万股、代码二次除 1e4），用 §1「总市值 17,015,220.01 万元 ÷ 26.90 元」反推应为 632,536 万股，**差 10,001 倍** |
  | 数据包·风险警示与占位板块 | F17 / F7 | §13.1 的 YOY 异常检测只报 2007→2008 / 2008→2009，本期「资产减值 +627.8%、商誉减值 0→1,546.54」反而没报；§8（行业与竞争）/§10（MD&A）/§13.2 永远是占位符（WebSearch 补充只在 PDF 下载失败时才做），而 D3 的 bundle 正以 §8 为行业证据来源 |
  | PDF 章节抽取 | F12 / 担保表列错位 | `MDA` 章节 `buffer_pages=3` 把前 3 页（非经常性损益）混进正文，`max_chars=8000` 又把真 MD&A 尾部截掉；`MATTERS` 的重大担保明细把「担保逾期金额/反担保/是否逾期」与主债务情况说明混排，担保是否逾期不可判定 |
  | 证据索引与 bundle 预算 | F5 / F13 / F14 / F20 | 索引 195 条里 178 条摘录超过契约的 300 字上限（最长 1,199），模块只能各自裁剪同一段摘录；同一 evidence id 的 `context_text` 与 `evidence[].quote` 覆盖行集不一致；利润表预算截断把「归母净利润」行砍掉（D5 的 `net_profit_mm` 只能为 null，且丢了上一轮尚可推导的单季归母 363.98）；`missing`/`omitted`/`truncated` 三态被混用，产生「不存在」式错误表述 |
  | 输入接线与元数据一致性 | F4 / F1 / F16 / F18 / F21 / F27 / **F29** / 重试策略 | `pdf_footnotes` 源在**每一轮** report-update run 都是 `exists=false`（`prepare` 找 `inputs/data_pack_report.md`，而规范里的 `--input` 清单从不含它），且 `prepare` 的 `warnings` 为空——**静默**；**F29**：附注源**期次**与 run 的 `primary_period` 不同期却无任何标记（2025 年报附注包进 2026H1 run）；同一 PDF 重解析只变 `metadata.extract_time` 却让输入指纹变化；模块 `as_of` 口径不一触发 high 级 `DATE-001` 并把整体置信度压到 low；对账器查不出**跨 run 评级被静默改写**（D2/D4 上调 vs D7 声称沿用）；`change_report` 的 `prior_synthesis` 卡片剥掉旧引用后无法区分「已剥离」与「本来无引用」，且 `synthesis` 卡片压缩后仍有 2 个悬空引用（`P4:003` / `P6:001`）；永久性 `no_permission` 仍走满 5 次重试 |
  | 第二轮重跑新增/加强 | F22 / F23 / F24 / F25 / F26 | **F22**：bundle 每节只给 1 条代表摘录（索引里 MDA 有 8 块、SUB 6、P3 4、§17 4、§4 3），D2 的核心证据（`MDA:006/007/008` 市场份额、`market_data:17` 的 Capex/5年CAGR）**在索引里但引不到**；**F23**：PDF 双栏交叉抽取导致文本串行错乱（「该类别票**据是由信用风险较**低银行出具」被另一栏切断），MATTERS 担保表还无法区分「0」与「未填写」；**F24**：模块规格要求 `unknown` 而 `cycle_position` 枚举没有它 → 「不知道」被迫写成「不适用」；**F25**：同一 `evidence_id` 被多模块以不同 `quote_index` 引用，而 sidecar 要求 id 唯一 → 同块内不同数值无法各自取得摘录；**F26**：模块 `run.status` 与 `business_trend`/`change_significance` 是 LLM 判断，**同一 `selection`** 下两轮分别给出 `complete`→`partial`、`恶化/重大`→`稳定/一般` |
  | 独立验收者发现 | F28 | `code_fingerprint` = HEAD sha → **纯文档提交也让 run 变 `framework_changed`**：在 `56a7d48`（只改 docs）上 `analysis_status` 由 exit 1/`report-update` 变 exit 3/`full-rerun`，即记录实跑的那次文档提交本身令该 run 失效（详见 `docs/verification/2026-09-25-REQ-006.1.md` 的存疑项，登记于 **AC-2.7**） |

- 验收标准：
  - **AC-2.1**：数据包的分部与关键指标可用——`§9 主营业务构成`不出现重复行、不出现字面 `nan`，
    且「产品」合计与 `§3` 营业收入的口径差在数据包内有文字说明；`§12` 的「营收同比增长率 (%)」
    在接口有该字段时必须逐列有值。由一条**对真实抓取结果做断言**的回归测试判定
    （夹具取自真实响应，不做网络调用）。
  - **AC-2.2**：数据包的单位与量级自洽——`§16` 的总股本与质押股数用正确单位输出，
    且存在一条一致性校验：用 `§1` 的总市值 ÷ 当前价反推的股数必须与 `§16` 的总股本一致（相对误差 <1%）。
    > 修法与依据（实跑已做判定性验证）：`pledge_stat` 返回的 `unrest_pledge=39775.1`、`rest_pledge=0.0`、
    > `total_share=632536.07`、`pledge_ratio=6.29`，而用 §1「17,015,220.01 万元 ÷ 26.90 元」反推为
    > **632,536.06 万股** —— 与接口 `total_share` 完全吻合，即**接口单位就是万股**；
    > 因此修法是**去掉 `other_data.py:436-438` 的 `divider=1e4`**（而不是改标签），
    > 并加一条用市值/价格反推股数的一致性测试。
  - **AC-2.3**：风险警示与占位板块不再误报/漏报——`§13.1` 的异常检测期次必须**包含本期**
    （不得使用最早可用列）；`§8`/`§10`/`§13.2` 的填充策略在需求与文档里明确到「增量更新流程到底填不填」，
    且 D3 的证据来源与该策略一致（不得再以占位符作为行业证据槽位）。
    > **策略（需求 owner 2026-09-25 决定，与 §7 的同类占位符一并适用）**：Agent 专属段落
    > （§7 定性治理信息 / §8 行业与竞争 / §10 MD&A / §13.2 风险补充）**只由全量分析填充**；
    > **增量更新流程不填**，占位符不得作为任何模块的证据槽位。落地方式：
    > `evidence.py` 把「只含占位符」的段落记入 `evidence_index.unfilled_sections` 并从 `entries` 剔除；
    > 模块 bundle 对这些槽位给 `evidence_coverage: unavailable` + `unavailable_inputs`（带原因）。
    > 文档见 [`docs/PERIODIC_UPDATE_PLAN.md` §7.1.1](../../PERIODIC_UPDATE_PLAN.md)；
    > 理由：增量更新的目标是快与可复现，行业信息变化慢，每次联网搜索会让同一份财报跑出不同结论
    > （参见 F26 的教训）；若行业/政策出现实质变化，走既有的 `stale:framework` 全量重跑。
    > 本条只把**既有判据**落到实现与文档，未改动 AC 文字。
  - **AC-2.4**：PDF 章节抽取可用于结论——`MDA` 章节里「前置上下文」与「章节正文」可区分
    （或前置部分不占用正文预算），且**代表块不得落在前置上下文上**（实测 `MDA:001` 整块是
    非经常性损益表，却被选为 D5 的唯一 MD&A 证据）；`MATTERS` 的重大担保明细列与值一一对应，
    `担保逾期` 信息可判定；双栏文本不得以串行错乱的形式进入证据索引（**F23**）。
  - **AC-2.5**：证据索引与 bundle 预算不丢关键行、语义不含糊——索引摘录不超出契约上限
    （或契约显式允许「索引摘录 + 子段范围」）；同一 evidence id 在 `context_text` 与 `evidence[].quote`
    的覆盖范围一致；利润表的**必选行**（营业收入、营业成本、财务费用、净利润、归母净利润）
    与**必选节**（资产负债表、现金流量表、关键财务指标）必须在 bundle 内
    （先保必选行/节再按预算填其余，实测 `period_delta` 有 7 个整节被 `omitted` 却仍要求必填指标）；
    同一节不得只给 1 条代表摘录就切断其余可用证据（**F22**）；`missing`/`omitted`/`truncated`
    三态在 bundle 里被翻译成「对本模块结论的可用性」说明；同一 `evidence_id` 的多段具名子段可被引用（**F25**）。
  - **AC-2.6**：输入接线与降级不得静默——附注证据源（`data_pack_report.md`，中报用
    `data_pack_report_interim.md`）要么被接进 run 的输入、要么被显式声明为不适用；源缺失时
    `prepare` 必须给出 warning，而不是让模块只在 `evidence_coverage` 里看到一个 `missing`。
  - **AC-2.7**：跨 run 一致性与元数据可信——`as_of` 有明确取值规则并被校验（不得晚于生成日、
    不得早于最近财报期末；实测六个模块给出 3 种不同 `as_of` 触发 high 级 `DATE-001`）；
    `reconcile_results` 对**评级类参数**（护城河/进入壁垒/管理层/诚信/飞轮）做跨 run 一致性检查，
    不一致时必须产出 conflict；同一份 PDF 重解析不得改变 run 的输入指纹；永久性权限类错误不得重试；
    规格与 schema 的枚举必须一致（**F24**：规格要求 `unknown` 而 `cycle_position` 没有该值）；
    卡片压缩后不得留下悬空引用、被剥离的引用必须显式标注（**F21/F27**）；
    对「同一输入应得同一判断」的字段（`run.status`、`business_trend` 等）应给出可机器判定的伴随字段（**F26**）；
    `code_fingerprint` 必须与「是否影响分析结果」同源——**只改 `docs/**` 的提交不得让 run 变成
    `framework_changed`**（**F28**：实测纯文档提交 `b23270b8 → 56a7d487` 就让 `analysis_status`
    从 exit 1/report-update 变成 exit 3/full-rerun，即记录实跑的那次文档提交本身令该 run 失效）。
  - **AC-2.8**（实跑）：修完后用**真实数据**复跑一次增量更新，逐条复核 `AC-2.1`~`AC-2.7` 在真实产物上的
    表现（命令 + 环境 + 观察），报告里带「## 实跑记录」；本次实跑发现的问题当次登记，不得只用运行时补丁绕过。
- 追溯：`tests/test_tushare_pack_sections.py`（AC-2.1~AC-2.3）、
  `tests/test_pdf_preprocessor.py`（AC-2.4）、`tests/test_results_pipeline.py`（AC-2.5、AC-2.7）、
  `tests/test_change_report.py`（AC-2.7）、`tests/test_runs_ledger.py`（AC-2.6、AC-2.7）；
  PR #49（进度）；跟踪 Issue [#50](https://github.com/CHU-2002/Value_analysis_framework/issues/50)

## 验收标准

- **AC-1**：分支模型为「一个特性一条特性分支」：子 PR 合入特性分支，特性分支**直接合入 `main`**；
  不维护长期集成分支（如 `develop`）；`main` 只接受来自特性分支的 PR 并受保护。
- **AC-2**：每个 PR（含合入特性分支的子 PR）都跑**全量**测试与覆盖率门禁；PR 描述中
  「研发自测（手工）」为必填栏，为空或只有占位时 CI 失败（`scripts/pr_body_guard.py`）。
  新功能必须有手工验证记录。
- **AC-3**：独立验收按**子需求/大特性收口**触发，**不按 PR**：只有把一个编号
  （`REQ-NNN` 或 `REQ-NNN.S`）推进到 `verified` 的那个 PR 才必须附带**独立验收报告**
  （`docs/verification/`），报告覆盖被推进的编号、逐条核对它自己的 `AC-n` / `AC-S.n`、
  记录全量测试结果、声明评审者独立性，并且该编号必须写进 PR 的「## 需求编号」批次（署名）；
  缺失或不合格时 CI 失败（`scripts/acceptance_gate.py`）。没有状态推进的 PR 不要求报告——
  一个子需求只做一次评审。
- **AC-4**：`main` 每累积 3 个特性合入（`feat` 提交或 merge 提交）后，若没有更新的批量全量回归记录
  （`docs/regression/`，含本批逐条 AC 结论），则下一个特性分支合 `main` 的 PR 被卡住
  （`scripts/regression_gate.py`）。
- **AC-5**：整体测试 scope 有登记表（`docs/TEST_SCOPE.md`）与预算（文件数 / 用例数上限），
  由 `scripts/test_scope.py --check` 强制：新增或删除测试文件必须同步登记表，超预算必须先清理。
- **AC-6**：本地 `make verify` 覆盖 CI 中**对仓库内容**的全部检查——编译/空白检查（含相对基线的
  整段 diff）、全量测试、覆盖率门禁、追溯门禁、测试 scope 检查、批量回归门禁；依赖 PR 元数据的
  `pr-title` / `pr-body` / `acceptance-gate` 由 CI 执行，`make gates` 给出本地预演方式。
  文档中的门禁描述、基线数字与实际一致，且不得出现已废弃分支（如 `develop`）的说法。
- **AC-7**：本需求下的工作以「## 任务清单」或**子需求**记录，每项有状态与证据；对既有门禁的修正**只允许更准或更严**，
  不得放宽任何既有判定；**唯一例外**是门禁自身的阈值/预算（覆盖率门槛、测试 scope 预算）——
  经需求 owner 书面批准并留下变更记录后可以调整，且必须在同一处写明代价与「先清理再谈上调」的纪律不变
  （这项确认在该子需求/大特性**收口时由独立评审者一次完成**，
  不再为每次零散修正单独拉一轮评审）。

  <!-- 需求变更记录（2026-09-20，AC-7 承载形式）
       原条款：「本需求下的工作以『## 任务清单』记录」。
       变更原因：一次真实财报分析实跑暴露的修复与查验工作是一个**可独立验收的切片**，
         需要自己的验收标准与实跑记录；这正是子需求（§6.1）的用途，而不是任务清单。
       新条款：工作以「任务清单」**或子需求**记录。
       批准人：需求 owner CHU-2002（2026-09-20 会话内要求「整个修复和查验工作作为一个小需求
         并入之前的大需求」）。
       独立评审：按评审粒度，在该子需求收口时一次完成。 -->

  <!-- 需求变更记录（2026-09-21，门禁阈值的例外通道）
       原条款（2026-09-20 登记）：「对既有门禁的修正只允许更准或更严，不得放宽任何既有判定。」
       变更原因：使用者已声明会持续给图形化控制台（REQ-009）追加新需求；REQ-009 的四个切片
         合计新增 4 个测试文件 / ≤64 条用例，做完后原预算（40 文件 / 1600 用例）只剩 14 条余量，
         后续每个 GUI 需求都会卡在门禁上。使用者在 2026-09-21 会话中被明确告知「上调与 AC-7 冲突、
         且现有预算其实够 REQ-009 做完」后，仍选择「确实要改 AC-7，上调到 48 文件 / 1800 用例」。
       新条款：修正仍只允许更准或更严；**唯一例外**是门禁自身的阈值/预算，经 owner 书面批准
         并留变更记录后可调整，且必须写明代价与「先清理再谈上调」的纪律不变。
         这不是放宽判定逻辑——判定仍是「超预算即失败」，只是阈值可经批准调整。
       代价（已实测并记录）：用例数上限 +12.5%；上调前全量测试实测 1519 passed、并行墙钟 28.45s。
         这是本仓库第一次上调 scope 预算，**不构成先例**。
       本次实施：`scripts/test_scope.py` 的 `MAX_TEST_FILES` 40→48、`MAX_COLLECTED_CASES` 1600→1800；
         `docs/TESTING.md` §5、`docs/DEVELOPMENT.md` §14、`docs/TEST_SCOPE.md` 同步；
         工作包登记为任务 **T7**。
       批准人：需求 owner CHU-2002（2026-09-21）。
       独立评审：按 AC-3 的评审粒度，在下一个子需求/大特性（REQ-006.1）收口时由独立评审者复核。 -->

  <!-- 需求变更记录（2026-09-20，AC-3 评审粒度）
       原条款（2026-09-20 登记）：「特性分支 → main」的 PR 必须附带独立验收报告。
       变更原因：使用者要求「评审要以子特性为标准，不要每一次 PR 都拉评审」——
         按 PR 触发会让每个合 main 的 PR（含纯补录）都要一份报告，评审成本与 PR 数成正比；
         而真正需要独立判断的时刻是「某个子需求/大特性宣布做完」。
       新条款：只有把编号推进到 verified 的收口 PR 才要报告，且该编号必须署名；
         一个子需求/大特性只做一次评审。不推进状态的 PR 一律不看报告。
       收窄还是加强：触发面收窄了（不再每 PR 都要报告），但**判定更严**——
         报告必须覆盖被推进的编号本身，堵住了「只申报更窄的子需求编号」绕过父需求 AC 的通道
         （该通道由独立评审者在 2026-09-20 实测发现，见 docs/verification/2026-09-20-REQ-006.md）。
       批准人：需求 owner CHU-2002（2026-09-20 会话内明确要求「以子特性为标准评审」）。
       独立评审：本次改动按新规则不需要单独评审；该条款的复核安排在下一个子需求/大特性收口时进行。 -->

  <!-- 需求变更记录（2026-09-20）
       原条款（2026-09-20 登记）：本地 `make verify` 覆盖 CI 的检查项；
         docs/DEVELOPMENT.md、docs/TESTING.md、CONTRIBUTING.md 的描述与实际门禁一致。
       变更原因：pr-title 与 pr-body 检查的是 GitHub 的 PR 标题与描述，acceptance-gate 还依赖
         PR 的 base/head 引用；这三项属 PR 元数据，本地命令无法等价复现，原条款按字面不可满足。
       同时加强而非放松：make verify 现在额外覆盖批量回归门禁（原条款未要求），lint 也从
         「只看未暂存改动」改为「工作区 + 相对基线的整段 diff」。
       批准人：需求 owner CHU-2002（2026-09-20 会话内明确批准本条修改）。
       独立评审：docs/verification/2026-09-20-REQ-006.md 记录了 AC-6 两轮不成立的具体依据。 -->

## 范围

**包含**

- 分支模型与三道门的文档、脚本、CI 作业与模板
- 测试 scope 登记表与预算
- PR 模板的「需求编号 / 研发自测 / 验收报告」栏位

**不包含**

- 引入外部 CI 服务或付费并发；仍只用 GitHub Actions
- 自动化的手工测试（不可能）：手工自测只做**留痕与评审**，不由 CI 判断质量
- 定时回归的调度平台（先用 GitHub 计划任务 + 手动触发）

## 约束与依赖

- CI 仍不得依赖网络与 `TUSHARE_TOKEN`（全 mock）。
- 门禁必须是**确定性**的：同一份 PR 描述与代码得到同样结论。
- 依赖已登记的 REQ-001…REQ-005 提供的期次、台账与验收标准格式。

## 追溯

| 项 | 内容 |
|----|------|
| 设计文档 | `docs/DEVELOPMENT.md`、`docs/TESTING.md` |
| 实现 PR | #25（建立，`22fe7fe`）、#29（门禁加固，`c65b47e`）、#30（验收戳）、#31、#32（T3 两处修正，`0d669c0`）、#34（T5 CI 提速，`2ed0662`）、T6 子需求机制（#35） |
| 测试 | `tests/test_release_gates.py`、`tests/test_requirement_traceability.py`、`tests/test_test_scope.py`、`tests/test_update_docs_contract.py`、`tests/test_two_layout_e2e.py` |
| 文档更新 | `docs/DEVELOPMENT.md`、`docs/TESTING.md`、`CONTRIBUTING.md`、`README.md`、`CHANGELOG.md` |

## 验收记录

| 日期 | 复验 sha | 合并 | 评审者 | 报告 | 结论 |
|------|----------|------|--------|------|------|
| 2026-09-20 | `a0d5ef6` | `22fe7fe`（#25） | 独立 agent（无上下文，未参与实现） | [`docs/verification/2026-09-20-REQ-006.md`](../verification/2026-09-20-REQ-006.md) | **AC-1…AC-6 全部成立**（共五轮复验：首轮与二轮 AC-6 不成立 → 两轮修复 → 三轮通过；CI 首次真实运行又暴露门禁自身缺陷 → 修复批次语义与两处加固 → 收尾轮与第 5 轮复核通过；其中 AC-6 条款变更经需求 owner 批准并留痕） |

## 维护与合并记录

- 2026-09-20：原 **REQ-007（门禁与治理工具加固）** 与 **REQ-008（覆盖率洼地补测）** 的内容并入
  本需求的任务清单 T2 / T4，两个编号置 `superseded`（保留不复用）。原因：使用者指出
  「维护开发流程」应当是**一条**需求，零散修正不该各占编号。
- 2026-09-20：原本为 T3 新开的 REQ-009 在合入前撤回，直接作为 T3 并入本需求。
- 2026-09-20：使用者要求「以后 REQ 以大特性的形式存在」并问怎么拆子需求 → 新增任务 **T6**，
  建立 `REQ-NNN.S` 子需求机制（规则见 [`README.md`](README.md) §6.1）。
  同一次对话里决定 **T4 覆盖补强不做**（不补测）。
- 2026-09-20：一次真实财报分析实跑（另一会话）暴露 6 个问题 + 1 个流程观察。首个版本把它们
  开成了新的顶层编号（REQ-009），被使用者指出**逻辑错误**：这些是对**已交付能力**的修正与
  流程补强，按 [`README.md`](README.md) §6 不该新开编号，应当是既有大需求下的一个**子需求**。
  已撤回该编号，改为本需求的 **REQ-006.1**（一个子需求承载全部修复与验收规则）；
  本需求状态随之从 `verified` 回落到 `in-progress`（§4 的不变量：父需求不得比最慢的子需求更靠前），
  待 REQ-006.1 收口后再回到 `verified`。
- 2026-09-20：使用者要求「把评审改掉，起码要以子特性为标准评审，不要每一次 PR 都拉评审了」
  → 修改 **AC-3**（变更记录见验收标准下方的注释），评审触发点从「合 `main` 的 PR」
  改为「编号推进到 `verified` 的收口」，一个子需求/大特性只做一次；
  该变更与 T6 的署名判定一起，反而堵住了「只申报子需求编号就能跳过父需求 AC」的通道。

**T5 交付内容**（PR #34）：`.github/workflows/ci.yml` 重写为两个 job；新增 `requirements-test.txt`；
`Makefile` 的 `test/cov/unit` 改为 `-n auto`；`CONTRIBUTING.md` 与 `docs/TESTING.md` 同步 CI 形态。
实测：site-packages 618MB → 357MB，全量测试串行约 55s → 并行约 30s。

**T6 交付内容**（需求模型：大特性拆子需求）：

| 改动 | 内容 |
|------|------|
| 规则 | `docs/requirements/README.md` 新增 §6.1（需求 / 子需求 / 任务 三层判据、`REQ-NNN.S` 与 `AC-S.n` 写法、8 条硬性约定）；§3、§4、§6、§8、§10 同步；`TEMPLATE.md` 增加「## 子需求」小节模板 |
| 唯一解析来源 | 新增 `scripts/req_registry.py`：父/子编号、子需求小节、状态、AC 提取集中一处，三个门禁不再各写一份编号正则 |
| 验收门禁 | `scripts/acceptance_gate.py` 按编号核对：写父需求核对 `AC-n`，写 `REQ-NNN.S` 只核对那个小节的 `AC-S.n`；子需求 `AC-1.1` 不再能满足父需求 `AC-1`（`(?![.\d])` 边界）；未登记的子需求编号报错而非放过。另加 `status_promotions()`：**把需求（父或子）推到 `implemented` / `verified` 的 PR 必须把该编号写进批次**，堵住独立评审者实测出的通道（只申报更窄的子需求编号 → 父需求 `AC-n` 永不被核对） |
| 追溯门禁 | `tests/test_requirement_traceability.py` 新增子需求与台账「子需求台账」的一致性、状态一致、`AC-S.n` 编号写法、子需求必须写在「## 子需求」小节内且编号唯一、以及**父需求状态不得先于最慢子需求**的不变量 |
| scope 与回归 | `scripts/test_scope.py` 归属列接受 `REQ-NNN.S`；`scripts/regression_gate.py` 的逐条验收小节接受子需求编号；`pr_body_guard` / PR 模板 / 开发与测试文档同步 |
| 评审粒度 | 独立验收改为**按子需求/大特性收口**触发（AC-3 变更，使用者要求）：`acceptance_gate` 只看「本 PR 是否把编号推进到 `verified`」，不看 PR 大小与目标分支；收口 PR 必须署名该编号并链接报告；`pr_body_guard` 不再要求报告栏；CI 的验收步骤对所有 PR 生效 |

**REQ-006.1 交付内容**（一次真实实跑暴露 6 个问题 + 1 个观察，全部落在同一个子需求里；
状态仍为 `in-progress`——**AC-1.9「无补丁复跑」要在真实 token + 只读 HOME 下由使用者执行**）：

| AC | 实跑暴露的问题 | 交付 |
|----|----------------|------|
| AC-1.1 | CNINFO 硬编码 https（403） | `discover_report.py` 协议可配置（`--cninfo-protocol` / `CNINFO_QUERY_PROTOCOL`）：默认 https，403 回退 http 且请求头一致并留痕，显式指定则钉死；+10 单测 |
| AC-1.2 | Tushare 要写 `~/tk.csv` | token 直接传给 `ts.pro_api(token, timeout=30)`（四处调用点），只读 HOME 下可用；+6 单测 |
| AC-1.3 | 文档漏 `--run-id`，台账与 run id 分叉 | 文档 Step 4 补 `--run-id {run_id}`；`prepare` 以 `run.json` 的 id 为准、不一致直接报错；契约测试钉住 |
| AC-1.4 | resolver 不认 `period_delta`（按文档喂就 digest 不匹配） | D7 登记为可选模块并参与 digest；不提供时 digest 与基线逐字节一致 |
| AC-1.5 | 默认预算装不下真实载荷且静默丢卡 | `synthesis` 默认 40k、`change_report` 默认 160k（按实跑实测载荷：30,124 / 38,070 / 26,141 / ~150k）；`budget.dropped`、`degraded` 显式记录并在 CLI 打印；文档写明依据 |
| AC-1.6 | `finish --artifact` 不校验 | 文件不存在/重复 name 报错且不写台账；相对路径按调用者 cwd 解析并入库绝对路径；+3 单测 |
| AC-1.7 | 模块 agent 越界引用 bundle 外证据 | `validate_bundle_evidence()` + `validate_result.py --context <bundle>` 可检出（越界即 exit≠0） |
| AC-1.8 | 实跑问题没有固定去处 | 已在 #36 交付：验收标准写「实跑」的编号，收口报告必须有「## 实跑记录」 |
| AC-1.10 | `TushareScreener._safe_call` 重试用的是旧客户端 | 每次尝试重新取客户端；回归测试证明「第一次失败、第二次成功」 |

本批实测：`make verify` → 1519 passed / 3 skipped，scope 33 支文件 1522 用例，三类门禁全绿；合入 `4450863`（PR #37）。

**T4 决定不做**（2026-09-20）：使用者明确「不用补测了」。覆盖率维持 76.80%、门禁维持 ≥74%；
`REQ-008` 文件保留为规格，编号仍为 `superseded`。

**T6 评审留痕说明**：首轮独立验收（报告见上）在 `d3d769e` 实测出一条真通道——只申报子需求编号
即可让父需求 `AC-n` 永不被核对——并给出另外两项「文档与实现不符」。实现者在后续提交里
逐条修复（新增 `verified_promotions()` 署名判定、子需求小节归属与唯一性检查、README 口径更正），
并把评审触发粒度改为「按子需求收口」（AC-3 变更）。**这次修复没有再经过独立复核**：
按新的粒度，复核在下一个子需求/大特性收口时做，这是使用者明确要求的节奏，此处如实记录。

**T3 交付内容**（两处修正，独立验收见 [`2026-09-20-REQ-006.md`](../verification/2026-09-20-REQ-006.md) 的「本次维护验证（T3）」一节）：

| 修正 | 之前的问题 | 交付 |
|------|------------|------|
| `acceptance_gate` 扫描 AC 前剔除围栏代码块、引用块与行内代码 | 报告里举例写「未打勾的 AC 长什么样」会被当成结论，评审者只好改措辞绕开 | PR #32；新增单测 2 项（举例不算结论 / 真正未打勾仍被抓出） |
| `test_scope --check` 比对归属列**内容** | 只查编号是否登记，补了标注忘 `make scope-write` 不会被拦（评审者靠人工 diff 才发现） | PR #32；新增单测 2 项，并在需求重构时真实触发过一次漂移告警 |

两项均以证伪法自检：关掉任一修复，对应测试失败；恢复后文件 sha256 一致。

## 备注

两次来自使用者的修正已固化为验收标准：

1. 「每个 PR 只测自己功能即可」修正为**CI 全量跑 + 新功能另需手工自测**，
   控制成本走整体 scope 维护（AC-2、AC-5）；
2. 不用长期集成分支：**一堆子 PR 合入特性分支，特性分支直接合 `main`**（AC-1），
   批量全量回归改为按特性计数、在 `main` 上每 3 个特性做一次（AC-4）。

**追溯门禁的一处副作用（2026-09-25 发现，登记为观察）**：AC-3 的「父需求不得比它最慢的子需求更靠前」
在 `tests/test_requirement_traceability.py::test_parent_status_cannot_outrun_its_sub_requirements` 里实现为
「父状态不得超前于子状态」。于是当父需求已经是 `in-progress` 时，**新登记、尚未开工的子需求不能如实写成
`accepted`** ——只能提前写 `in-progress`，或把父需求降级为 `accepted`（而父需求明明已有完成的任务与 PR）。
`REQ-006.1` 与 `REQ-006.2` 都是这么写成 `in-progress` 的。若要让「未开工」可表达，需要给子需求补一个
不参与父子比较的中间状态，或把不变量改成「只比较 `implemented` 及之后的状态」——**属门禁判定变更，
须按 AC-7 走 owner 批准 + 变更记录**，本次不做，只登记。
