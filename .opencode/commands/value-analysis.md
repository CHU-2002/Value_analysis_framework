Run a Buffett-Munger-Duan style Value Analysis (价值分析，含通用估值子模块) on stock: $ARGUMENTS

## Input Validation
- Stock code must be a valid A-share (e.g., 600887, 000858.SZ), HK stock (00700.HK), or US stock (AAPL)
- If $ARGUMENTS is empty or invalid, ask the user for a valid stock code before proceeding
- If only digits are given, the code will be normalized by scripts/config.py
- Store the validated canonical code as `{ticker}`. Derive `{directory_code}` by removing only the final market suffix, locate exactly one existing `output/{directory_code}_*/` company directory, and store it as `{output_dir}`.

## Prerequisite Check
Before executing, verify these files exist in output/{code}_{company}/:
- **Structured qualitative results** — preferred: the four core `modules/*/result.json` files.
- **qualitative_report.md** — compatibility fallback only when no `run_manifest.json` exists and the resolver selects `source=legacy`. A manifest-backed failure never falls back to Markdown.
- **data_pack_market.md** — required. If missing, same as above.
- **data_pack_report.md** — optional. If present, use annual report footnotes to validate cash/profit quality.
- If `qualitative_report.md` and `data_pack_market.md` exist under different `output/{code}_*/` directories for the same stock, reconcile them into a single directory before continuing

Resolve the qualitative source first:
```bash
.venv/bin/python -m scripts.results.resolve_qualitative --output-dir "{output_dir}" --ticker "{ticker}" --output "{output_dir}/qualitative_input.json"
```

If `data_pack_market.md` is missing or the resolver returns status 3 (status 2 is an invocation error):
- Automatically run `/business-analysis {stock_code}` first instead of stopping immediately
- After `/business-analysis` completes, rerun the resolver and re-check `data_pack_market.md`
- If `/business-analysis` stops because annual report download failed, stop and tell the user to download/provide the annual report PDF before continuing

## Execution Instructions

Read `strategies/value/coordinator.md` for the full pipeline specification, then execute each step:

### Step 1: Refresh market-sensitive data
```bash
cp "{output_dir}/data_pack_market.md" "{output_dir}/data_pack_market_current.md"
.venv/bin/python scripts/tushare_collector.py --code "{ticker}" --output "{output_dir}/data_pack_market_current.md" --refresh-market
```
- Preserve the hashed `data_pack_market.md`; use `data_pack_market_current.md` for current market cross-checks.
- Refreshes current price, market cap, weekly price series, and risk-free rate
- If the data pack is stale enough for full refresh fallback, allow the script to degrade automatically

### Step 2: Python precompute layer
```bash
.venv/bin/python scripts/value_analysis_engine.py --code "{ticker}" --output-dir "{output_dir}"
```
- Collects fresh structured data and produces deterministic value anchors
- Outputs: `output/{code}_{company}/value_computed.md`
- Contains: valuation anchor selection, owner earnings bridge, cross-cycle normalization, defensive balance-sheet checks, capital-allocation signals, scenario valuation, EE, asset backstop, and LLM adjustment interface

### Step 3: Value analysis and report assembly
- Read `strategies/value/phase2_value_analysis.md` for execution instructions
- Read `strategies/value/references/value_principles.md` for methodology anchors
- Read `strategies/value/references/report_template.md` for report structure
- Read `output/{code}_{company}/qualitative_input.json` for validated business quality, moat, management, evidence, and quality warnings
- Read `qualitative_report.md` only when `source=legacy`
- **Cross-check** material-event claims against the targeted annual-report evidence when needed, especially "收购/重组/发行股份/购买资产" in 重要事项. Record discrepancies without loading the complete PDF into the default context.
- Read `output/{code}_{company}/value_computed.md` for all deterministic numbers and scenario anchors
- Read `{output_dir}/data_pack_market_current.md` for current market cross-checks and supporting context
- Read `output/{code}_{company}/data_pack_report.md` if available for footnote-level validation
- Produce `output/{code}_{company}/{company}_{code}_价值分析报告.md`

## Method Requirements
- Do **not** use Graham-style net asset discounting as the primary thesis
- Use future cash-flow discounting / owner earnings thinking as the core valuation logic
- For cyclical, asset-heavy, or high-uncertainty sectors, add cross-cycle normalized earnings and a balance-sheet backstop alongside the cash-flow thesis
- Explicitly assess whether the current stock price is attractive relative to future discounted cash flows
- Include `EE = (市值 + 负债 - 现金) / 利润` as an acquisition-view valuation check
- Treat stock purchase as partial business acquisition, not ticker speculation
- If integrity, complexity, or defensive metrics are weak, do not force a positive recommendation even if a model shows upside

## Error Recovery
- Qualitative resolver returns unavailable → auto-run /business-analysis first; if PDF download fails there, stop and ask user for PDF
- Missing data_pack_market.md → auto-run /business-analysis first; if PDF download fails there, stop and ask user for PDF
- Market refresh failure → continue with existing pack and note reduced timeliness
- `value_analysis_engine.py` fails → check TUSHARE_TOKEN / Python deps, retry, then stop if still failing
- Missing data_pack_report.md → continue in degraded mode and disclose lower confidence on cash/profit quality
- Always produce a final report even with partial data, but allow the final action to be `观察 / 等待更好价格 / 放弃`

## Output
Final report: `output/{code}_{company}/{company}_{code}_价值分析报告.md`

Usage: /value-analysis 600887 or /value-analysis 00700.HK or /value-analysis AAPL
