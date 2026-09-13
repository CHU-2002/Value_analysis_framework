# Changelog

本文件记录本仓库（Value Analysis Framework）的变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> 上游龟龟框架 v1 → v3 的架构演进见 [CHANGELOG_V2.md](CHANGELOG_V2.md)。

## [Unreleased]

### Added

- 成熟的工程化文档：README、CONTRIBUTING、CODE_OF_CONDUCT、SECURITY、PR 与 Issue 模板
- GitHub Actions CI：在 push 与 PR 上运行 pytest
- 架构文档 `docs/ARCHITECTURE.md`
- 结构化定性结果管线：`scripts/results/`（schema、manifest、evidence、context、prepare、reconcile、synthesis、resolver）
- 价值分析模块 `strategies/value/` 与预计算引擎 `scripts/value_analysis_engine.py`
- 组合策略模块 `strategies/portfolio/` 与引擎 `scripts/portfolio_engine.py`
- 年报链接发现 `scripts/discover_report.py`

### Fixed

- 在 `requirements.txt` 中补充缺失的运行时依赖 `markdown` 与 `numpy`
- 行情刷新改写为独立副本，避免破坏定性分析的原始证据快照

### Changed

- 消费者统一通过 `resolve_qualitative` 获取定性输入
- `main` 分支启用保护，所有改动必须经 Pull Request 合入

## [0.1.0] - 2026-09-13

### Added

- 基于上游龟龟投资框架建立独立的 Value Analysis Framework 仓库
- 保留 MIT 许可证与上游版权声明，并在 README 中注明来源

[Unreleased]: https://github.com/CHU-2002/Value_analysis_framework/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/CHU-2002/Value_analysis_framework/releases/tag/v0.1.0
