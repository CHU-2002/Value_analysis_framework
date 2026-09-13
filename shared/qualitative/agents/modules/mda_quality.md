# Module Agent: MD&A and Earnings Quality

Follow `../module_output_contract.md`.

## Scope

- D5: MD&A source quality, operating performance attribution, forward guidance, capital allocation intent, risk disclosure and consistency with income statement and cash flow

## Method

Read `contexts/mda_quality.json`. First identify the source and date of the MD&A material. Compare management's explanations with supplied revenue, profit, margin, cash-flow, dividend, buyback and derived-metric data. Separate management's stated explanation from your own verification.

If MD&A is missing or only from WebSearch, lower `mda_credibility` and record the limitation. Do not infer that an undisclosed item did not happen.

## Required parameters

```text
mda_credibility, mda_impact, mda_forward_guidance, distribution_signal
```

Write `modules/mda_quality/report.md` and `modules/mda_quality/result.json`.
