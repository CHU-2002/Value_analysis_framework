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


def _load_optional(
    path: str | Path | None,
    expected_type: str,
    *,
    strict: bool = True,
    missing: list[str] | None = None,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Load an optional result, recording (or raising) when it is unusable.

    ``strict=False`` downgrades a missing/wrong-typed/malformed file into a
    recorded diagnostic instead of failing the whole change report.
    """

    if path is None:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        if strict:
            raise ValueError(f"file not found: {candidate}")
        if missing is not None:
            missing.append(str(candidate))
        return None
    try:
        result = load_result(candidate)
        _require_type(result, expected_type)
        return result
    except ValueError as exc:
        if strict:
            raise
        if errors is not None:
            errors.append({"path": str(candidate), "error": str(exc)[:300]})
        return None


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
    max_chars: int = 160000,
) -> dict[str, Any]:
    """Create a compact, valid JSON handoff for the change-report Agent.

    默认 160000 字符来自一次真实实跑（REQ-006.1）：period_delta 单卡就要 26,141 字符，
    原来 24,000 的默认值直接失败；40k–80k 区间会在不吭声的情况下丢掉本期 synthesis 卡片，
    实测要 ~150k 才能「本期 + 上次」两卡俱全。
    """

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    delta = load_result(delta_result_path)
    _require_type(delta, "qualitative.period_delta")
    missing_inputs: list[str] = []
    load_errors: list[dict[str, str]] = []
    # This run's own synthesis is authoritative: a broken one is an error.
    synthesis = _load_optional(synthesis_result_path, "qualitative.synthesis")
    # The previous run's synthesis is advisory: a missing or stale one must
    # degrade the report, not make the update impossible.
    prior = _load_optional(
        prior_synthesis_path,
        "qualitative.synthesis",
        strict=False,
        missing=missing_inputs,
        errors=load_errors,
    )

    evidence_index = json.loads(Path(evidence_index_path).read_text(encoding="utf-8"))
    if not isinstance(evidence_index, dict) or evidence_index.get("schema") != "investment.evidence_index":
        raise ValueError("evidence index has an invalid schema")

    run_id = delta["run"]["run_id"]
    subject = delta["subject"]
    if evidence_index.get("run", {}).get("run_id") != run_id:
        raise ValueError("evidence index run_id does not match the period-delta result")
    if evidence_index.get("subject") != subject:
        raise ValueError("evidence index subject does not match the period-delta result")

    # This run's own results must be fully verifiable against this run's index.
    # The previous run's synthesis belongs to an older run: its locators point
    # at the old snapshot, so it is downgraded rather than rejected (the whole
    # point of an update is that the inputs changed).
    for name, result in (("period_delta", delta), ("synthesis", synthesis)):
        if result is None:
            continue
        errors = validate_result_evidence(result, evidence_index)
        if errors:
            raise ValueError(
                f"{name} cites evidence missing from the index:\n- " + "\n- ".join(errors)
            )

    indexed_ids = {
        entry.get("evidence_id")
        for entry in evidence_index.get("entries", [])
        if isinstance(entry, dict)
    }
    prior_evidence_unavailable = 0
    if prior is not None:
        verifiable: list[dict[str, Any]] = []
        dropped_ids: set[Any] = set()
        for item in prior.get("evidence", []):
            if not isinstance(item, dict):
                continue
            # Reuse the same excerpt/locator rules as this run: an id that exists
            # in both runs may still point at a different chunk or snapshot.
            probe = dict(prior)
            probe["evidence"] = [item]
            if validate_result_evidence(probe, evidence_index):
                prior_evidence_unavailable += 1
                dropped_ids.add(item.get("evidence_id"))
            else:
                verifiable.append(item)
        verifiable_ids = {item.get("evidence_id") for item in verifiable}
        for claim in prior.get("claims", []):
            if not isinstance(claim, dict):
                continue
            references = claim.get("evidence_ids", [])
            if not isinstance(references, list):
                continue
            unavailable = [ref for ref in references if ref not in verifiable_ids]
            if unavailable:
                claim["evidence_status"] = (
                    "partially_unavailable" if any(ref in verifiable_ids for ref in references) else "unavailable"
                )
                claim["unavailable_evidence_ids"] = unavailable
            claim["evidence_ids"] = [
                ref
                for ref in references
                if ref in verifiable_ids or (ref in indexed_ids and ref not in dropped_ids)
            ]
        prior["evidence"] = verifiable

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
        synthesis_card = (
            _card(synthesis, max_claims=synthesis_claims, max_evidence=8)
            if with_synthesis
            else None
        )
        prior_card = (
            _card(prior, max_claims=prior_claims, max_evidence=10) if with_prior else None
        )
        payload: dict[str, Any] = {
            "schema": CHANGE_REPORT_CONTEXT_SCHEMA,
            "schema_version": CHANGE_REPORT_CONTEXT_VERSION,
            "run": {"run_id": run_id},
            "subject": subject,
            "report_period": report_period,
            "comparable_period": comparable_period,
            "period_delta": _card(delta, max_claims=12, max_evidence=16),
            "synthesis": synthesis_card,
            "prior_synthesis": prior_card,
            "reconciliation": reconciliation,
            # Part of the written JSON, so it is measured by the budget below.
            "degraded": {
                "prior_synthesis_dropped": prior is not None and prior_card is None,
                "synthesis_dropped": synthesis is not None and synthesis_card is None,
                # 卡片被留下但结论被截断时，也要看得出来（实跑实测：40k–80k 区间会悄悄少卡）
                "prior_synthesis_claims": prior_claims if prior_card is not None else 0,
                "synthesis_claims": synthesis_claims if synthesis_card is not None else 0,
                "prior_evidence_unavailable": (
                    0 if prior_card is None else prior_evidence_unavailable
                ),
                "missing_inputs": list(missing_inputs),
                "unusable_inputs": list(load_errors),
            },
            "instructions": {
                "must_state_comparison_basis": True,
                "must_diff_previous_conclusions": True,
                "must_cite_evidence": True,
                "cumulative_vs_single_quarter": "Q1/H1/Q3 are year-to-date cumulative; derive single quarters by subtraction and state it. When the input lacks the required prior cumulative period, write 无法计算 instead of inventing a quarter.",
                "missing_section_wording": "本期未披露",
                "cite_prior_via": "Cite previous conclusions through prior_analysis:* evidence ids; the previous run's own evidence ids belong to that run's index and may be unavailable here.",
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
        (True, True, 6, 6),   # 第一个组合 = 全卡片；选到它之后的组合都算降级，会记进 budget["dropped"]
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

    return payload


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Build a bounded change-report context")
    parser.add_argument("--delta", required=True, help="period_delta result.json")
    parser.add_argument("--evidence-index", required=True)
    parser.add_argument("--synthesis", help="current run synthesis/result.json")
    parser.add_argument("--prior-synthesis", help="previous run synthesis/result.json")
    parser.add_argument("--reconciliation", help="current run synthesis/reconciliation.json")
    parser.add_argument("--max-chars", type=int, default=160000)
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
    degraded = payload.get("degraded") or {}
    losses: list[str] = []
    if degraded.get("prior_synthesis_dropped"):
        losses.append("prior_synthesis 整卡丢弃")
    elif degraded.get("prior_synthesis_claims", 6) < 6:
        losses.append(f"prior_synthesis 结论条数 -> {degraded['prior_synthesis_claims']}")
    if degraded.get("synthesis_dropped"):
        losses.append("synthesis 整卡丢弃")
    elif degraded.get("synthesis_claims", 6) < 6:
        losses.append(f"synthesis 结论条数 -> {degraded['synthesis_claims']}")
    if losses:
        print("预算不足，已降级：" + "；".join(losses) + "；需要完整卡片请上调 --max-chars")


if __name__ == "__main__":
    main()
