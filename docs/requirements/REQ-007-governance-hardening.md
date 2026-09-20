---
id: REQ-007
title: 门禁与治理工具加固
status: verified
priority: P2
owner: CHU-2002
created: 2026-09-20
updated: 2026-09-20
issue: N/A（以 PR 跟踪）
design: docs/DEVELOPMENT.md
milestone: 工程化流程
pr: "#29"
depends-on: REQ-006
supersedes: TBD
---

# REQ-007 门禁与治理工具加固

## 背景与问题

REQ-006 落地的三道门在第一次实战（#25–#28）中暴露了一批**工具自身的**薄弱点。
它们都被独立验收记进了需求台账的 Inbox，本需求一次性处理完，而不是让它们散落在待办里：

1. `pr_body_guard` 的判据是「需求编号小节非空 **或** 正文任意位置有 `REQ-NNN`」，
   后者可被正文提及代替，弱化了「本批需求」声明的权威性；
2. `test_scope --check` 只比对文件集合与预算，**不校验归属列**——曾有 `REQ-999`
   悬空编号混进登记表而检查照过；
3. `test_scope --check` 的失败路径没有任何直接单测；
4. `regression_gate` 的记录内容校验只到「## 逐条验收」小节**存在**，小节里不写
   AC 结论也能过；
5. 扁平路径守卫的扫描根是硬编码的 4 个目录，根列表之外新增消费者文档不会被覆盖；
6. AC-5 的双布局端到端是**台账级**（`new` + `finish`），未跑 `prepare` 与模块，
   证明的是布局不变量而不是完整增量流水线；
7. prose 文档里硬编码测试数量与耗时，每加一个测试就要回填多处并重跑验收
   （REQ-006 一个周期内回填了 5 次）；
8. 测试夹具仍用已废弃的分支名 `develop` 作示例。

## 目标

- 让「本批需求」的声明只有一个权威来源，且缺了就直接失败。
- 让测试 scope 登记表**自身**的一致性（文件集合 + 归属编号 + 预算）都有检查、有单测。
- 让回归记录不是空壳。
- 让扁平路径守卫覆盖全仓提示词/规范，新增目录无需改测试。
- 把 AC-5 的端到端加强到真正跑通 `prepare`（含 `--prior-analysis`）。
- 让 prose 文档不再因测试数量变化而反复失效。

## 验收标准

- **AC-1**：`scripts/pr_body_guard.py` 要求任何 PR 的「## 需求编号」小节非空，
  不再接受「正文任意位置出现 `REQ-NNN`」作为替代；小节为空或只有占位符时失败。
- **AC-2**：`scripts/test_scope.py --check` 校验归属列：登记表里出现的每个 `REQ-NNN`
  必须在 `docs/requirements/` 下有对应条目，否则失败并点明是哪支文件的哪个编号。
- **AC-3**：`scripts/test_scope.py --check` 的三条失败路径各有直接单测：
  文件集合漂移、超出预算、归属编号未登记。
- **AC-4**：`scripts/regression_gate.py` 的记录校验要求「## 逐条验收」小节内**确有**
  AC 结论（`- [x] **AC-n**` / `- [ ] **AC-n**` / `REQ-NNN`）或指向 `docs/verification/`
  报告或写明「无功能改动」；小节存在但为空时失败。
- **AC-5**：扁平定性输入路径守卫扫描**全仓 `*.md`**（排除 `.git`、`.venv`、`output`、
  `docs`、`tests`、`node_modules`、`__pycache__`），基线生产者仍以显式白名单豁免；
  新增消费者目录无需修改测试即被覆盖。
- **AC-6**：AC-5 的端到端加强：在 run-store 布局的增量 run 上跑通
  `prepare_run(..., primary_period=…, prior_analysis=<上一 run 的 synthesis/result.json>)`，
  断言 manifest 记录 `primary_period` 与 `prior_analysis` 输入、证据索引含
  `prior_analysis` 源、`period_delta` 上下文能选中它，且 baseline run 仍可解析。
- **AC-7**：prose 文档不再硬编码测试数量与耗时：`docs/DEVELOPMENT.md`、
  `docs/TESTING.md`、`CHANGELOG.md` 只保留覆盖率基线（一位小数）并指向自动生成的
  `docs/TEST_SCOPE.md`；新增一个测试不再需要回填这些文档。
- **AC-8**：`tests/test_release_gates.py` 不再用已废弃分支名 `develop` 作夹具，
  改用 `feat/xxx`，与流程文档术语一致。

## 范围

**包含**

- 上述 8 项工具与文档的加固，以及相应单测
- Inbox 中 8 条工具类待办的清理（登记进本需求并从 Inbox 移除）

**不包含**

- 覆盖率洼地补测（→ REQ-008）
- 新功能（`--light` 结转模式、`runs/` 保留策略、港股/美股通路）——仍留在 Inbox，
  待验收标准可判定后再登记
- 改变三道门的判定口径（本需求只加固实现，不改 AC 语义）

## 约束与依赖

- 依赖 REQ-006 已合入的三道门与需求台账。
- 加固不得放宽任何既有判定：AC-1 与 AC-4 都是**加强**，不需要需求变更流程。
- 全部测试仍须 mock、离线、无 Token。

## 追溯

| 项 | 内容 |
|----|------|
| 设计文档 | `docs/DEVELOPMENT.md`、`docs/TESTING.md` |
| 实现 PR | #29（已合入 `c65b47e`） |
| 测试 | `tests/test_release_gates.py`、`tests/test_test_scope.py`、`tests/test_two_layout_e2e.py`、`tests/test_update_docs_contract.py` |
| 文档更新 | `docs/DEVELOPMENT.md`、`docs/TESTING.md`、`CHANGELOG.md`、`.github/PULL_REQUEST_TEMPLATE.md` |

## 维护记录

对已交付能力的**零散修正**记在这里，不新开需求编号（见 [`README.md`](README.md) 的粒度规则）。它们仍走正常的 PR 与门禁。

| 日期 | 修正 | 触发来源 |
|------|------|----------|
| 2026-09-20 | `acceptance_gate` 在校验 AC 前剔除围栏代码块、引用块与行内代码：报告里字面举例「未打勾的 AC 长什么样」不再被误判成结论 | 本需求独立验收存疑项（评审者实测踩到，只好改措辞绕开） |
| 2026-09-20 | `test_scope --check` 增加归属列**内容**比对：补了测试标注却忘记 `make scope-write` 不再能蒙混过关 | 本需求独立验收存疑项（评审者用只读 diff 实测漂移） |

## 验收记录

| 日期 | 复验 sha | 合并 | 评审者 | 报告 | 结论 |
|------|----------|------|--------|------|------|
| 2026-09-20 | `229593e` | `c65b47e`（#29） | 独立 agent（无上下文，未参与实现） | [`docs/verification/2026-09-20-REQ-007.md`](../verification/2026-09-20-REQ-007.md) | 见报告逐条结论 |

## 备注

来源均为 REQ-006 的独立验收（`docs/verification/2026-09-20-REQ-006.md`）与 REQ-005 的
复验（`docs/verification/2026-09-20-REQ-005-reverify.md`）在存疑项中列出的非阻断观察。
登记本需求即把它们从「散落的观察」变成「有验收标准的待办」。
