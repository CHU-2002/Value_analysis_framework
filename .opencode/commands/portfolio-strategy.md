# /portfolio-strategy - 综合资产配置策略 v2.0

## Usage
```
/portfolio-strategy [user profile description]
```

## Execution

Treat `$ARGUMENTS` as the user profile plus an optional `--full` or `--incremental` mode. If it is empty, ask for total investable assets and age before proceeding; never substitute an example profile.

Read `strategies/portfolio/coordinator.md` and execute the complete pipeline. Build the profile, macro assessment, seven-class strategic allocation, tactical selection, risk checks, and final report.

When scanning each `output/{code}_{company}` directory, run:

```bash
.venv/bin/python -m scripts.results.resolve_qualitative --output-dir "{company_output_dir}" --ticker "{ticker}" --output "{company_output_dir}/qualitative_input.json"
```

Exit status 3 means the company analysis is unavailable and should be marked for research; status 2 is an invocation error and must be fixed. Prefer module-owned parameters when `source=structured`. Parse `qualitative_report.md` only when the resolver selects `source=legacy`. Value defensive ratings still come from the value-analysis report and must not be inferred from qualitative modules.

Options: `--full` rebuilds the allocation; `--incremental {existing_portfolio_dir}` requires and preserves the named existing portfolio as the source configuration while refreshing market data and holdings.

For portfolio precomputation, `profile.md` must contain the seven-key `组合引擎基准权重` table defined in `strategies/portfolio/references/portfolio_schema.md`. Run `portfolio_engine.py --mode full --profile ...` so benchmark, frontier, risk parity, scenarios, and correlations are all produced.

## Prerequisites

- Existing company analyses are optional; mark missing research and assign priorities.
- Never recommend a company solely from qualitative results.
- Governance or integrity warnings and unresolved reconciliation conflicts lower recommendation confidence.

## Output

Write the pipeline artifacts and final report under `output/portfolio_{timestamp}/`.
