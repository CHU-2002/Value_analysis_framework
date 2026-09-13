# Module Agent: Business Model and Moat

Follow `../module_output_contract.md`.

## Scope

- D1: business model, capital intensity, collection mode and cash impact
- D2: industry structure, barriers, quantitative moat validation, moat source, competitors and sustainability

## Method

Read `contexts/business_moat.json`. Use only the supplied Tushare sections, PDF evidence and evidence IDs. Apply the D1/D2 parts of `qualitative_assessment_v2.md`, the judgement anchors, and the framework guide.

Distinguish:

- observed data such as ROE, gross margin, segment mix and cash-flow relationships;
- inference such as the likely source of pricing power;
- judgement such as moat rating and sustainability.

Do not treat high ROE by itself as proof of a moat. Check durability, reinvestment needs, cash conversion, industry structure and competitor comparison.

## Required parameters

Populate the applicable fields from `output_schema.md`:

```text
business_model_clarity, capital_intensity, collection_mode, cash_impact,
market_structure, market_cr4, entry_barrier, roe_5y_avg,
moat_existence, moat_evidence_strength, moat_type, moat_framework_primary,
supply_side_rating, demand_side_rating, scale_economy_rating, moat_flywheel,
false_advantages, competitors, competitor_ranking,
advantage_gap_sustainability, pricing_power, human_capital_dep,
moat_sustainability, moat_rating, moat_monitor_kpis
```

Write a concise module explanation to `modules/business_moat/report.md` and the validated machine result to `modules/business_moat/result.json`.
