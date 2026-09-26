# Module Agent Output Contract

All qualitative module Agents follow this contract. The contract is intentionally short so it can be loaded by every Agent without becoming a new source of context bloat.

## Input

Read only:

1. `contexts/{module}.json` for the bounded input.
2. The module-specific prompt.
3. The relevant sections of `references/output_schema.md` and `agents/writing_style.md`.

Do not read the complete annual report, the complete `data_pack_market.md`, another module's report, or historical output directories unless the coordinator explicitly requests a targeted evidence lookup.

The context bundle contains canonical `run`, `subject`, bounded `context_text`, and `evidence` objects with exact excerpts. Copy `run.run_id` and `subject` exactly. Set your own `run.generated_at`, evidence-supported `run.as_of`, and result `run.status`; never copy the context's `prepared` status into a result. Copy the context `input_digest` when present; `reconcile_results --prior-input` uses it to detect non-deterministic judgement changes when the same input is processed twice. Use only the supplied evidence identifiers; do not invent identifiers.

Check `selection.evidence_coverage`, `selection.missing_pdf_sections`, and section truncation metadata. A coverage value is the selected evidence ID, `missing` (no indexed entry for that source/section), or `omitted` (available but outside this bundle's budget). A selected entry establishes excerpt coverage, not completeness of the section or factual verification. For a material gap, request a bounded lookup by source/section from the coordinator or record it in `quality.missing_inputs` and use `partial`. Missing/truncated guarantees, litigation or integrity records do not establish absence of risk or an adverse finding.

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
    "status": "complete",
    "input_digest": "{copy from context when present}"
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
6. Keep `result.json` compact. Put the primary investment judgement first in `claims` and order `risks` by materiality with explicit severity. Cite known `evidence_ids` in risks and metrics as well as claims, and include those precise excerpts in `evidence`. Keep units, dates and measurement basis with the corresponding metric. The detailed human-readable explanation belongs in `report.md`.
7. Run `python3 -m scripts.results.validate_result "{result_path}" --evidence-index "{output_dir}/evidence/index.json"` after writing the file. Fix schema, identity and excerpt validation errors before reporting completion.
8. A `qualitative.synthesis` result must copy `upstream_digest` from `synthesis/context.json`; module results do not set this field.
9. For a `complete` module result, include every required parameter listed by the module prompt. A `partial` result may omit unavailable parameters but must explain them in `quality.missing_inputs`.
10. Evidence objects must copy the indexed `source_id` and `locator`; `quote` must be one exact, continuous excerpt of the supplied indexed text, at most 300 characters. Do not paraphrase, normalize whitespace, insert ellipses, or join distant sentences. Separately check that the excerpt supports the attached claim, including its amount, unit and date; a matching ID or quote alone does not establish semantic support.
