Run the General Valuation submodule of Value Analysis (价值分析子模块 · 通用估值) on stock: $ARGUMENTS

## Input Validation
- Stock code must be a valid A-share (e.g., 600887, 000858.SZ), HK stock (00700.HK), or US stock (AAPL)
- If $ARGUMENTS is empty or invalid, ask the user for a valid stock code before proceeding
- If only digits are given, the code will be normalized by scripts/config.py
- Store the validated canonical code as `{ticker}`. Derive `{directory_code}` by removing only the final market suffix, locate exactly one existing `output/{directory_code}_*/` company directory, and store it as `{output_dir}`.

## Prerequisite Check
Before executing, verify these files exist in output/{code}_{company}/:
- **Structured qualitative results** — preferred; `qualitative_report.md` is a fallback only in directories without `run_manifest.json` when the resolver selects `source=legacy`.
- **data_pack_market.md** — required. If missing, same as above.

Resolve the run directory first. `resolve_qualitative` accepts a **run directory** (one containing `run_manifest.json`) or a legacy flat directory — it does **not** follow `latest.json`, so passing the company directory would silently fall back to `source=legacy`/`unavailable`. When `{output_dir}/latest.json` exists, resolve the latest run and store the printed path as `{run_dir}`:
```bash
.venv/bin/python scripts/runs.py resolve --company-dir "{output_dir}" --latest
```
Without `latest.json`, set `{run_dir}` = `{output_dir}` (legacy flat layout).

Resolve and validate the qualitative source:
```bash
.venv/bin/python -m scripts.results.resolve_qualitative --output-dir "{run_dir}" --ticker "{ticker}" --output "{run_dir}/qualitative_input.json"
```
Status 3 means no consumable qualitative source; stop and request `/business-analysis {ticker}`. Status 2 is an invocation error.

## Execution Instructions

Read strategies/value/valuation/coordinator.md for the full pipeline specification, then execute each step:

### Step 1: Python Valuation Computation
```bash
.venv/bin/python scripts/valuation_engine.py --code "{ticker}" --output-dir "{output_dir}"
```
- Collects fresh data via Tushare, computes classification + WACC + all valuation methods
- Outputs: output/{code}_{company}/valuation_computed.md
- Contains: company type, WACC, each method's result + 5×5 sensitivity tables, cross-validation

### Step 2: LLM Qualitative Adjustment + Report
- Read strategies/value/valuation/phase2_valuation.md for qualitative adjustment instructions
- Read strategies/value/valuation/references/valuation_methods.md for methodology reference
- Read strategies/value/valuation/references/report_template.md for output format
- Read {run_dir}/qualitative_input.json for validated qualitative parameters, claims, risks, and evidence
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
Executable buy/sell plans belong to `/value-analysis`. This standalone valuation research command does not create or overwrite `buy_sell_basis.json` / `buy_sell_plan.json`: its generic company classification does not supply the primary value workflow's financial-sector guardrails or fixed valuation cycle. Run `/value-analysis {ticker}` for the executable plan; do not invent trading prices in this report.

Final report: output/{code}_{company}/{company}_{code}_估值报告.md

Usage: /valuation 600887 or /valuation 00700.HK or /valuation AAPL
