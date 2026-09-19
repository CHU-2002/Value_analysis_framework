---
batch: YYYY-MM-DD-<特性名>
date: YYYY-MM-DD
reviewer: independent-agent
independence: independent
requirements: REQ-00X, REQ-00Y
base: <被验收的 main 基线 sha>
full-suite: "<passed> passed, <skipped> skipped，覆盖率 <x>%"
---

<!--
谁写这份报告：**没有参与实现的独立评审者**（人或独立 agent）。
不要由实现者自己写，也不要在报告里复述实现者的说法——以自己的观察为准。
写完放到 docs/verification/，并在「特性分支 → main」的 PR 正文「## 验收报告」里链接它。
CI 会用 scripts/acceptance_gate.py 校验：报告存在、覆盖本批全部 REQ、每条 AC 都打勾、有全量测试结果。
-->

# 独立验收报告：<批次名>

## 独立验收声明

<!-- 说明评审者是谁、如何保证独立性（例如：无上下文的独立 agent，未参与实现、未查看开发过程）。 -->

## 全量测试

```bash
make verify
```

- 结果：
- 覆盖率：
- 基线（对比的 main sha）：

## 逐条验收

<!-- 每条 AC 必须给出结论。未通过用 - [ ]，并在下面说明缺口。 -->

### REQ-00X <标题>

- [x] **AC-1**：
- [x] **AC-2**：

## 未通过 / 存疑项

<!-- 没有就写「无」。有则写清现象、复现命令、影响范围与建议处理。 -->

无

## 结论

<!-- 通过 / 有条件通过（列出条件）/ 不通过。 -->
