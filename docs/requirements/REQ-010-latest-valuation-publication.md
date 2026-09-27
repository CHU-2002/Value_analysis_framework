---
id: REQ-010
title: 最新价值分析报告发布与历史版本保留
status: verified
priority: P1
owner: CHU-2002
created: 2026-09-27
updated: 2026-09-28
issue: "#61"
design: docs/PERIODIC_UPDATE_PLAN.md
milestone: TBD
pr: "#78, #79, #80, #81"
depends-on: REQ-003, REQ-004, REQ-005
supersedes: TBD
---

# REQ-010 最新价值分析报告发布与历史版本保留

## 背景与问题

`/update-analysis` 会生成新的定性分析 run，但不会自动重算价值分析。现有 `record.json` 会将旧 `value_computed` 标为 stale，然而公司目录里仍有价值分析报告，使用者不容易判断它是否基于最新财报期。`latest.json` 指向最新分析 run，却没有单独表达当前有效价值报告的版本与来源。

## 目标

- 分开记录最新分析 run 与最新价值分析产物，并明确价值分析是否过期。
- 成功重算后发布唯一的当前价值分析报告，且标明其来源 run 与财报期。
- 保留不可变 run 和历史报告，使使用者可追溯任一历史版本。
- 价值分析失败或产物不完整时，不覆盖最后一个成功版本。

## 验收标准

- **AC-1**：公司分析状态可分别解析最新成功分析 run 与最新成功价值分析产物，并为价值分析产物提供来源 run、财报期和新鲜度状态。最新分析财报期晚于价值分析基准时，状态必须为 `stale`。
- **AC-2**：读取“当前价值分析报告”的唯一入口解析到被登记为当前有效的报告，并返回其来源 run、财报期和新鲜度；当该报告为 `stale` 或 `unavailable` 时，入口必须明确返回该状态，不得将旧报告标为最新有效。
- **AC-3**：显式运行 `/value-analysis` 时，输入必须解析到最新成功且可消费的定性分析 run；成功产物必须登记来源 run、财报期及完整性摘要，并原子更新当前价值报告指针或等价发布镜像。
- **AC-4**：价值分析失败或产物校验未通过时，不得改变最后一个成功价值报告的字节、来源元数据或当前指针；状态继续显示 `stale` 或 `unavailable`，并留下本次失败记录。
- **AC-5**：更新当前价值报告不得改写既有 run 或历史价值报告；可按 run_id 解析历史版本，且当前指针指向的报告摘要必须与对应历史产物一致。
- **AC-6**：自动化测试覆盖增量更新后标记 stale、成功重算后更新当前指针、失败重算不覆盖成功版本、以及历史版本可追溯；全量测试不联网、不使用真实 Token。
- **AC-7**（实跑）：在真实公司数据上完成一次“增量更新 → 价值分析重算”，记录命令、环境、分析来源 run 与财报期、重算前后的状态以及当前报告指针。

## 范围

**包含**

- 最新分析 run 与最新价值报告的独立来源和新鲜度状态。
- 当前价值报告的读取入口、成功发布与失败保护。
- 价值报告与不可变 run 历史之间的追溯关系。

**不包含**

- 增量更新时自动运行价值分析；用户仍显式触发 `/value-analysis`。
- 自动刷新买卖计划；买卖计划仍单独触发，并继续按自身基准判断新鲜度。
- 删除或回收历史 run 与历史报告。

## 约束与依赖

- 依赖 REQ-003 的 run-store、REQ-004 的增量分析流程和 REQ-005 的下游接线。
- 历史 run 保持不可变；当前发布指针或镜像是派生产物，不得作为历史 run 的输入。
- 失败或不完整的新产物不得破坏最后一个成功版本。
- 测试不得访问网络或读取真实 Token。

## 手工自测清单

