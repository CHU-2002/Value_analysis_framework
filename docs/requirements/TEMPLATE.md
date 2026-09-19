---
id: REQ-NNN
title: 一句话说清要什么
status: proposed
priority: P2
owner: TBD
created: YYYY-MM-DD
updated: YYYY-MM-DD
issue: TBD
design: TBD
milestone: TBD
pr: TBD
depends-on: TBD
supersedes: TBD
---

<!--
front matter 字段说明（全部必填，未知填 TBD）：
  id          需求编号，与文件名前缀一致，永不复用
  title       一句话目标，不写实现方式
  status      proposed | accepted | in-progress | implemented | verified | deferred | rejected | superseded
  priority    P0 | P1 | P2 | P3
  owner       负责人 GitHub 用户名
  created     登记日期
  updated     最近一次状态/内容变更日期
  issue       关联 Issue 编号，如 #42
  design      设计文档路径（可带锚点）；纯实现类需求填 N/A
  milestone   关联里程碑名称
  pr          实现 PR 编号，如 #43
  depends-on  依赖的需求编号，多个用逗号分隔
  supersedes  被本条取代的需求编号，无则 TBD
-->

# REQ-NNN 一句话说清要什么

## 背景与问题

<!-- 现在的痛点是什么，为什么值得做。写事实与证据（文件、行为、用户反馈），不写主观判断。 -->

## 目标

<!-- 2-4 条，动词开头，说明「做完之后世界有什么不同」。 -->

## 验收标准

<!-- 每条必须可判定：给定输入 → 可观察结果。禁止「性能更好」「体验更佳」这类无法验证的表述。 -->

- **AC-1**：
- **AC-2**：
- **AC-3**：

## 范围

**包含**

-

**不包含**

<!-- 明确写出「本期不做」，防止范围蔓延。 -->

-

## 约束与依赖

<!-- 必须遵守的既有原则（如「整组原子回退」「测试不依赖 Token」）、外部依赖、上下游需求。 -->

-

## 手工自测清单

<!-- 自动化测试覆盖不到的、需要人确认的行为。实现者要照着这份清单在 PR 的
     「研发自测（手工）」栏里写清验了什么、怎么验、看到什么。 -->

| 编号 | 对应 AC | 手工验证步骤 | 预期观察结果 |
|------|---------|--------------|--------------|
| MT-1 | AC-1 | | |

## 追溯

| 项 | 内容 |
|----|------|
| 设计文档 | |
| 实现 PR | |
| 测试 | `tests/test_xxx.py` |
| 文档更新 | |

## 备注

<!-- 开放问题、风险、后续可能的拆分。 -->
