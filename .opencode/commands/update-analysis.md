Run an incremental periodic-report update analysis (定期报告增量更新) on stock: $ARGUMENTS

## Input Validation
- Stock code must be a valid A-share (e.g., 600887, 000858.SZ). This command currently supports A-shares only because it depends on CNINFO regular-report discovery.
- If `$ARGUMENTS` is empty or invalid, ask the user for a valid A-share code before proceeding.
- An optional period (e.g. `2026H1`) may be given; otherwise the newest published period is used.
- Store the validated canonical code as `{ticker}` and the existing `output/{directory_code}_*/` company directory as `{company_dir}`. The run-store layout is described in `docs/PERIODIC_UPDATE_PLAN.md`.

## Prerequisite Check
Decide whether this is an update or a baseline:

```bash
python3 scripts/analysis_status.py --company-dir "{company_dir}" --ticker "{ticker}" --json
```

| Exit code | Meaning | Action |
|-----------|---------|--------|
| 0 | already up to date | report this to the user and stop; do not re-run analysis |
| 1 | a new report is available | continue with the incremental flow |
| 3 | changed inputs, framework/schema change, incomplete run, or broken record | continue, but the change report must state that the prior basis was invalidated |
| 2 | invocation error | fix the invocation |

- No analysis record at all → run `/business-analysis {stock_code}` first to establish a baseline, then return here. Never fabricate an incremental update without a record.
- `{company_dir}` missing but an analysis exists under another directory for the same code → reconcile into one directory before continuing.

## Execution Instructions

Read `shared/qualitative/coordinator_update.md` for the full pipeline specification and execute every step:

### Step 1: Discover and download the newest period(s)
```bash
python3 scripts/download_report.py \
  --stock-code "{stock_code}" \
  --report-type auto \
  --since "<first period missing from record.json>" \
  --save-dir "{company_dir}/sources/pdf"
```
- Already-held periods are skipped; add `--force` only when the user asks for a re-download.
- If the result is `PARTIAL` or `FAILED`, stop and tell the user which periods are missing before continuing.

### Step 1B: Refresh the market data pack (before opening the run)
```bash
python3 scripts/tushare_collector.py --code "{ticker}" --output "{company_dir}/data_pack_market.md"
```
Do not skip this: `period_delta` derives every 同比 / 单季 figure from the pack's current-period columns. Without it the run snapshots the previous period's numbers and those metrics silently degrade to `null` / `无法计算`.

### Step 2: Parse report sections per period
```bash
python3 scripts/pdf_preprocessor.py \
  --pdf "{company_dir}/sources/pdf/<period report>.pdf" \
  --period "<period>" \
  --output "{company_dir}/sources/pdf/pdf_sections_{period}.json"
```
`--output` must be explicit: the tool's default is `pdf_sections_{period}.json` **next to the PDF**, and `runs.py new` snapshots inputs by basename, so this path must match Step 3 exactly. Extract footnotes into `data_pack_report.md` as described in `coordinator_v2.md` Step 1C when the period discloses them (quarterly reports usually do not).

### Step 3: Open a new run
```bash
python3 scripts/runs.py new --company-dir "{company_dir}" --ticker "{ticker}" --company "{company_name}" --market "{market}" --kind report-update --primary-period "<period>" --supersedes "<previous run_id>" \
  --input "{company_dir}/data_pack_market.md" \
  --input "{company_dir}/sources/pdf/<period report>.pdf" \
  --input "{company_dir}/sources/pdf/pdf_sections_{period}.json" \
  --input "{company_dir}/data_pack_report.md"
```
Capture the printed run directory as `{run_dir}`. Keep the previous run untouched.

`--input` 一个都不能少：附注源是 `data_pack_report.md`（中报用 `data_pack_report_interim.md`）；
本期确实不披露附注时删掉那一行即可——`prepare` 会给出显式 warning 并把
`pdf_footnotes` 登记成 `not_applicable`（`run_manifest.unavailable_inputs`），
不要让它静默变成模块里的一个 `missing`（REQ-006.2 AC-2.6）。

### Step 4: Prepare evidence and module contexts
```bash
python3 -m scripts.results.prepare --output-dir "{run_dir}" --ticker "{ticker}" --company "{company_name}" --market "{market}" --primary-period "<period>" --prior-analysis "{company_dir}/runs/<previous run_id>/synthesis/result.json"
```
`--prior-analysis` registers the previous run's conclusions as the `prior_analysis` evidence source so `period_delta` can cite what changed.

### Step 5: Run the modules
Run the four core modules **and** `period_delta` in parallel, each reading only its own context bundle and writing `modules/{module}/result.json` + `report.md`:
- `shared/qualitative/agents/modules/business_moat.md`
- `shared/qualitative/agents/modules/environment.md`
- `shared/qualitative/agents/modules/governance.md`
- `shared/qualitative/agents/modules/mda_quality.md`
- `shared/qualitative/agents/modules/period_delta.md`
- `shared/qualitative/agents/modules/holding_structure.md` (only when `d6_trigger.json` requires it)

