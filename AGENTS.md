# AGENTS.md

本仓库是**需求驱动**的：需求写在 `docs/requirements/`，测试挂在需求编号上，
**门禁在 CI 里自动判红判绿**。你不需要另记一套流程——下面这几份就是权威来源，已导入本文件：

@docs/requirements/README.md
@docs/DEVELOPMENT.md
@docs/TESTING.md

（还有两份按需查阅：提交/PR/CI 细则 [`CONTRIBUTING.md`](CONTRIBUTING.md)、
需求条目模板 [`docs/requirements/TEMPLATE.md`](docs/requirements/TEMPLATE.md)。）

## 每次动手的最小循环

1. **看状态**：读 [`docs/requirements/ledger.md`](docs/requirements/ledger.md)（需求台账 + 子需求台账），
   或直接跑 `make verify` 看当前是否干净。
2. **做事**：改代码/文档/测试；测试文件用 `# 覆盖需求：REQ-NNN`（子需求写 `REQ-NNN.S`）声明归属。
3. **跑门禁**：`make verify` 必须绿（lint + 全量测试 + 覆盖率 + 追溯 + scope + 回归）。
   增删测试文件后跑 `make scope-write` 重新生成 `docs/TEST_SCOPE.md`。
4. **开 PR**：正文「## 需求编号」与「研发自测（手工）」写实（`scripts/pr_body_guard.py` 会拦空栏）；
   用 `Refs #N` 而不是 `Closes #N`。
5. **合并**：CI 绿再合；`main` 每累积 3 个特性合入要补一条 `docs/regression/` 回归记录
   （`scripts/regression_gate.py` 会拦下一个合 main 的 PR）。

## 评审什么时候做（别每次都拉）

- **只有把某个编号推进到 `verified` 的那次收口 PR** 才需要独立验收报告
  （`scripts/acceptance_gate.py` 按 diff 判定，不看 PR 大小）；一个子需求/大特性只做一次。
- 收口 PR 必须把该编号写进「## 需求编号」**署名**，并在「## 验收报告」里链接报告。
- 验收标准里写了「**实跑**」的编号，报告必须有「## 实跑记录」（可复制的命令 + 环境 + 观察）：
  CI 全 mock，测不到外部数据源行为、运行环境约束、真实载荷规模。

## 护栏

**必须停下来问人**：

- 受理**新需求编号**（判定「这算不算新能力」是需求决策）；
- 修改或收窄任何验收标准（AC）；
- 放宽门禁判定、测试 scope / 用例预算上限、覆盖率门槛；
- 需要真实 token、真实数据源、真实环境的验收（「实跑」类判据由人执行）；
- 删除数据、重写 git 历史、绕过分支保护。

**永远不许**：

- 为了让门禁变绿而放宽判定、删断言、加 `skip`、写空 `except`；
- 让需求文件与台账状态不一致（必须同一次改动里同步；父需求不得比最慢的子需求更靠前）；
- 只把实跑/开发中发现的问题留在对话或提交信息里而不登记（写成子需求或任务）；
- 用运行时补丁（monkeypatch、临时改环境变量、手工改文件）代替修复而不留登记。

## 并行

互不重叠的改动各开一个 git worktree（`git worktree add .wt-xxx -b fix/xxx HEAD`）交给不同 agent，
各自自检并 commit，主线负责集成与门禁（`.wt-*` 已在 `.git/info/exclude`）。
