#!/usr/bin/env python3
"""Run deterministic cross-module checks before LLM synthesis."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .schema import load_result, require_consistent_result_set, require_valid_result, result_set_digest


def _conflict(
    conflict_id: str,
    conflict_type: str,
    modules: list[str],
    description: str,
    severity: str = "medium",
    required_action: str = "Final synthesis must address this item.",
) -> dict[str, Any]:
    return {
        "conflict_id": conflict_id,
        "type": conflict_type,
        "modules": modules,
        "description": description,
        "severity": severity,
        "status": "requires_llm_review",
        "required_action": required_action,
    }


def reconcile_results(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    result_list = require_consistent_result_set(require_valid_result(result) for result in results)
    identity = result_list[0]
    conflicts: list[dict[str, Any]] = []
    warnings: list[str] = []
    statuses: dict[str, str] = {}
    as_of_values: dict[str, str] = {}
    parameter_values: dict[str, list[tuple[str, Any]]] = {}

    for result in result_list:
        result_type = result.get("result_type", "unknown")
        statuses[result_type] = result.get("run", {}).get("status", "unknown")
        as_of = result.get("run", {}).get("as_of")
        if as_of:
            as_of_values[result_type] = as_of
        for key, value in (result.get("parameters") or {}).items():
            if value is not None:
                parameter_values.setdefault(key, []).append((result_type, value))
        if result.get("run", {}).get("status") in {"failed", "partial", "stale"}:
            warnings.append(f"{result_type} status is {result['run']['status']}")

    unique_as_of = sorted(set(as_of_values.values()))
    if len(unique_as_of) > 1:
        conflicts.append(
            _conflict(
                "DATE-001",
                "temporal",
                list(as_of_values),
                f"Modules use different as_of dates: {as_of_values}",
                severity="high",
                required_action="Final synthesis must distinguish current and historical evidence.",
            )
        )

    for parameter, values in parameter_values.items():
        distinct = {json.dumps(value, ensure_ascii=False, sort_keys=True) for _, value in values}
        if len(distinct) > 1:
            conflicts.append(
                _conflict(
                    f"PARAM-{parameter}",
                    "parameter",
                    [module for module, _ in values],
                    f"Parameter {parameter} has conflicting values: {values}",
                )
            )

    by_type = {result.get("result_type"): result for result in result_list}
    moat = (by_type.get("qualitative.business_moat") or {}).get("parameters", {}).get("moat_rating")
    management = (by_type.get("qualitative.governance") or {}).get("parameters", {}).get("management_rating")
    integrity = (by_type.get("qualitative.governance") or {}).get("parameters", {}).get("integrity_rating")
    if moat in {"强", "较强"} and management in {"损害价值", "观察期"}:
        conflicts.append(
            _conflict(
                "JUDGEMENT-001",
                "interpretation",
                ["qualitative.business_moat", "qualitative.governance"],
                f"护城河评级为 {moat}，但管理层评价为 {management}。",
                severity="high",
                required_action="Final synthesis must explain why governance affects valuation confidence.",
            )
        )
    if integrity in {"存疑", "不可靠"} and moat in {"强", "较强"}:
        conflicts.append(
            _conflict(
                "JUDGEMENT-002",
                "interpretation",
                ["qualitative.business_moat", "qualitative.governance"],
                f"护城河评级为 {moat}，但诚信评级为 {integrity}。",
                severity="high",
                required_action="Do not present the moat rating without the governance qualification.",
            )
        )

    degraded_status = any(status in {"failed", "stale"} for status in statuses.values())
    return {
        "schema": "investment.reconciliation",
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run": {"run_id": identity["run"]["run_id"]},
        "subject": identity["subject"],
        "result_digest": result_set_digest(result_list),
        "modules": statuses,
        "as_of": as_of_values,
        "conflicts": conflicts,
        "warnings": warnings,
        "cross_dimension_findings": [],
        "overall_confidence": (
            "low"
            if degraded_status or any(item["severity"] == "high" for item in conflicts)
            else "medium"
        ),
        "requires_llm_review": bool(conflicts or warnings),
        "missing_information": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile module result JSON files")
    parser.add_argument("--input", action="append", required=True, help="path to result.json")
    parser.add_argument(
        "--optional-input",
        action="append",
        default=[],
        help="optional result.json path; missing files are recorded rather than failing",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    results = [load_result(path) for path in args.input]
    missing_optional = [path for path in args.optional_input if not Path(path).exists()]
    results.extend(load_result(path) for path in args.optional_input if Path(path).exists())
    reconciliation = reconcile_results(results)
    if missing_optional:
        reconciliation["missing_information"].extend(
            {"path": path, "status": "not_applicable_or_not_produced"} for path in missing_optional
        )
        reconciliation["warnings"].extend(
            f"Optional module result missing: {path}" for path in missing_optional
        )
        reconciliation["requires_llm_review"] = True
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(reconciliation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Reconciliation written: {args.output} ({len(reconciliation['conflicts'])} conflicts)")


if __name__ == "__main__":
    main()
