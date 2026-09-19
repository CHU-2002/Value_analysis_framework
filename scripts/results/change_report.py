#!/usr/bin/env python3
"""Build the bounded input bundle consumed by the change-report Agent.

The change report compares one incremental update run against the previous
recorded analysis. This module is deterministic scaffolding: it validates the
period-delta result, gathers the previous and current synthesis cards, checks
every cited excerpt against the same-run evidence index, and enforces a
character budget so the Agent reads bounded material.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .evidence import validate_result_evidence
from .schema import compact_result, load_result


CHANGE_REPORT_CONTEXT_SCHEMA = "investment.change_report_context"
CHANGE_REPORT_CONTEXT_VERSION = "1.0"


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, indent=2)) + 1


def _require_type(result: dict[str, Any], expected: str) -> None:
    if result.get("result_type") != expected:
        raise ValueError(
            f"expected a {expected!r} result, got {result.get('result_type')!r}"
        )


def _load_optional(path: str | Path | None, expected_type: str) -> dict[str, Any] | None:
    if path is None:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        return None
    result = load_result(candidate)
    _require_type(result, expected_type)
    return result


def _card(result: dict[str, Any] | None, *, max_claims: int, max_evidence: int) -> dict[str, Any] | None:
    if result is None:
        return None
    return compact_result(result, max_claims=max_claims, max_evidence=max_evidence)


def build_change_report_context(
    *,
    delta_result_path: str | Path,
    evidence_index_path: str | Path,
    synthesis_result_path: str | Path | None = None,
    prior_synthesis_path: str | Path | None = None,
    reconciliation_path: str | Path | None = None,
    max_chars: int = 24000,
) -> dict[str, Any]:
    """Create a compact, valid JSON handoff for the change-report Agent."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    delta = load_result(delta_result_path)
    _require_type(delta, "qualitative.period_delta")
    synthesis = _load_optional(synthesis_result_path, "qualitative.synthesis")
    prior = _load_optional(prior_synthesis_path, "qualitative.synthesis")

    evidence_index = json.loads(Path(evidence_index_path).read_text(encoding="utf-8"))
    if not isinstance(evidence_index, dict) or evidence_index.get("schema") != "investment.evidence_index":
        raise ValueError("evidence index has an invalid schema")

    run_id = delta["run"]["run_id"]
    subject = delta["subject"]
    if evidence_index.get("run", {}).get("run_id") != run_id:
        raise ValueError("evidence index run_id does not match the period-delta result")
    if evidence_index.get("subject") != subject:
        raise ValueError("evidence index subject does not match the period-delta result")

    for name, result in (("period_delta", delta), ("synthesis", synthesis), ("prior_synthesis", prior)):
        if result is None:
            continue
        errors = validate_result_evidence(result, evidence_index)
        if errors:
            raise ValueError(
                f"{name} cites evidence missing from the index:\n- " + "\n- ".join(errors)
            )

    reconciliation = None
    if reconciliation_path is not None:
        recon_path = Path(reconciliation_path)
        if recon_path.is_file():
            loaded = json.loads(recon_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("schema") == "investment.reconciliation":
                if loaded.get("run", {}).get("run_id") == run_id:
                    reconciliation = loaded

    parameters = delta.get("parameters", {})
    report_period = parameters.get("report_period", "")
    comparable_period = parameters.get("comparable_period", "")

    def build_payload(
        *,
        with_prior: bool,
        with_synthesis: bool,
        prior_claims: int,
        synthesis_claims: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": CHANGE_REPORT_CONTEXT_SCHEMA,
            "schema_version": CHANGE_REPORT_CONTEXT_VERSION,
            "run": {"run_id": run_id},
            "subject": subject,
            "report_period": report_period,
            "comparable_period": comparable_period,
            "period_delta": _card(delta, max_claims=12, max_evidence=16),
            "synthesis": (
                _card(synthesis, max_claims=synthesis_claims, max_evidence=8)
                if with_synthesis
                else None
            ),
            "prior_synthesis": (
                _card(prior, max_claims=prior_claims, max_evidence=10) if with_prior else None
            ),
            "reconciliation": reconciliation,
            "instructions": {
                "must_state_comparison_basis": True,
                "must_diff_previous_conclusions": True,
                "must_cite_evidence": True,
                "cumulative_vs_single_quarter": "Q1/H1/Q3 are year-to-date cumulative; derive single quarters by subtraction and state it.",
                "missing_section_wording": "本期未披露",
                "source_of_truth": "period_delta and the supplied deterministic metrics; never recalculate",
            },
        }
        payload["budget"] = {
            "max_chars": max_chars,
            "actual_chars": 0,
            "delta_claims": len(payload["period_delta"]["claims"]) if payload["period_delta"] else 0,
            "has_synthesis": payload["synthesis"] is not None,
            "has_prior_synthesis": payload["prior_synthesis"] is not None,
        }
        while True:
            actual_chars = _size(payload)
            if payload["budget"]["actual_chars"] == actual_chars:
                break
            payload["budget"]["actual_chars"] = actual_chars
        return payload

    # Degrade in a fixed, disclosed order so the bundle always fits: shrink the
    # previous analysis first, then the current synthesis, then drop each.
    attempts = [
        (True, True, 6, 6),
        (True, True, 3, 4),
        (True, True, 1, 2),
        (True, False, 1, 0),
        (False, False, 0, 0),
    ]
    payload = None
    for with_prior, with_synthesis, prior_claims, synthesis_claims in attempts:
        candidate = build_payload(
            with_prior=with_prior,
            with_synthesis=with_synthesis,
            prior_claims=prior_claims,
            synthesis_claims=synthesis_claims,
        )
        if candidate["budget"]["actual_chars"] <= max_chars:
            payload = candidate
            break
    if payload is None:
        raise ValueError(
            f"Unable to fit the change-report context within {max_chars} characters; "
            "the period-delta result alone exceeds the budget"
        )

    payload["degraded"] = {
        "prior_synthesis_dropped": prior is not None and payload["prior_synthesis"] is None,
        "synthesis_dropped": synthesis is not None and payload["synthesis"] is None,
    }
    return payload


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Build a bounded change-report context")
    parser.add_argument("--delta", required=True, help="period_delta result.json")
    parser.add_argument("--evidence-index", required=True)
    parser.add_argument("--synthesis", help="current run synthesis/result.json")
    parser.add_argument("--prior-synthesis", help="previous run synthesis/result.json")
    parser.add_argument("--reconciliation", help="current run synthesis/reconciliation.json")
    parser.add_argument("--max-chars", type=int, default=24000)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    payload = build_change_report_context(
        delta_result_path=args.delta,
        evidence_index_path=args.evidence_index,
        synthesis_result_path=args.synthesis,
        prior_synthesis_path=args.prior_synthesis,
        reconciliation_path=args.reconciliation,
        max_chars=args.max_chars,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Change-report context written: {args.output} "
        f"({payload['budget']['actual_chars']}/{args.max_chars} chars)"
    )


if __name__ == "__main__":
    main()
