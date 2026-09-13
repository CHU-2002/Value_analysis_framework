# Module Agent: External Environment

Follow `../module_output_contract.md`.

## Scope

- D3: cyclicality, cycle position, regulatory risk, policy exposure, external dependencies and industry monitoring keywords

## Method

Read `contexts/environment.json` and the D3 part of `qualitative_assessment_v2.md`. Base the conclusion on the supplied historical series, industry evidence and MD&A excerpts. Explain the transmission mechanism from macro or industry variables to revenue, margin, cash flow or valuation.

Do not claim a current cycle position when the supplied data does not identify a complete cycle. Use `unknown` or a warning instead.

## Required parameters

```text
cyclicality, cycle_position, regulatory_risk, industry_keywords
```

Write `modules/environment/report.md` and `modules/environment/result.json`. Important judgements must cite evidence IDs.