Validate each with `python3 -m scripts.results.validate_result "{run_dir}/modules/{module}/result.json" --evidence-index "{run_dir}/evidence/index.json"`. Do not invent evidence IDs.

### Step 6: Reconcile and synthesize
Pass the same module set (four core + `period_delta` + optional `holding_structure`) to both:
```bash
python3 -m scripts.results.reconcile_results ... \
  --prior-input "{company_dir}/runs/<previous run_id>/modules/business_moat/result.json" \
  --prior-input "{company_dir}/runs/<previous run_id>/modules/environment/result.json" \
  --prior-input "{company_dir}/runs/<previous run_id>/modules/governance/result.json" \
  --prior-input "{company_dir}/runs/<previous run_id>/modules/mda_quality/result.json" \
  --prior-input "{company_dir}/runs/<previous run_id>/modules/period_delta/result.json" \
  --output "{run_dir}/synthesis/reconciliation.json"
python3 -m scripts.results.synthesis ... --output "{run_dir}/synthesis/context.json"
```
Then run the Final Synthesis Agent (`shared/qualitative/agents/final_synthesis.md`) to write the updated `{run_dir}/qualitative_report.md` and `{run_dir}/synthesis/result.json`, including a short 「本次更新说明」 section.

### Step 7: Validate the updated conclusions
```bash
python3 -m scripts.results.resolve_qualitative --output-dir "{run_dir}" --ticker "{ticker}" --output "{run_dir}/qualitative_input.json"
```
Require `source=structured`. Exit status 3 means the run must be fixed or re-run; never fall back to the old Markdown in a manifest-backed run.

### Step 8: Build and write the standalone change report
```bash
python3 -m scripts.results.change_report --delta "{run_dir}/modules/period_delta/result.json" --synthesis "{run_dir}/synthesis/result.json" --prior-synthesis "{company_dir}/runs/<previous run_id>/synthesis/result.json" --evidence-index "{run_dir}/evidence/index.json" --reconciliation "{run_dir}/synthesis/reconciliation.json" --output "{run_dir}/synthesis/change_report_context.json"
```
Run the Change Report Agent (`shared/qualitative/agents/change_report.md`) to write `{run_dir}/change_report_{period}.md` and `change_report_{period}.json`.

### Step 9: Record the iteration
```bash
python3 scripts/runs.py finish --company-dir "{company_dir}" --run-dir "{run_dir}" --status complete --primary-period "<period>" --report-periods "<covered periods>" --artifact report="{run_dir}/qualitative_report.md" --artifact change_report="{run_dir}/change_report_<period>.md" --artifact qualitative_input="{run_dir}/qualitative_input.json" --conclusion-changed "<one line>"
```

### Step 10: Downstream freshness
- `runs.py finish` marks `record.json`'s downstream components stale when they were built on an older period; `analysis_status` then reports `stale:downstream_stale` (exit 1) until they are refreshed.
- **Do not** regenerate or alter a buy/sell plan automatically. Tell the user to run `/value-analysis` and, if they want an executable plan, `/buy-sell-plan`.
- After those reruns, clear the flags so the company returns to `up_to_date`:
  ```bash
  python3 scripts/runs.py downstream --company-dir "{company_dir}" --fresh value_computed
  # or, once the buy/sell plan is refreshed too:
  python3 scripts/runs.py downstream --company-dir "{company_dir}" --fresh all
  ```

## Method Requirements
- Compare like with like: `Q1` / `H1` / `Q3` are year-to-date cumulative; derive single quarters by subtraction and state it. Never compare an interim period against a full year.
- Every asserted change cites evidence: this period's data/PDF for the new fact, `prior_analysis:*` for the previous conclusion.
- Missing sections (typical in quarterly reports) are written as `本期未披露`, not treated as proof that nothing happened.
- Preserve deterministic numbers; never recalculate or invent them.
- If `period_delta` reports `requires_full_rerun=true`, say so prominently and name the conclusion that can no longer be carried forward.

## Error Recovery
- No analysis record → `/business-analysis` first; do not improvise a baseline.
- Newest period not published or not downloadable → stop and report which period is missing; offer degraded mode only with the user's consent.
- `prepare`/`resolve_qualitative` failure → fix inputs and re-run the module set; never mix results from two runs.
- `synthesis` failure → keep the run directory; it is resumable, and the ledger entry is written only by `runs.py finish`.

## Output
- Updated conclusions: `{run_dir}/qualitative_report.md` (+ `qualitative_input.json`)
- Standalone change report: `{run_dir}/change_report_{period}.md` (+ `.json`)
- Ledger: `{company_dir}/history.jsonl`, `latest.json`, `record.json`
- Downstream freshness note for `/value-analysis` and `/buy-sell-plan`

Usage: /update-analysis 600887 or /update-analysis 000858 2026H1
