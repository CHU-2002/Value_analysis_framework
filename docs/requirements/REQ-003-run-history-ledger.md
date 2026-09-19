---
id: REQ-003
title: 分析迭代台账（run-store）
status: verified
priority: P1
owner: CHU-2002
created: 2026-09-20
updated: 2026-09-20
issue: "#18"
design: docs/PERIODIC_UPDATE_PLAN.md
milestone: periodic-update-v1
pr: "#16（已合入 ab635c8）"
depends-on: REQ-001
supersedes: TBD
---

# REQ-003 分析迭代台账（run-store）

## 背景与问题

此前每次分析都覆盖同一目录：`run_manifest.json` 每次覆盖写、`status` 永远停在 `prepared`，
既没有历史也没有台账，**无法回答「上次结论是什么、跑的是哪一期、用的哪个框架版本」**。
这是增量更新（REQ-004）无法开工的根因。

## 目标

- 分析产物不可变：一个 run 落一个目录，跑完即冻结，后续数据变化不得让历史 run 失效。
- 提供追加式台账与「当前生效 run」指针，使「上次结论、期次、框架版本」可查。
- 提供状态判定器，回答「这家公司要不要重跑、为什么」。
- 能非破坏接管既有的扁平输出目录。

## 验收标准

- **AC-1**：`scripts/version.py` 提供 `FRAMEWORK_VERSION`、`prompt_fingerprint`（覆盖 `strategies/**`、
  `shared/qualitative/**` 与两份命令目录，按相对路径排序取 sha256）、`code_fingerprint`
  （git commit + dirty，非 git 环境退化为 `scripts/**/*.py` 哈希）、`schema_versions()`、`framework_block()`；
  同一输入重复调用结果稳定。
- **AC-2**：`scripts/runs.py` 提供 `new` / `resolve` / `finish` / `adopt` / `export`；
  run 落在 `runs/{run_id}/`，`run.json` 记录 `kind` / `periods` / `framework` / `supersedes`；退出码语义固定。
- **AC-3**：run 输入快照**默认真实复制**（`shutil.copy2`），`--hardlink` 才使用 `os.link`，
  `sources_manifest.json` 记录 `method`；**原地覆盖源文件后，快照字节与 sha256 不变**。
- **AC-4**：`adopt` 可非破坏接管既有扁平目录（默认保留原文件，`--prune` 才清理）；
  迁移后 `resolve_qualitative` 仍返回 `source=structured`，`--prune` 模式同样成立。
- **AC-5**：公司目录含 `latest.json`（当前生效 run 指针）、`record.json`（记录卡）、
  `history.jsonl`（追加式台账，每 run 一行），三者内容互相一致。
- **AC-6**：`scripts/analysis_status.py` 输出
  `no_record / legacy_layout / up_to_date / stale / broken / unsupported_market`，
  reasons 覆盖 `new_report` / `framework_changed` / `inputs_changed` / `schema_changed` / `run_failed` / `downstream_stale`；
  **`finish(status=failed)` 之后不得判为 `up_to_date`**；港股 / 美股返回 `unsupported_market`。
- **AC-7**：状态判定默认不联网：`--check-upstream` 关闭时不做任何网络访问，模块导入期不引入 `requests`。
- **AC-8**：manifest 的 `framework` 为**可选加性字段**：不传时输出与既有逐字节等价，`schema_version` 仍为 `1.0`。

## 范围

**包含**

- `scripts/version.py`、`scripts/runs.py`、`scripts/analysis_status.py` 及对应测试
- manifest 可选 `framework` 块
- 既有扁平布局的 `adopt` 接管

**不包含**

- 增量分析流程与变化报告（→ REQ-004）
- 命令路径重构与下游接线（→ REQ-005）
- 数据库、外部调度、git-annex 等重型依赖

## 约束与依赖

- 不得破坏既有「整组原子回退」：resolver 仍只认一个 run。
- 跨 run 只通过 `supersedes` 台账与变化报告发生关系，**绝不混用两个 run 的参数**。
- 依赖 REQ-001 的期次模型。

## 追溯

| 项 | 内容 |
|----|------|
| 设计文档 | `docs/PERIODIC_UPDATE_PLAN.md` §3、§8 |
| 实现 PR | #16（`feat(runs): run-store 台账、框架指纹与状态判定器`，已合入 `ab635c8`） |
| 测试 | `tests/test_version.py`、`tests/test_runs_ledger.py`、`tests/test_analysis_status.py` |
| 文档更新 | 待 REQ-005 |

## 验收记录

| 日期 | 基线 sha | 评审者 | 报告 | 结论 |
|------|----------|--------|------|------|
| 2026-09-20 | `397687f` | 独立 agent（无上下文，未参与实现） | [`docs/verification/2026-09-20-REQ-003-004-005.md`](../verification/2026-09-20-REQ-003-004-005.md) | **8/8 AC 通过**（含快照不可变、adopt 后仍 `structured`、failed 不判 `up_to_date` 的实测复现） |

## 备注

PR #16 已由无上下文独立子 agent 做过对抗式评审并修复 3 个阻断项，其中「硬链接快照并非不可变」
（刷新路径原地覆盖写导致旧 run 失效）正是本需求 AC-3 的直接来源。

本需求实现已随 PR #16 合入 `main`（`ab635c8`），状态推进到 `implemented`；
待逐条核对 AC-1…AC-8 后转为 `verified` 并关闭 #18。
