# Module Agent: Management and Governance

Follow `../module_output_contract.md`.

## Scope

- D4: audit opinion, internal control, major matters, related-party transactions, litigation, guarantees, pledges, shareholder signals, management capability and capital allocation integrity

## Required annual-report checks

Use the supplied evidence to explicitly check the following. If a check is not covered by the input, write `年报中未检索到相关信息` in `unresolved_questions` or `warnings`; do not convert absence of evidence into evidence of absence.

- mergers, restructuring or major asset purchases in 第五节 重要事项;
- non-routine related-party transactions;
- major lawsuits or contingent liabilities;
- top shareholder, pledge or disposal signals;
- audit opinion and auditor changes;
- buyback, dividend and major investment commitments.

## Required parameters

```text
governance_flags, management_rating, integrity_rating, promise_delivery,
valuation_confidence_impact, capital_allocation_record, related_party_risk
```

Governance risk must be able to reduce confidence in a strong business or moat judgement. Write `modules/governance/report.md` and `modules/governance/result.json`.
