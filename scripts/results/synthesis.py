#!/usr/bin/env python3
"""Build the bounded input bundle consumed by the final synthesis Agent."""

from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path
from typing import Any, Iterable

from .evidence import validate_result_evidence
from .schema import load_result, require_consistent_result_set, result_set_digest


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, indent=2)) + 1


def _referenced_evidence(value: Any) -> list[str]:
    references: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "evidence_ids" and isinstance(item, list):
                references.extend(ref for ref in item if isinstance(ref, str))
            else:
                references.extend(_referenced_evidence(item))
    elif isinstance(value, list):
        for item in value:
            references.extend(_referenced_evidence(item))
    return list(dict.fromkeys(references))


def _initial_card(result: dict[str, Any]) -> dict[str, Any]:
    # Keep the primary judgement and risk with their exact evidence even when
    # optional details are too large. Units/date/basis must accompany metrics.
    card = {key: result[key] for key in ("result_type", "run", "scope", "summary", "parameters")}
    high_risks = [item for item in result["risks"] if isinstance(item, dict) and item.get("severity") == "high"]
    card["risks"] = high_risks or result["risks"][:1]
    basis_keys = {"basis", "period_basis", "unit", "amount_unit", "currency", "period", "periods", "as_of", "period_end"}
    card["metrics"] = {key: value for key, value in result["metrics"].items() if key in basis_keys}
    card["claims"] = result["claims"][:1]
    card["watchlist"] = []
    quality = result["quality"]
    card["quality"] = {"completeness": quality["completeness"]}
    for key, value in quality.items():
        if isinstance(value, list):
            card["quality"][key] = value[:1] if value and _size(value[0]) <= 500 else []
    return card


def _candidates(result: dict[str, Any], card: dict[str, Any]):
    queues = []
    for key in ("metrics", "risks", "claims", "quality", "watchlist"):
        value = result[key]
        if key == "metrics":
            queues.append(deque((key, name, item) for name, item in value.items() if name not in card[key]))
        elif key == "quality":
            for name, items in value.items():
                if isinstance(items, list):
                    queues.append(deque((key, name, item) for item in items[len(card[key][name]):]))
                elif name not in card[key]:
                    queues.append(deque([(key, name, items)]))
        else:
            queues.append(deque((key, None, item) for item in value if item not in card[key]))
    while any(queues):
        for queue in queues:
            if queue:
                yield queue.popleft()


def _result_card(result: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any]:
    card = dict(selected)
    by_id = {item["evidence_id"]: item for item in result["evidence"]}
    references = _referenced_evidence(card)
    missing = [ref for ref in references if ref not in by_id]
    if missing:
        raise ValueError(f"{result['result_type']} references evidence without a module excerpt: {missing}")
    card["evidence"] = [by_id[ref] for ref in references]
    card["omitted"] = {
        key: len(result[key]) - len(card[key])
        for key in ("metrics", "claims", "risks", "watchlist", "evidence")
    }
    card["omitted"]["quality"] = {
        key: len(value) - len(card["quality"].get(key, [])) if isinstance(value, list) else int(key not in card["quality"])
        for key, value in result["quality"].items() if key != "completeness"
    }
    return card


def _shared_evidence(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shared: dict[str, dict[str, Any]] = {}
    for card in cards:
        references = []
        for item in card["evidence"]:
            evidence_id = item["evidence_id"]
            entry = shared.setdefault(evidence_id, {
                key: item[key]
                for key in ("evidence_id", "source_id", "locator", "quote", "content_hash") if key in item
            })
            quotes = [entry["quote"], *entry.get("alternative_quotes", [])]
            if item["quote"] not in quotes:
                entry.setdefault("alternative_quotes", []).append(item["quote"])
                quotes.append(item["quote"])
            references.append({"evidence_id": evidence_id, "quote_index": quotes.index(item["quote"])})
        card["evidence"] = references
    return list(shared.values())


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
    present_paths = [*result_paths, *(path for path in optional_result_paths if Path(path).exists())]
    results = [load_result(path) for path in present_paths]
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

    selections = [_initial_card(result) for result in results]
    for card, path in zip(selections, present_paths):
        card["result_path"] = str(path)

    def build_payload() -> dict[str, Any]:
        cards = [
            _result_card(result, selected)
            for result, selected in zip(results, selections)
        ]
        selected = _shared_evidence(cards)
        payload = {
            "schema": "investment.synthesis_context",
            "schema_version": "1.0",
            "run": {"run_id": run_id},
            "subject": subject,
            "upstream_digest": upstream_digest,
            "modules": cards,
            "reconciliation": reconciliation,
            "evidence": selected,
            "missing_modules": missing_modules,
            "instructions": {
                "must_rewrite": True,
                "must_address_conflicts": True,
                "must_cite_evidence": True,
                "omissions_require_targeted_lookup": True,
                "quote_index": "0 = quote; n > 0 = alternative_quotes[n - 1]. Copy one continuous excerpt; never concatenate alternatives.",
                "source_of_truth": "module results plus reconciliation; numerical metrics remain deterministic",
            },
        }
        payload["budget"] = {
            "max_chars": max_chars,
            "actual_chars": 0,
            "module_count": len(results),
            "evidence_count": len(payload["evidence"]),
        }
        while True:
            actual_chars = _size(payload)
            if payload["budget"]["actual_chars"] == actual_chars:
                break
            payload["budget"]["actual_chars"] = actual_chars
        return payload

    payload = build_payload()
    if payload["budget"]["actual_chars"] > max_chars:
        raise ValueError(
            f"Unable to fit protected synthesis content within {max_chars} characters "
            f"(requires {payload['budget']['actual_chars']}); parameters, primary claims, "
            "key risks, metric basis and reconciliation cannot be discarded"
        )
    # Fair round-robin admission measures the complete JSON including shared
    # excerpts and omission metadata. Large entries cannot block small ones.
    candidates = [deque(_candidates(result, card)) for result, card in zip(results, selections)]
    while any(candidates):
        for card, queue in zip(selections, candidates):
            if not queue:
                continue
            key, name, item = queue.popleft()
            target = card[key]
            is_list = key not in {"metrics", "quality"} or (
                key == "quality" and isinstance(target.get(name), list)
            )
            if key == "quality" and is_list:
                target = target[name]
            if is_list:
                target.append(item)
            else:
                target[name] = item
            trial = build_payload()
            if trial["budget"]["actual_chars"] <= max_chars:
                payload = trial
            elif is_list:
                target.pop()
            else:
                del target[name]
    return payload


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
