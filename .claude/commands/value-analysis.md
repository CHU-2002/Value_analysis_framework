Run a Buffett-Munger-Duan style Value Analysis (价值分析，含通用估值子模块) on stock: $ARGUMENTS

## Input Validation
- Stock code must be a valid A-share (e.g., 600887, 000858.SZ), HK stock (00700.HK), or US stock (AAPL)
- If $ARGUMENTS is empty or invalid, ask the user for a valid stock code before proceeding
- If only digits are given, the code will be normalized by scripts/config.py
- Store the validated canonical code as `{ticker}`. Derive `{directory_code}` by removing only the final market suffix, locate exactly one existing `output/{directory_code}_*/` company directory, and store it as `{output_dir}`.

## Prerequisite Check
Before executing, verify these files exist in output/{code}_{company}/:
- **Structured qualitative results** — preferred: the four core `modules/*/result.json` files inside the latest run directory. When `{output_dir}/latest.json` exists, resolve the run directory first and store the printed path as `{run_dir}`:
  ```bash
  .venv/bin/python scripts/runs.py resolve --company-dir "{output_dir}" --latest
  ```
  `resolve_qualitative` accepts a **run directory** (one containing `run_manifest.json`) or a legacy flat directory — it does **not** follow `latest.json` itself, so passing the company directory would fall back to `source=legacy`/`unavailable`. Without `latest.json`, set `{run_dir}` = `{output_dir}` (legacy flat layout).
- **qualitative_report.md** — compatibility fallback only when no `run_manifest.json` exists and the resolver selects `source=legacy`. A manifest-backed failure never falls back to Markdown.
- **data_pack_market.md** — required. If missing, same as above.
- **data_pack_report.md** — optional. If present, use annual report footnotes to validate cash/profit quality.
- If `qualitative_report.md` and `data_pack_market.md` exist under different `output/{code}_*/` directories for the same stock, reconcile them into a single directory before continuing

### Freshness check (newer periodic report)

If the company directory has iteration state (`record.json` / `latest.json`), check whether the conclusions are built on the latest published period:

```bash
.venv/bin/python scripts/analysis_status.py --company-dir "{output_dir}" --ticker "{ticker}" --json
```

- exit `0` (up to date) → continue.
- Route by `reasons[].code` rather than by exit code alone:
  - `new_report` / `framework_changed` / `schema_changed` / `inputs_changed` / `run_failed` → recommend `/update-analysis {stock_code}` first.
  - `downstream_stale` only means the frozen valuation inputs belong to an older period; `/update-analysis` will **not** clear it. Continue here, disclose it, and clear only the component this command refreshes: `.venv/bin/python scripts/runs.py downstream --company-dir "{output_dir}" --fresh value_computed`. `buy_sell_basis` is produced by `/buy-sell-plan`, not here, so the aggregate `downstream.stale` intentionally stays `true` (and `analysis_status` keeps reporting `stale:downstream_stale`) until the buy/sell plan is refreshed too — run `--fresh all` only after that.
- If the user chooses to continue on a stale period, state in the report that the analysis is based on an older period and record it as a data-freshness limitation. Never silently consume stale conclusions.

Resolve the qualitative source first:
```bash
.venv/bin/python -m scripts.results.resolve_qualitative --output-dir "{run_dir}" --ticker "{ticker}" --output "{run_dir}/qualitative_input.json"
```

If `data_pack_market.md` is missing or the resolver returns status 3 (status 2 is an invocation error):
- **When a run-store exists (`{output_dir}/latest.json`)**, do **not** auto-run `/business-analysis`: it writes the legacy flat layout and would silently discard the incremental run. Report the broken run and fix or re-run the incremental flow. (`runs.py adopt` is **not** an option here: it refuses a directory that already has `latest.json`.)
- Only for a directory without a run-store, automatically run `/business-analysis {stock_code}` first, then rerun the resolver and re-check `data_pack_market.md`
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
- Also outputs `value_computed.json` (structured valuation scenarios and independent method values). It does **not** generate a buy/sell plan or `buy_sell_market.json`; those are produced on demand.
- Contains: valuation anchor selection, owner earnings bridge, cross-cycle normalization, defensive balance-sheet checks, capital-allocation signals, scenario valuation, EE, asset backstop, and LLM adjustment interface

### Step 2B: Buy/sell plan is NOT generated automatically
This command only produces the research report and the frozen `value_computed.json`. Do **not** run `buy_sell_engine.py` here and do **not** fabricate a buy/sell section. The executable plan is triggered on demand by the user after reading the report (see `/buy-sell-plan`, documented in `docs/BUY_SELL_CONTRACT.md`).

### Step 3: Value analysis and report assembly
- Read `strategies/value/phase2_value_analysis.md` for execution instructions
- Read `strategies/value/references/value_principles.md` for methodology anchors
- Read `strategies/value/references/report_template.md` for report structure
- Read `{run_dir}/qualitative_input.json` for validated business quality, moat, management, evidence, and quality warnings
- Read `qualitative_report.md` only when `source=legacy`
- **Cross-check** material-event claims against the targeted annual-report evidence when needed, especially "收购/重组/发行股份/购买资产" in 重要事项. Record discrepancies without loading the complete PDF into the default context.
- Read `output/{code}_{company}/value_computed.md` for all deterministic numbers and scenario anchors
- Only if `{output_dir}/buy_sell_plan.json` already exists (the user triggered `/buy-sell-plan`), read it and insert `{output_dir}/buy_sell_plan.md` verbatim; quote the engine's action, tier, buy percentage, cumulative percentage, next price and exit price without recalculating. If it does not exist, do not invent a plan: state that no executable buy/sell plan has been generated and that `/buy-sell-plan` can produce one.
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
- Qualitative resolver returns unavailable → when `{output_dir}/latest.json` exists (run-store), do **not** auto-run `/business-analysis` (it writes the legacy flat layout and would discard the incremental run); otherwise auto-run `/business-analysis` first. If PDF download fails there, stop and ask user for PDF
- Missing data_pack_market.md → same rule: auto-run `/business-analysis` first only when there is no run-store; if PDF download fails there, stop and ask user for PDF
- Market refresh failure → continue with existing pack and note reduced timeliness
- `value_analysis_engine.py` fails → check TUSHARE_TOKEN / Python deps, retry, then stop if still failing
- Missing data_pack_report.md → continue in degraded mode and disclose lower confidence on cash/profit quality
- With partial data, quote the engine's explicit action and blockers when a plan exists. Never replace a generated plan with only `观察 / 可能买入`, and never fabricate unavailable prices. Without a triggered plan, say so plainly instead of writing a placeholder plan.

## Output
Final report: `output/{code}_{company}/{company}_{code}_价值分析报告.md`

Usage: /value-analysis 600887 or /value-analysis 00700.HK or /value-analysis AAPL