| 编号 | 对应 AC | 手工验证步骤 | 预期观察结果 |
|------|---------|--------------|--------------|
| MT-1 | AC-7 | 用真实公司完成增量更新，然后显式运行价值分析重算；检查状态记录、run 产物和当前价值报告入口 | 重算前显示 stale；重算后来源 run/财报期正确、状态为最新；历史 run 与报告保留 |
| MT-2 | AC-4 | 在受控实跑中触发一次价值分析失败或不完整产物校验 | 当前指针仍指向上一份成功报告，状态显示 stale/unavailable，并记录失败 |

## 实跑记录

2026-09-28 在真实公司 `output/600887_伊利`（600887.SH）上验证「增量更新 → 价值分析重算 → 发布」链路。
环境：仓库工作区 == 合并提交 `9519ef41b18c9355296666df590d740cceca4c46`（实现 PR #78），Python 3.12.13（`.venv`），
真实 `TUSHARE_TOKEN`（仓库根 `.env`，不落盘、不回显），联网可达 `api.tushare.pro`。

| 步骤 | 命令 | 观察 |
|------|------|------|
| 增量更新（该目录里已有的真实 run） | `scripts/runs.py resolve --company-dir output/600887_伊利 --latest` | run `20260926T164155496211Z`，kind=`report-update`，primary_period=`2026H1`，report_periods=`2025H1,2026H1`，status=`complete`（600887 当前最新已发布期次就是 2026H1，没有更新的期次可增量） |
| 重算前状态 | `scripts/value_publication.py read --company-dir output/600887_伊利 --json` | `unavailable` / `no_pointer`（退出码 3）；`analysis_status` 的 `value` 块同状态 |
| 价值分析重算 | `scripts/tushare_collector.py --code 600887.SH --output …/data_pack_market_current.md --refresh-market` + `scripts/value_analysis_engine.py --code 600887.SH --output-dir output/600887_伊利` | 退出码 0；`as_of=2026-09-28`、`financial_period=2026-06-30`、V_bear/V_base/V_bull = 19.94 / 26.82 / 31.28 元/股（`yc_cb` 仍无权限，已按既有口径披露） |
| 受控失败（`MT-2`） | `scripts/value_publication.py publish --company-dir output/600887_伊利 --report …/stub-report.md` | 退出码 2；`failures.jsonl` 记 `incomplete_product` / `report_non_empty`；指针仍未创建（`read` 仍 `unavailable`） |
| 成功发布 | `scripts/value_publication.py publish --company-dir output/600887_伊利 --report …/伊利股份_600887_价值分析报告.md` | 退出码 0；指针 `source_run=20260926T164155496211Z`、`primary_period=2026H1`、`report_sha256=6fcf8fe11f00…` |
| 重算后状态 | `scripts/runs.py downstream --company-dir output/600887_伊利 --fresh value_computed`，再 `read` | `fresh`（退出码 0）；`analysis_status` 顶层仍 `stale`（`buy_sell_basis` 与框架指纹），`value.state=fresh`——两条新鲜度线分开 |
| 指针/历史一致性 | `scripts/value_publication.py resolve --company-dir output/600887_伊利 --run-id 20260926T164155496211Z --json` + 自行 sha256 比对 | 指针摘要 == 冻结产物摘要；历史落在 `value_reports/20260926T164155496211Z/6fcf8fe11f00/`，另有 `revisions.jsonl` |

可复制的命令与逐条观察见验收报告 [`2026-09-28-REQ-010.md`](../verification/2026-09-28-REQ-010.md) 的「## 实跑记录」。
本轮实跑由实现 agent（非人类）执行；独立评审者复跑了其中的只读校验与产物一致性检查，这一证据强度限制已写进报告。

## 验收发现与处置

独立验收（[`2026-09-28-REQ-010.md`](../verification/2026-09-28-REQ-010.md)）分四轮核对：首轮判「有条件通过」并发现 1 个阻断项 V1 与 4 项观察；第二轮确认 V1 已修、新发现 O5；第三轮确认 O5 已修、新登记 O6/O7/O8；第四轮确认全部关闭并判通过。

