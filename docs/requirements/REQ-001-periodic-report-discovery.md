---
id: REQ-001
title: 定期报告发现与下载
status: verified
priority: P1
owner: CHU-2002
created: 2026-09-19
updated: 2026-09-20
issue: N/A（源自设计文档 PR #12）
design: docs/PERIODIC_UPDATE_PLAN.md
milestone: periodic-update-v1
pr: "#13, #14"
depends-on: TBD
supersedes: TBD
---

# REQ-001 定期报告发现与下载

## 背景与问题

发现层从未为非年报查询 CNINFO：`get_cninfo_category()` 对非年报返回 `""`，`should_prefer_cninfo()` 仅年报为真，
于是季度报 / 半年报只剩 `stockpage.10jqka.com.cn` 这一路，而该源基本只列年报，事实上拿不到季度报。
同时系统没有期次模型，只靠文件名约定 `{code}_{year}_{type}.pdf`，无法表达 `2026Q1` / `2026H1`，也无法回答「已覆盖哪些期次」。
结论：**季度报下载不是「完全没工具」，而是「壳有、通路没接」**。

## 目标

- 从 CNINFO 稳定发现 A 股四类定期报告（一季报 / 半年报 / 三季报 / 年报）。
- 能自动确定「最新已发布期次」，并支持按期次补齐下载。
- 引入可互转的期次标识，作为后续增量更新（REQ-004）与台账（REQ-003）的地基。

## 验收标准

- **AC-1**：`discover_report --report-type 一季报|半年报|三季报|年报` 对 A 股均能从 CNINFO 返回该类型公告；
  全文检索返回的同行报告不得被登记为本公司期次（只保留 `secCode` 与目标公司一致的条目）。
- **AC-2**：`--report-type auto`（或省略 `--report-type`）配合 `--latest` 时返回「任意类型的最新期次」，
  不得静默退化为最新年报。
- **AC-3**：期次以 `2026Q1` / `2026H1` / `2026Q3` / `2026A` 表达，可与 `(year, type)` 双向互转（`scripts/periods.py`）。
- **AC-4**：`--since` 早于默认回看窗口时自动拓宽查询窗口，不得出现「补齐」却静默漏期。
- **AC-5**：`sources_index.json` 记录已下载期次；已存在期次默认跳过并以 `--force` 重下；
  下载失败后索引中不残留指向已删除文件的陈旧条目。
- **AC-6**：单个非法上游链接记入 `periods_failed` 而非中断整批；非 JSON 响应按网络失败处理而非参数错误。
- **AC-7**：全部测试使用 mock 数据，不依赖真实网络与 `TUSHARE_TOKEN`。

## 范围

**包含**

- CNINFO 四类公告分类接入、`totalpages` 翻页、非 PDF 附件过滤
- 期次模型 `scripts/periods.py`
- CLI `--latest` / `--report-type auto` / `--since` / `--force` 与 `sources_index.json`

**不包含**

- 增量更新分析与变化报告（→ REQ-004）
- run-store 迭代台账（→ REQ-003）
- 港股 / 美股定期报告通路

## 约束与依赖

- 保留 10jqka 兜底；失败须明确报「期次未获取」，不静默降级。
- 年份路径与期次路径必须共用同一套标题关键词，避免两条路径结论不一致。

## 追溯

| 项 | 内容 |
|----|------|
| 设计文档 | `docs/PERIODIC_UPDATE_PLAN.md` §4、§5 |
| 实现 PR | #13（`feat(download): discover and download quarterly and interim reports`）、#14（评审问题修复） |
| 测试 | `tests/test_discover_report.py`、`tests/test_download_report.py`、`tests/test_periods.py` |
| 文档更新 | `README.md`、`CHANGELOG.md` |

## 备注

`--latest` 与周期模式 / `--url` 互斥的参数冲突已显式报错，避免参数被静默忽略。
