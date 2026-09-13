#!/usr/bin/env python3
"""Build the bounded input bundle consumed by the final synthesis Agent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .evidence import select_evidence, validate_result_evidence
from .schema import compact_result, load_result, require_consistent_result_set, result_set_digest


def _shorten(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars].rstrip() + "..."
    if isinstance(value, list):
        return [_shorten(item, max_chars) for item in value]
    if isinstance(value, dict):
        return {key: _shorten(item, max_chars) for key, item in value.items()}
    return value


def _result_card(result: dict[str, Any], *, max_claims: int, max_evidence: int) -> dict[str, Any]:
    card = compact_result(
        result,
        max_claims=max_claims,
        max_evidence=max_evidence,
        max_risks=6,
        max_watchlist=6,
    )
    card["summary"] = _shorten(card.get("summary", {}), 800)
    card["claims"] = _shorten(card.get("claims", []), 500)
    card["risks"] = _shorten(card.get("risks", []), 400)
    card["watchlist"] = _shorten(card.get("watchlist", []), 300)
    # Evidence prose is supplied once below from the shared index.
    card["evidence"] = [
        {
            "evidence_id": item.get("evidence_id"),
            "source_id": item.get("source_id"),
            "locator": item.get("locator", {}),
        }
        for item in card.get("evidence", [])
        if isinstance(item, dict)
    ]
    return card


def _referenced_evidence(result: dict[str, Any]) -> list[str]:
    references: list[str] = []
    for claim in result.get("claims", []):
        if isinstance(claim, dict):
            references.extend(
                reference
                for reference in claim.get("evidence_ids", [])
                if isinstance(reference, str)
            )
    for evidence in result.get("evidence", []):
        if isinstance(evidence, dict) and isinstance(evidence.get("evidence_id"), str):
            references.append(evidence["evidence_id"])
    return list(dict.fromkeys(references))


def _require_artifact_identity(
    artifact: dict[str, Any],
    *,
    schema: str,
    run_id: str,
    subject: dict[str, Any],
) -> None:
    if not isinstance(artifact, dict) or artifact.get("schema") != schema:
        raise ValueError(f"Expected {schema} artifact")
    if not isinstance(artifact.get("run"), dict) or artifact["run"].get("run_id") != run_id:
        raise ValueError(f"{schema} run_id does not match module results")
    if artifact.get("subject") != subject:
        raise ValueError(f"{schema} subject does not match module results")


def build_synthesis_context(
    result_paths: Iterable[str | Path],
    *,
    optional_result_paths: Iterable[str | Path] = (),
    reconciliation_path: str | Path,
    evidence_index_path: str | Path,
    max_chars: int = 30000,
) -> dict[str, Any]:
    """Create a compact, valid JSON handoff for Final Synthesis Agent."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    optional_result_paths = list(optional_result_paths)
    missing_modules = [str(path) for path in optional_result_paths if not Path(path).exists()]
    results = [load_result(path) for path in result_paths]
    results.extend(
        load_result(path)
        for path in optional_result_paths
        if Path(path).exists()
    )
    results = require_consistent_result_set(results)
    reconciliation = json.loads(Path(reconciliation_path).read_text(encoding="utf-8"))
    evidence_index = json.loads(Path(evidence_index_path).read_text(encoding="utf-8"))
    run_id = results[0]["run"]["run_id"]
    subject = results[0]["subject"]
    upstream_digest = result_set_digest(results)
    _require_artifact_identity(
        reconciliation,
        schema="investment.reconciliation",
        run_id=run_id,
        subject=subject,
    )
    if reconciliation.get("result_digest") != upstream_digest:
        raise ValueError("investment.reconciliation result_digest does not match module results")
    _require_artifact_identity(
        evidence_index,
        schema="investment.evidence_index",
        run_id=run_id,
        subject=subject,
    )
    evidence_errors = [
        f"{result['result_type']}: {error}"
        for result in results
        for error in validate_result_evidence(result, evidence_index)
    ]
    if evidence_errors:
        raise ValueError("Invalid module evidence:\n- " + "\n- ".join(evidence_errors))

    def build_payload(max_claims: int, max_evidence: int) -> dict[str, Any]:
        cards = [
            _result_card(result, max_claims=max_claims, max_evidence=max_evidence)
            for result in results
        ]
        evidence_ids = [
            evidence_id
            for card in cards
            for evidence_id in _referenced_evidence(card)
        ]
        selected = select_evidence(
            evidence_index,
            evidence_ids=evidence_ids,
            limit=max_evidence * max(1, len(results)),
        )
        selected = [
            {
                "evidence_id": item.get("evidence_id"),
                "source_id": item.get("source_id"),
                "locator": item.get("locator", {}),
                "quote": item.get("quote", "")[:300],
            }
            for item in selected
        ]
        return {
            "schema": "investment.synthesis_context",
            "schema_version": "1.0",
            "run": {"run_id": run_id},
            "subject": subject,
            "upstream_digest": upstream_digest,
            "modules": cards,
            "reconciliation": _shorten(reconciliation, 1000),
            "evidence": selected,
            "missing_modules": missing_modules,
            "instructions": {
                "must_rewrite": True,
                "must_address_conflicts": True,
                "must_cite_evidence": True,
                "source_of_truth": "module results plus reconciliation; numerical metrics remain deterministic",
            },
        }

    for max_evidence in (12, 8, 6, 4, 2):
        for max_claims in (8, 6, 4, 2):
            payload = build_payload(max_claims, max_evidence)
            payload["budget"] = {
                "max_chars": max_chars,
                "actual_chars": 0,
                "module_count": len(results),
                "evidence_count": len(payload["evidence"]),
            }
            while True:
                actual_chars = len(json.dumps(payload, ensure_ascii=False, indent=2)) + 1
                if payload["budget"]["actual_chars"] == actual_chars:
                    break
                payload["budget"]["actual_chars"] = actual_chars
            if actual_chars <= max_chars:
                return payload

    # Prompt contracts should normally make this path unreachable. Keep the
    # failure explicit rather than emitting invalid JSON or silently slicing it.
    raise ValueError(f"Unable to fit synthesis context within {max_chars} characters")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a bounded final synthesis context")
    parser.add_argument("--input", action="append", required=True, help="module result.json")
    parser.add_argument("--optional-input", action="append", default=[], help="optional module result.json")
    parser.add_argument("--reconciliation", required=True)
    parser.add_argument("--evidence-index", required=True)
    parser.add_argument("--max-chars", type=int, default=30000)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = build_synthesis_context(
        args.input,
        optional_result_paths=args.optional_input,
        reconciliation_path=args.reconciliation,
        evidence_index_path=args.evidence_index,
        max_chars=args.max_chars,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Synthesis context written: {args.output} ({payload['budget']['actual_chars']} chars)")


if __name__ == "__main__":
    main()
