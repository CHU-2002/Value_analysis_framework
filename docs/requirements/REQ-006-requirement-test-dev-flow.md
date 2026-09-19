---
id: REQ-006
title: 需求-测试-开发流程与三道门
status: in-progress
priority: P1
owner: CHU-2002
created: 2026-09-20
updated: 2026-09-20
issue: TBD
design: docs/DEVELOPMENT.md
milestone: 工程化流程
pr: TBD
depends-on: REQ-001, REQ-002, REQ-003, REQ-004, REQ-005
supersedes: TBD
---

# REQ-006 需求-测试-开发流程与三道门

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

## 验收标准

- **AC-1**：分支模型为「一个特性一条特性分支」：子 PR 合入特性分支，特性分支**直接合入 `main`**；
  不维护长期集成分支（如 `develop`）；`main` 只接受来自特性分支的 PR 并受保护。
- **AC-2**：每个 PR（含合入特性分支的子 PR）都跑**全量**测试与覆盖率门禁；PR 描述中
  「研发自测（手工）」为必填栏，为空或只有占位时 CI 失败（`scripts/pr_body_guard.py`）。
  新功能必须有手工验证记录。
- **AC-3**：「特性分支 → `main`」的 PR 必须附带**独立验收报告**（`docs/verification/`），
  覆盖本批全部 `REQ-NNN`、逐条 `AC-n` 打勾、记录全量测试结果，且声明评审者独立性；
  缺失或不合格时 CI 失败（`scripts/acceptance_gate.py`）。
- **AC-4**：`main` 每累积 3 个特性合入（`feat` 提交或 merge 提交）后，若没有更新的批量全量回归记录
  （`docs/regression/`，含本批逐条 AC 结论），则下一个特性分支合 `main` 的 PR 被卡住
  （`scripts/regression_gate.py`）。
- **AC-5**：整体测试 scope 有登记表（`docs/TEST_SCOPE.md`）与预算（文件数 / 用例数上限），
  由 `scripts/test_scope.py --check` 强制：新增或删除测试文件必须同步登记表，超预算必须先清理。
- **AC-6**：本地 `make verify` 覆盖 CI 的检查项；`docs/DEVELOPMENT.md`、`docs/TESTING.md`、
  `CONTRIBUTING.md` 的描述与实际门禁一致。

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
| 实现 PR | TBD |
| 测试 | `tests/test_release_gates.py`、`tests/test_requirement_traceability.py` |
| 文档更新 | `docs/DEVELOPMENT.md`、`docs/TESTING.md`、`CONTRIBUTING.md`、`README.md`、`CHANGELOG.md` |

## 备注

两次来自使用者的修正已固化为验收标准：

1. 「每个 PR 只测自己功能即可」修正为**CI 全量跑 + 新功能另需手工自测**，
   控制成本走整体 scope 维护（AC-2、AC-5）；
2. 不用长期集成分支：**一堆子 PR 合入特性分支，特性分支直接合 `main`**（AC-1），
   批量全量回归改为按特性计数、在 `main` 上每 3 个特性做一次（AC-4）。
