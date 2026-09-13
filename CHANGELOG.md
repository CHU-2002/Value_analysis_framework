# Changelog

本文件记录本仓库（Value Analysis Framework）的变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> 上游龟龟框架 v1 → v3 的架构演进见 [CHANGELOG_V2.md](CHANGELOG_V2.md)。

## [Unreleased]

### Added

- 成熟的工程化文档：README、CONTRIBUTING、CODE_OF_CONDUCT、SECURITY、PR 与 Issue 模板
- GitHub Actions CI：在 push 与 PR 上运行 pytest
- CI 增强：`lint` 与 `pr-title` 检查、`ci-success` 汇总状态，用于分支保护
- CODEOWNERS 与可配置管理员名单 `.github/admins.yml`，管理员可直接合并 PR
- 一键应用 main 分支保护的工作流 `setup-branch-protection`（必需 CI + 必需 review + 管理员放行）
- 架构文档 `docs/ARCHITECTURE.md`
- 结构化定性结果管线：`scripts/results/`（schema、manifest、evidence、context、prepare、reconcile、synthesis、resolver）
- 价值分析模块 `strategies/value/` 与预计算引擎 `scripts/value_analysis_engine.py`
- 组合策略模块 `strategies/portfolio/` 与引擎 `scripts/portfolio_engine.py`
- 年报链接发现 `scripts/discover_report.py`

### Fixed

- 定性模块上下文按来源和章节分配预算，保证业务、治理附注及财务证据覆盖可见，避免长财务表挤占年报内容
- 汇总按完整 JSON 预算选取条目，保留参数、主判断及关键风险，记录省略项；传递模块精准引文及同 ID 的不同摘录
- 模块与最终 sidecar 校验增加 `--evidence-index`，核验 run、subject、来源、定位和连续原文；同步语义复核与附注交叉检查要求
- 在 `requirements.txt` 中补充缺失的运行时依赖 `markdown` 与 `numpy`
- 行情刷新改写为独立副本，避免破坏定性分析的原始证据快照
- 分支保护工作流区分用户仓库与组织仓库，避免用户仓库因 push restrictions 报错

### Changed

- 消费者统一通过 `resolve_qualitative` 获取定性输入
- `main` 分支启用保护，所有改动必须经 Pull Request 合入
- README 改为更直白的说明，并把来源与致谢统一放在文末
- CI 升级到 `actions/checkout@v5` 与 `actions/setup-python@v6`，适配 Node.js 24
- 通用估值（`/valuation`）降为价值分析的子模块，物理路径调整为 `strategies/value/valuation/`，仍可单独调用
- 共享数据层与选股器去除 "Turtle/龟龟" 命名
- 组合策略（`/portfolio-strategy`）的候选池改为以价值分析为主、选股器补充，不再依赖龟龟策略

### Removed

- 移除龟龟策略模块：`strategies/turtle/`、`/turtle-analysis` 命令与 skill；其能力由价值分析覆盖，上游项目仍保留在致谢中

## [0.1.0] - 2026-09-13

### Added

- 基于上游龟龟投资框架建立独立的 Value Analysis Framework 仓库
- 保留 MIT 许可证与上游版权声明，并在 README 中注明来源

[Unreleased]: https://github.com/CHU-2002/Value_analysis_framework/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/CHU-2002/Value_analysis_framework/releases/tag/v0.1.0
