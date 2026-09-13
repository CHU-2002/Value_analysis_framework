# Portfolio Strategy — Skill Definition (v2.0)

## Skill Info
- **Name**: portfolio-strategy
- **Description**: 基于用户画像（年龄、资产、风险承受）构建集中化资产配置策略。遵循巴菲特"集中投资+安全边际"原则，输出精选个股为主的配置方案而非 ETF 超市。复用已有 /turtle-analysis 和 /value-analysis 的个股分析结果。
- **Entry Point**: `strategies/portfolio/coordinator.md`
- **Slash Command**: `/portfolio-strategy [user profile description]`

## Dependencies
- **business-analysis**: 若用户提及已有持仓需审查，自动检查并建议运行 `/business-analysis`
- **turtle-analysis**: 用于 A 股和部分港美股的个股深度分析
- **value-analysis**: 用于港股和美股的价值分析
- **Python venv**: `.venv/` with `tushare`, `pandas`, `numpy` (created by `bash init.sh`)
- **Tushare Pro API**: Required for screener and market data (for A/H shares)

## Required Environment Variables
| Variable | Description | Required |
|----------|-------------|----------|
| `TUSHARE_TOKEN` | Tushare Pro API token | For A/H stock analysis |

## Pipeline Phases
1. **Step 0**: User profiling — collect age, assets, risk tolerance, goals, existing holdings
2. **Step 1**: Macro + Valuation cycle assessment — global economic cycle, interest rates, geopolitics, China factors, **market valuation percentiles (PE/PB/CAPE)**
3. **Step 2**: Strategic Asset Allocation (SAA) — 7 core asset classes, valuation-adjusted weights, Python efficient frontier
4. **Step 3**: Tactical allocation & security selection — concentrated stock picks (3-5 A-shares, 1-2 HK, BRK.B + 0-2 US), ETFs only as research placeholder
5. **Step 4**: Portfolio construction & risk overlay — concentration acceptance, factor/sector check, scenario analysis, **valuation-mean-reversion stress test (v2.0)**
6. **Step 5**: Implementation roadmap — valuation-aware building plan, rebalancing rules, monitoring KPIs, annual review checklist

## Output
- `output/portfolio_{timestamp}/` — all intermediate and final files
- `profile.md` — user profile assessment
- `macro_assessment.md` — macro + valuation cycle evaluation
- `efficient_frontier.md` — Python-generated optimization results (7 assets)
- `strategic_allocation.md` — strategic asset allocation weights
- `tactical_allocation.md` — tactical allocation with concentrated instrument selection
- `portfolio_construction.md` — risk checks and scenario analysis
- `portfolio_strategy_report.md` — final integrated report

## Investment Philosophy (v2.0)
- **Concentration over diversification**: 5-8 stocks you truly understand > 20 ETFs you don't
- **Valuation honesty**: If multiple markets are at historical highs, raise cash to 25%+ and wait
- **Buffett principles**: Circle of competence, margin of safety, cash is a call option on panic
- **ETF is transitional**: Use ETFs only while researching; switch to individual stocks once research is done
- **Cash is ammunition**: 10-30% permanent cash position, not a drag but a weapon
- **Reuse not rebuild**: Individual stock analysis relies on existing `/turtle-analysis` and `/value-analysis`

## Extensibility
- New asset classes: Must justify irreplaceability; add to `asset_class_playbook.md`, `allocation_framework.md`
- New strategies: Create under `strategies/`, reference in `tactical_allocation.md`
- Multi-user: Support via `portfolio_{timestamp}_{username}/` output directories
