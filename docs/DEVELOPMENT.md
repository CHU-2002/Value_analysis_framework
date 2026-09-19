# 开发流程

本文件定义从「想做一件事」到「确认做完」的完整路径。需求写什么见 [`docs/requirements/`](requirements/README.md)，
测试怎么写见 [`docs/TESTING.md`](TESTING.md)，分支 / 提交 / CI / 评审的细则见 [`CONTRIBUTING.md`](../CONTRIBUTING.md)。

## 1. 全景

```
登记需求        就绪评审        设计            拆分任务         实现
REQ-NNN    →   DoR 通过   →   docs/*.md   →   一个或多个 PR  →  feat/* 分支
   ↑                                                                    ↓
台账状态推进  ←  逐条验收   ←   合并（squash）  ←  Review + CI  ←  自检 make verify
   ↑                                                                    ↓
 verified                                                      PR 模板 / 测试追溯
```

一个阶段可以有多个 PR，一条需求也可以拆成多条需求；但**每个 PR 都必须能指回一个 `REQ-NNN`**。

## 2. 就绪定义（Definition of Ready）

需求进入实现前必须全部满足，否则停在 `proposed`：

- [ ] Issue 已开，背景写的是**事实与证据**（文件、行为、报错），不是主观判断
- [ ] 台账已有该 `REQ-NNN`，状态为 `accepted`
- [ ] 验收标准每条都**可判定**：给定输入 → 可观察结果
- [ ] 明确写出「本期不做」的范围，以及依赖的其他需求
- [ ] 依赖的上下游需求已 `verified`（或明确标注可并行）

## 3. 完成定义（Definition of Done）

一条需求的交付必须全部满足，才能从 `implemented` 推进到 `verified`：

- [ ] 代码已合入 `main`，CI 全绿（`pytest` × Python 3.10/3.12、`lint`、`pr-title`、`ci-success`）
- [ ] 测试覆盖每条验收标准，文件里标注 `# 覆盖需求：REQ-NNN`（见 [`docs/TESTING.md`](TESTING.md) §7）
- [ ] `make verify` 本地通过，覆盖率不低于门禁
- [ ] 文档同步：用户可见改动更新 `README.md`，架构或数据流变化更新 `docs/ARCHITECTURE.md`，
      行为变更写入 `CHANGELOG.md` 的 `[Unreleased]`
- [ ] 台账状态、关联 PR、里程碑已回填；Issue 在**验收通过后**才关闭
- [ ] 没有留下「已知偏差」未被记录（偏差要么修掉，要么写进需求条目或 Inbox）

## 4. 拆分任务

- 一条需求对应一个里程碑，**一个 PR 只做一件事**：能独立评审、能独立回滚、不放无关文件。
- 改动规模以「评审者能在一次专注阅读内看完」为准；超过约 800 行有效改动时考虑拆分。
- 有依赖关系的 PR 使用**栈式分支**（后一个 PR 基于前一个分支），并在 PR 正文写明合并顺序，
  合并前 rebase 到 `main`。
- 新需求在实现过程中被发现，不要塞进当前 PR：新开 `REQ-NNN` 并登记台账。

## 5. 分支、提交与 PR

细则见 [`CONTRIBUTING.md`](../CONTRIBUTING.md)：分支前缀（`feat/` `fix/` `docs/` `refactor/` `test/` `chore/`）、
Conventional Commits、Squash and merge、CODEOWNERS 与分支保护。

本流程额外要求：

- PR 正文写明 `REQ-NNN` 与覆盖到的 `AC-n`，便于反查与验收；
- 使用 `Refs #18` 而不是 `Closes #18`，避免合并即关闭 Issue 而跳过验收。

## 6. 本地校验

```bash
make verify   # lint + 全量测试 + 覆盖率门禁（提交前必跑，等价于 CI）
make help     # 列出全部目标
```

CI 会做同样的事，但本地失败比 CI 失败便宜得多。

## 7. 评审

- 至少 1 个 review 批准（CODEOWNERS 指定的审阅人）；所有对话解决后才能合并。
- 评审关注：是否满足验收标准、是否破坏既有原则（原子回退、全 mock、向后兼容）、文档与测试是否同步。
- **大型或高风险改动建议加一轮独立对抗式评审**：由无上下文的独立评审者按「找出阻断项」的目标复核，
  结论要能复现（给命令与证据），而不是只给意见。本仓库 PR #16 即以此方式发现并修复了 3 个阻断项。

## 8. 合并与验收

1. Squash and merge 合入 `main`（历史线性、信息清晰）。
2. **合并后立刻**把台账状态推到 `implemented`，回填 PR 编号（同一次改动或紧随的 `docs(req)` PR）。
3. 逐条核对验收标准；全部通过 → `verified`，并在 GitHub 关闭对应 Issue。
   不通过 → 记录缺口，状态回退 `in-progress`，并为缺口新开 `REQ-NNN` 或补 PR。
4. 若实现方式与设计文档不符，改设计文档，**不要改小验收标准**。

## 9. 度量

| 指标 | 现行门禁 / 基线 | 出处 |
|------|------------------|------|
| 测试覆盖率 | ≥ 74%（基线 76.77%） | `pytest.ini` 注释、CI、`make cov` |
| 需求追溯 | 台账 ↔ 条目 ↔ 测试引用一致，已交付需求必须被测试引用 | `tests/test_requirement_traceability.py` |
| PR 标题 | Conventional Commits，≤ 72 字符 | CI `pr-title` |
| 全量测试耗时 | 约 63s（1389 passed / 3 skipped） | `make cov` |

## 10. 反模式

| 反模式 | 后果 | 正确做法 |
|--------|------|----------|
| 先写代码，缺什么补什么需求 | 验收标准迁就实现，等于没有验收 | 先登记需求与验收标准（DoR） |
| 合并了就关 Issue | 跳过验收，`verified` 形同虚设 | 验收通过再关 |
| 台账只在心里更新 | 追溯链断裂，CI 会拦 | 状态与台账同一次改动内同步 |
| 一个 PR 混入重构 + 新功能 + 格式调整 | 无法评审、无法回滚 | 拆成独立 PR |
| 覆盖率不够就降低门禁 | 债务永久固化 | 补测或登记豁免理由 |

## 11. 与 AI agent 协作

本项目的分工是**Python 算数字、AI 读年报写判断**，因此 agent 也会改这个仓库：

- agent 的改动与人类改动**走完全相同的流程**：需求登记、测试追溯、CI、评审，没有例外通道。
- agent 适合承担的：批量补测、跨文件一致性核查、文档与代码同步、对抗式评审。
- 让 agent 做评审时，要给**独立的上下文与明确的对抗目标**（找阻断项），并要求给出可复现证据。
- agent 产出的「已完成」是待验证声明，不是验收结论：验收仍以 `AC-n` 与测试为准。
