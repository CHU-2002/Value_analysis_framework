Run a Valuation Analysis (估值分析) on stock: $ARGUMENTS

## Input Validation
- Stock code must be a valid A-share (e.g., 600887, 000858.SZ), HK stock (00700.HK), or US stock (AAPL)
- If $ARGUMENTS is empty or invalid, ask the user for a valid stock code before proceeding
- If only digits are given, the code will be normalized by scripts/config.py
- Store the validated canonical code as `{ticker}`. Derive `{directory_code}` by removing only the final market suffix, locate exactly one existing `output/{directory_code}_*/` company directory, and store it as `{output_dir}`.

## Prerequisite Check
Before executing, verify these files exist in output/{code}_{company}/:
- **Structured qualitative results** — preferred; `qualitative_report.md` is a fallback only in directories without `run_manifest.json` when the resolver selects `source=legacy`.
- **data_pack_market.md** — required. If missing, same as above.

Resolve and validate the qualitative source:
```bash
.venv/bin/python -m scripts.results.resolve_qualitative --output-dir "{output_dir}" --ticker "{ticker}" --output "{output_dir}/qualitative_input.json"
```
Status 3 means no consumable qualitative source; stop and request `/business-analysis {ticker}`. Status 2 is an invocation error.

## Execution Instructions

Read strategies/valuation/coordinator.md for the full pipeline specification, then execute each step:

### Step 1: Python Valuation Computation
```bash
.venv/bin/python scripts/valuation_engine.py --code "{ticker}" --output-dir "{output_dir}"
```
- Collects fresh data via Tushare, computes classification + WACC + all valuation methods
- Outputs: output/{code}_{company}/valuation_computed.md
- Contains: company type, WACC, each method's result + 5×5 sensitivity tables, cross-validation

### Step 2: LLM Qualitative Adjustment + Report
- Read strategies/valuation/phase2_valuation.md for qualitative adjustment instructions
- Read strategies/valuation/references/valuation_methods.md for methodology reference
- Read strategies/valuation/references/report_template.md for output format
- Read output/{code}_{company}/qualitative_input.json for validated qualitative parameters, claims, risks, and evidence
- Read qualitative_report.md only when `source=legacy`
- Read output/{code}_{company}/valuation_computed.md for all computed numbers
- Apply qualitative adjustments: D1 revenue quality → growth rate, D2 moat → terminal growth, D3 cycle → scenario weights, D4 management → governance discount
- Select adjusted scenarios from sensitivity tables (no arithmetic needed)
- Output: output/{code}_{company}/{company}_{code}_估值报告.md

## Error Recovery
- Qualitative resolver returns unavailable → stop and prompt user to run /business-analysis first
- valuation_engine.py fails → check TUSHARE_TOKEN, retry
- Classification ambiguous → Python defaults to 混合型
- A valuation method fails → Python skips it, weights redistributed
- Legacy qualitative report has no structured parameters → skip qualitative adjustments, use Python defaults
- Always produce a final report even with partial data

## Output
Final report: output/{code}_{company}/{company}_{code}_估值报告.md

Usage: /valuation 600887 or /valuation 00700.HK or /valuation AAPL
