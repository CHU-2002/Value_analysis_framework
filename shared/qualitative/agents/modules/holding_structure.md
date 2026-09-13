# Module Agent: Holding Structure

Follow `../module_output_contract.md`.

## Scope

- D6: whether holding-structure analysis applies, subsidiary and associate structure, parent-company asset concentration, SOTP relevance and holding discount

## Method

Read `contexts/holding_structure.json`. This module is conditional. Use its embedded `routing` object. If `routing.status=not_required`, write a valid `not_applicable` result with `holding_structure=false`, null SOTP fields and a concise reason. If `routing.status=required`, verify that the structure is genuinely an investment-holding case rather than an ordinary operating subsidiary tree. If `routing.status=unknown`, do not treat D6 as inapplicable; return `partial` and record the missing input.

Do not invent subsidiary valuations. Any SOTP estimate must identify the supplied inputs and cite their evidence IDs.

## Required parameters

```text
holding_structure, sotp_value_mm, sotp_discount_pct
```

Write `modules/holding_structure/report.md` and `modules/holding_structure/result.json`.
