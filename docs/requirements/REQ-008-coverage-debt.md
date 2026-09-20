---
id: REQ-008
title: 覆盖率洼地补测
status: proposed
priority: P2
owner: CHU-2002
created: 2026-09-20
updated: 2026-09-20
issue: TBD
design: docs/TESTING.md
milestone: 工程化流程
pr: TBD
depends-on: REQ-006
supersedes: TBD
---

# REQ-008 覆盖率洼地补测

## 背景与问题

测试 scope 登记（REQ-006）让总量可控，但**覆盖深度**仍不均：`scripts/valuation_engine.py`
34%、`scripts/portfolio_engine.py` 47%、`scripts/split_data_pack.py` 45%，
另有三个 0% 的脚本（`report_to_html.py`、`md_to_mobile_html.py`、`generate_available_fields.py`）。
这些数字自 REQ-006 起就登记在台账 Inbox 里，一直没处理。

## 目标

- 把会被真实调用的模块补到约定下限，而不是笼统地「提高覆盖率」。
- 对确实属于一次性脚本的模块，给出**显式豁免**，而不是让它一直挂着 0%。

## 验收标准

- **AC-1**：`report_to_html.py`、`md_to_mobile_html.py`、`generate_available_fields.py`
  逐个给出判定：属于一次性脚本的，在本条目登记豁免理由，并在 `docs/TESTING.md`
  的「已知洼地」里标注为已豁免；仍会被调用的，补测试到 ≥ 70%。
- **AC-2**：`scripts/valuation_engine.py` 覆盖率 ≥ 60%（当前 34%）。
- **AC-3**：`scripts/portfolio_engine.py` 覆盖率 ≥ 70%（当前 47%）。
- **AC-4**：总覆盖率 ≥ 80%（当前 76.4%），并把覆盖率门禁从 74% 提到不低于 78%。
- **AC-5**：补测**只加测试**，不改变生产代码行为；确需改代码时另开需求。
- **AC-6**：测试 scope 仍在预算内（文件数 ≤ 40、用例数 ≤ 1600），超预算先清理。

## 范围

**包含**

- 上述模块的测试补充与豁免判定
- 覆盖率门禁的上调

**不包含**

- 为了数字而写的无断言测试（属反模式，见 `docs/TESTING.md` §10）

## 约束与依赖

- 依赖 REQ-006 的 scope 登记与预算机制；依赖 REQ-007 对 `test_scope --check` 的加固。
- 补测必须 mock、离线，且不得降低既有断言的强度。

## 追溯

| 项 | 内容 |
|----|------|
| 设计文档 | `docs/TESTING.md` §6 覆盖率、§5 scope 与预算 |
| 实现 PR | TBD |
| 测试 | 待补（本需求本身就是补测试） |
| 文档更新 | `docs/TESTING.md`（洼地清单）、`scripts/test_scope.py`（预算上限） |

## 备注

本需求尚未受理。开工前需要先做 AC-1 的判定：那三个 0% 脚本到底还会不会被调用
（若要判定，先读 `README.md`/`docs/ARCHITECTURE.md` 的引用与命令接线）。
