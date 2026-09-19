Generate an executable buy/sell plan for an already-analysed company: $ARGUMENTS

## When to run
Run this only after the user has read the value-analysis report and explicitly decides to generate a plan. The default `/value-analysis` run never produces one.

## Input Validation
- Stock code must be a valid A-share (e.g., 600887, 000858.SZ), HK stock (00700.HK), or US stock (AAPL)
- If $ARGUMENTS is empty or invalid, ask the user for a valid stock code before proceeding
- Store the validated canonical code as `{ticker}`. Derive `{directory_code}` by removing only the final market suffix and locate exactly one `output/{directory_code}_*/` directory as `{output_dir}`
- Require `{output_dir}/value_computed.json` (the frozen valuation basis). If missing, stop and run `/value-analysis {stock_code}` first

## Execution
Read `docs/BUY_SELL_CONTRACT.md`, then run:

```bash
.venv/bin/python scripts/buy_sell_plan.py --code "{ticker}" --output-dir "{output_dir}"
```

- Collects a fresh quote, writes `buy_sell_market.json`, then runs the offline engine against the frozen valuation basis
- Outputs: `buy_sell_market.json`, `buy_sell_basis.json`, `buy_sell_plan.json`, `buy_sell_plan.md`
- Never recompute or overwrite `value_computed.json`; the dated frozen basis governs trading prices even when the current research valuation differs
- Reuse `buy_sell_state.json` when present; never infer fills from a prior recommendation. Without state, this is a first-allocation proposal in percentages, not an additional order on rerun
- Structured `integrity_rating=不可靠` is a hard exit. For evidence-confirmed financial fraud, governance failure, insolvency, or core-business failure, record the exact references in `buy_sell_risk.json` and rerun. Do not infer these from generic caution flags
- Exit code 3 means a current BLOCKED plan was written: quote it including missing-data reasons and null prices. Exit code 2 means invalid inputs/invocation: stop and fix; never consume an old plan after failure

## Report Update
- Insert `{output_dir}/buy_sell_plan.md` verbatim into the existing report `{output_dir}/{company}_{code}_价值分析报告.md`; add the buy/sell section if the report has none
- Quote the engine's action, tier, buy percentage, cumulative percentage, next price and exit price without recalculating or overriding them
- Distinguish the frozen plan basis date from the current research valuation date

## Error Recovery
- Missing `value_computed.json` → run `/value-analysis {stock_code}` first, then retry
- Data collection failure → check TUSHARE_TOKEN / network, retry, then stop if still failing
- BLOCKED plan → disclose blocking reasons and null prices; never fabricate prices

## Output
Plan: `{output_dir}/buy_sell_plan.md`; updated report: `{output_dir}/{company}_{code}_价值分析报告.md`

Usage: /buy-sell-plan 600887 or /buy-sell-plan 00700.HK or /buy-sell-plan AAPL
