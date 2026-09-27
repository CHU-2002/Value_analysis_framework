# 提案暂存区（非权威）

> **这里的东西都不算数。** 需求的唯一权威来源是 [`docs/requirements/`](../requirements/)，
> 任何「要做什么、做到什么程度」以那里为准。本目录只放**还没受理的草案**：
> 产品走查结论、候选需求条目、方案比较。

## 规则

1. 本目录的文件**不进 `ledger.md`、不占 `REQ-NNN` 编号、不被任何测试引用**。
   写成 `REQ-011` 只是**占位建议**，owner 点头后才按
   [`docs/requirements/README.md`](../requirements/README.md) §7 正式登记（`cp` 到
   `docs/requirements/REQ-NNN-<slug>.md` + 台账加行 + 开 Issue）。
2. owner 拒绝或延后的草案**保留在本目录**（不删），把结论写回文件顶部；
   这样「为什么没做」不会只留在对话里。
3. 草案里的验收标准刻意写成可判定的样子——**写不出可判定 AC 的想法不该走这一步**，
   应该留在 `ledger.md` 的「待登记想法（Inbox）」。

## 现有提案

| 文件 | 是什么 | 建议编号 | 状态 |
|------|--------|----------|------|
| [2026-09-28-console-pm-review.md](2026-09-28-console-pm-review.md) | 控制台（REQ-009 交付物）的产品走查：问题清单、证据、改进建议 | — | 已读，结论已并入下面两条 |
| [2026-09-28-REQ-011-draft-data-acquisition.md](2026-09-28-REQ-011-draft-data-acquisition.md) | 候选需求：一次性全量数据获取 + 统一原始数据仓（存储结构重构） | `REQ-011` | **已受理** → [`../requirements/REQ-011-unified-data-acquisition.md`](../requirements/REQ-011-unified-data-acquisition.md) |
| [2026-09-28-REQ-012-draft-console-v2.md](2026-09-28-REQ-012-draft-console-v2.md) | 候选需求：控制台 2.0（公司上下文 + 任务式交互 + 视图质量） | `REQ-012` | **已受理** → [`../requirements/REQ-012-console-v2.md`](../requirements/REQ-012-console-v2.md) |

> 两份草案于 2026-09-28 由 owner（CHU-2002）受理并转正，**正式条目以 `docs/requirements/` 为准**；
> 本目录保留草案与走查报告，作为「当时看到了什么、为什么这么定」的证据。
> 受理时的三项决策（存储结构走 SQLite 单文件；图表混口径并入 `REQ-012.3`；测试预算新开文件、
> 必要时上调上限）已写进对应需求条目的「## 备注 · 受理记录」。
