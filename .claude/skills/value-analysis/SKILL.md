# Value Investment Analysis — Skill Definition

## Skill Info
- **Name**: value-analysis
- **Description**: Run a full 巴菲特-芒格-段永平风格价值分析 on A-share, HK, or US stocks with business-analysis as prerequisite
- **Entry Point**: `strategies/value/coordinator.md`
- **Slash Command**: `/value-analysis <stock_code>`

## Dependencies
- **business-analysis**: If prerequisite outputs are missing, `/value-analysis` should automatically run `/business-analysis` first
- **Python venv**: `.venv/` with `tushare`, `pandas`, `pdfplumber` (created by `bash init.sh`)
- **Tushare Pro API**: Requires `TUSHARE_TOKEN` environment variable for market refresh

## Required Environment Variables
| Variable | Description | Required |
|----------|-------------|----------|
| `TUSHARE_TOKEN` | Tushare Pro API token | Yes |

## Pipeline Phases
1. **Prerequisite**: `/business-analysis` output validation; auto-run `/business-analysis` if missing
2. **Step 1**: Market data refresh (`scripts/tushare_collector.py --refresh-market`)
3. **Step 2**: Python precompute (`scripts/value_analysis_engine.py`)
4. **Step 3**: Veto-first value analysis, anti-thesis, and report generation

## Investment Style
- Primary lens: discounted future cash flow / owner earnings
- Business-first: qualitative moat, management, and capital allocation drive scenario selection
- Veto-first: if integrity, complexity, or balance-sheet defense is weak, default to wait / observe / pass
- Acquisition lens: use `EE = (市值 + 负债 - 现金) / 利润` plus owner-earnings purchase multiple as cross-checks
- Dual-anchor when needed: cyclical / asset-heavy / high-uncertainty names must also inspect balance-sheet backstop
- Not asset-cheap-first: no Graham-style net-asset discount requirement for ordinary quality businesses
- Sector-aware valuation is required: banks / insurers / brokers / other regulated financials should not be valued with ordinary-company Owner Earnings DCF as the primary method
- For regulated financials, prefer `ROE + capital adequacy + dividend capacity + P/TBV / residual income` framing; treat reported safety margin as moderate unless both normalized profitability and capital quality support it

## Output
- `output/{code}_{company}/` — all prerequisite and final files
- Precompute file: `value_computed.md` with valuation anchor, cross-cycle normalization, defensive layer, capital-allocation signals, and asset backstop
- Final report: `{company}_{code}_价值分析报告.md`
