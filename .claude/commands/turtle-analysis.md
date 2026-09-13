Run a full Turtle Investment Framework (龟龟投资策略) analysis on stock: $ARGUMENTS

## Input Validation
- Stock code must be a valid A-share (e.g., 600887, 000858.SZ), HK stock (00700.HK), or US stock (AAPL)
- If $ARGUMENTS is empty or invalid, ask the user for a valid stock code before proceeding
- If only digits are given, the code will be normalized by scripts/config.py
- Store the validated canonical code as `{ticker}`. Derive `{directory_code}` by removing only the final market suffix, locate exactly one existing `output/{directory_code}_*/` company directory, and store it as `{output_dir}`. Do not create a guessed directory during prerequisite resolution.

## Execution Instructions

Read strategies/turtle/coordinator.md for the full pipeline specification, then execute each phase:

### Prerequisite: Check BA outputs
- **Structured qualitative results** — preferred: the four core `modules/*/result.json` files from `/business-analysis`.
- **qualitative_report.md** — compatibility fallback only when no `run_manifest.json` exists and the resolver selects `source=legacy`. A manifest-backed failure never falls back to Markdown.
- **data_pack_market.md** — required. If missing, same as above.
- **data_pack_report.md** — optional. If missing, Agent B uses degraded mode (no PDF footnote data).

Resolve and validate the qualitative source before analysis:
```bash
.venv/bin/python -m scripts.results.resolve_qualitative --output-dir "{output_dir}" --ticker "{ticker}" --output "{output_dir}/qualitative_input.json"
```
If the command exits with status 3, inform the user to run `/business-analysis {ticker}` first, then stop. Status 2 is an invocation error and must be fixed. Do not combine parameters from an incomplete JSON set with the legacy report.

### Step A: Market Data Refresh
```bash
cp "{output_dir}/data_pack_market.md" "{output_dir}/data_pack_market_current.md"
.venv/bin/python scripts/tushare_collector.py --code "{ticker}" --output "{output_dir}/data_pack_market_current.md" --refresh-market
```
- Preserve the hashed `data_pack_market.md`. Pass `data_pack_market_current.md` to all quantitative and valuation steps; qualitative evidence remains tied to the original run.
- Refreshes §1 (price/market cap), §2 (52-week range), §11 (weekly prices), §14 (risk-free rate)
- If data pack is >7 days old, auto-falls back to full collection

### Phase 3: Analysis and Report
- **Step 3.0**: Read strategies/turtle/phase3_preflight.md for data validation
- **Step 3.1 Agent B**: Read strategies/turtle/phase3_quantitative.md for penetrating return rate calculation
- **Step 3.2 Agent C**: Read strategies/turtle/phase3_valuation.md for valuation + report assembly
  - Reads `qualitative_input.json` for validated qualitative parameters and evidence
  - Reads `qualitative_report.md` only when `qualitative_input.json.source=legacy`
  - Reads phase3_quantitative.md (from Agent B) for quantitative parameters
- Output: output/{code}_{company}/{company}_{code}_分析报告.md

## Error Recovery
- `qualitative_input.json.source=unavailable` → stop and prompt user to run /business-analysis first
- Step A refresh failure → attempt yfinance fallback, then proceed with existing data
- Missing data_pack_report.md → Agent B uses degraded mode (no footnote data)
- Always produce a final report even if partial data

## Output
Final report: output/{code}_{company}/{company}_{code}_分析报告.md

Usage: /turtle-analysis 600887 or /turtle-analysis 00700.HK
