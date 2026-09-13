# Module Agent Output Contract

All qualitative module Agents follow this contract. The contract is intentionally short so it can be loaded by every Agent without becoming a new source of context bloat.

## Input

Read only:

1. `contexts/{module}.json` for the bounded input.
2. The module-specific prompt.
3. The relevant sections of `references/output_schema.md` and `agents/writing_style.md`.

Do not read the complete annual report, the complete `data_pack_market.md`, another module's report, or historical output directories unless the coordinator explicitly requests a targeted evidence lookup.

The context bundle contains canonical `run`, `subject`, bounded `context_text`, and evidence identifiers. Copy `run.run_id` and `subject` exactly. Set your own `run.generated_at`, evidence-supported `run.as_of`, and result `run.status`; never copy the context's `prepared` status into a result. Use only the supplied evidence identifiers; do not invent identifiers.

## Required JSON output

Write valid JSON, not a Markdown code fence, to the requested `result.json` path:

```json
{
  "schema": "investment.result",
  "schema_version": "1.0",
  "result_type": "qualitative.{module}",
  "run": {
    "run_id": "{run_id}",
    "generated_at": "{ISO-8601 timestamp}",
    "as_of": "{latest supported date}",
    "status": "complete"
  },
  "subject": {
    "ticker": "{ticker}",
    "company": "{company}",
    "market": "{market}"
  },
  "scope": ["{dimension}"],
  "summary": {
    "thesis": "No more than 120 Chinese characters.",
    "decision_relevance": "No more than 180 Chinese characters.",
    "confidence": "high | medium | low | unknown"
  },
  "parameters": {},
  "metrics": {},
  "claims": [
    {
      "claim_id": "{module}-001",
      "statement": "A concise judgement.",
      "type": "fact | inference | judgement",
      "confidence": "high | medium | low | unknown",
      "evidence_ids": ["{evidence_id}"]
    }
  ],
  "risks": [
    {
      "risk": "A concrete risk",
      "severity": "high | medium | low",
      "monitoring_indicator": "A measurable indicator"
    }
  ],
  "watchlist": ["A future monitoring item"],
  "evidence": [
    {
      "evidence_id": "{evidence_id}",
      "source_id": "{source_id}",
      "locator": {"path": "...", "section": "...", "chunk": 1},
      "quote": "At most 300 Chinese characters."
    }
  ],
  "quality": {
    "completeness": 0.0,
    "missing_inputs": [],
    "warnings": [],
    "unresolved_questions": []
  }
}
```

## Rules

1. Every non-factual claim must cite one or more evidence IDs.
2. Facts, calculations and judgements must be distinguishable in `claims.type`.
3. Numeric metrics must come from the supplied data; do not estimate missing values.
4. Use `null` only where the parameter schema explicitly permits it, with the reason in `quality.warnings`. Omit unavailable non-nullable parameters and use `partial` rather than inventing a value.
5. If input is incomplete, use `partial`, `failed`, or `stale` rather than filling gaps with assumptions. Reserve `not_applicable` for a verified inapplicable D6, with `holding_structure=false` and null SOTP fields; missing data is not proof of inapplicability.
6. Keep `result.json` compact. The detailed human-readable explanation belongs in `report.md`.
7. Run `python3 -m scripts.results.validate_result {result_path}` after writing the file. Fix validation errors before reporting completion.
8. A `qualitative.synthesis` result must copy `upstream_digest` from `synthesis/context.json`; module results do not set this field.
9. For a `complete` module result, include every required parameter listed by the module prompt. A `partial` result may omit unavailable parameters but must explain them in `quality.missing_inputs`.
10. Evidence objects must copy the indexed `source_id` and `locator`; `quote` must be an exact excerpt of the supplied indexed text.
