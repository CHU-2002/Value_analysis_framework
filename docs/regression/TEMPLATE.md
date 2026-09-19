---
date: YYYY-MM-DD
covered-until: <main 上的 sha>
reviewer: TBD
full-suite: "1442 passed, 3 skipped"
coverage: "76.36%"
---

<!--
什么时候写：main 每累积 3 个特性合入必须跑一次批量全量回归并留档（含本批逐条 AC 结论），
否则 scripts/regression_gate.py 会卡住下一个特性分支合入 main 的 PR。
草稿可以直接用：python scripts/regression_gate.py --new
放在 docs/regression/ 下，文件名用日期，如 2026-09-20-batch1.md。
-->

# main 全量回归记录

## 覆盖范围

<!-- 覆盖到哪个 sha，自上次记录以来累积了哪些功能合入。 -->

## 全量测试

```bash
make verify
```

结果：

## 逐条验收

<!-- 至少给出本批需求的 AC 结论，或链接对应的 docs/verification/ 报告。 -->

## 结论

<!-- 通过 / 发现的问题与处理。 -->