| 编号 | 程度 | 现象 | 处置 |
|------|------|------|------|
| **V1** | 阻断（`AC-2`/`AC-5`） | 指针缺 `report_sha256`（字段被删或置 `null`）时 `read_current` 整段跳过摘要校验，一份被就地改写的报告被读成 `fresh` | **已修**：无摘要即 `unavailable`/`digest_missing`（PR #79），并为「删字段」与「置 null」两种写法各加一条回归用例；第二轮复验通过 |
| O1 | 观察 | 指针与 `revisions.jsonl` 存绝对路径，目录搬迁后 `read` 报 `unavailable`/`report_missing` | **登记不修**：与既有 `latest.json:run_dir` 同一口径；要支持搬迁应同时改两处指针，属独立改动 |
| O2 | 观察 | 人工删掉 `revisions.jsonl` 后，「最新版本」退回目录名序 | **登记不修**：只在日志被人为删除后发生，回退口径已写在代码注释与 `docs/ARCHITECTURE.md` §2D |
| O3 | 观察 | `history.jsonl` 不可读时库函数抛 `PermissionError` | **登记不修**：`analysis_status` 与 `value_publication` 两个 CLI 入口都已收敛为退出码 2、无 traceback；库消费方需自行捕获 `OSError` |
| O4 | 观察 | `tests/test_latest_valuation_publication.py` 里有一条恒真断言（`x == x`） | **已修**：换成对冻结副本路径的真实断言（PR #79） |
| **O5** | 非阻断（`AC-5`） | 指针可以改指到 `value_reports/` 之外那份可变报告、再用该文件的自洽 sha256「自签」，`read` 仍返回 `fresh`，而它并不是冻结历史产物 | **已修**：读取入口要求指针必须绑定到 `value_reports/<source_run>/<sha12>/`，且该目录 `manifest.json` 的摘要与指针一致、`report` 正是该副本（PR #80）；四条回归覆盖自签指针、目录外指针、被重写 manifest 与正面绑定 |
| **O6** | 非阻断（`AC-5`） | `snapshot_dir` 允许**等于** `value_reports/<run_id>/` 这一层，在那层自造 `manifest.json` + `report.md` 即可读到 `fresh` | **已修**：绑定判据改为 `snapshot.parent == value_reports/<source_run>/`，只认 `<sha12>` 修订目录（PR #81） |
| **O7** | 非阻断 | 指针 `source_run` 里带 `../` 会放大绑定接受范围 | **已修**：`source_run` 必须是单个普通路径分量（非空、非 `.`/`..`、不含路径分隔符）（PR #81） |
| **O8** | 非阻断 | 指针 `report` 用相对路径时，绑定按公司目录解析、存在性/摘要按进程 CWD 判断 | **已修**：存在性、摘要与绑定统一走同一次「按公司目录解析」的路径（PR #81）；`read` 返回的 `report` 恒为绝对路径 |

三轮修复的共同原则：**判据不足即可信度为零**，不做「看起来一致就放行」的兜底（V1 无摘要不认、O5 未锚定冻结产物不认、O6 不是恰好一个修订目录不认）。

## 追溯

| 项 | 内容 |
|----|------|
| 设计文档 | `docs/PERIODIC_UPDATE_PLAN.md` §8.7 |
| 实现 PR | #78（实现）、#79（独立验收 V1/O4 修复）、#80（O5 修复）、#81（O6/O7/O8 收紧） |
| 测试 | `tests/test_latest_valuation_publication.py`（另在 `tests/test_analysis_status.py` 断言状态输出新增的 `latest_successful_run` / `value` 块） |
| 文档更新 | `README.md`（「当前价值报告」小节 + 目录树）、`docs/ARCHITECTURE.md` §2C/§2D、`docs/PERIODIC_UPDATE_PLAN.md` §8.7、`CHANGELOG.md`、`.claude/commands/value-analysis.md` 与 `.opencode/commands/value-analysis.md`（Step 4 发布与失败登记） |

## 备注

- 需求讨论 Issue：[#61](https://github.com/CHU-2002/Value_analysis_framework/issues/61)。
- 买卖计划独立触发且有自己的新鲜度，不随本需求自动刷新。
