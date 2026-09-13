#!/usr/bin/env python3
"""Resolve structured qualitative results with an atomic Markdown fallback."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .evidence import validate_result_evidence
from .manifest import validate_manifest_artifacts, validate_manifest_inputs
from .schema import (
    PARAMETER_OWNERS,
    ResultValidationError,
    compact_result,
    load_result,
    result_set_digest,
    validate_result_set,
)

try:
    from scripts.config import validate_stock_code
except ImportError:  # Support importing the package with scripts/ on sys.path.
    from config import validate_stock_code


CORE_MODULES = {
    "business_moat": "qualitative.business_moat",
    "environment": "qualitative.environment",
    "governance": "qualitative.governance",
    "mda_quality": "qualitative.mda_quality",
}
OPTIONAL_MODULES = {"holding_structure": "qualitative.holding_structure"}
CONSUMABLE_STATUSES = {"complete", "partial"}


def _normalise_ticker(ticker: str) -> str:
    return validate_stock_code(ticker)


def _load_candidate(
    path: Path,
    *,
    expected_type: str,
    ticker: str,
    optional: bool = False,
    expected_run_id: str | None = None,
    expected_subject: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.exists():
        return None, [f"missing result: {path}"]
    try:
        result = load_result(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ResultValidationError) as exc:
        return None, [f"invalid result {path}: {exc}"]

    errors: list[str] = []
    if result.get("result_type") != expected_type:
        errors.append(
            f"{path} result_type is {result.get('result_type')!r}; expected {expected_type!r}"
        )
    try:
        actual_ticker = _normalise_ticker(str(result.get("subject", {}).get("ticker", "")))
    except ValueError:
        errors.append(f"{path} has an invalid ticker")
    else:
        if actual_ticker != ticker:
            errors.append(f"{path} ticker is {actual_ticker!r}; expected {ticker!r}")
        else:
            result["subject"]["ticker"] = actual_ticker
    run_id = result.get("run", {}).get("run_id")
    if expected_run_id is not None and run_id != expected_run_id:
        errors.append(f"{path} run_id is {run_id!r}; expected {expected_run_id!r}")
    if expected_subject is not None and result.get("subject") != expected_subject:
        errors.append(f"{path} subject does not match the run manifest")
    allowed_statuses = CONSUMABLE_STATUSES | ({"not_applicable"} if optional else set())
    status = result.get("run", {}).get("status")
    if status not in allowed_statuses:
        errors.append(f"{path} status {status!r} is not consumable")
    return (None, errors) if errors else (result, [])


def _load_reconciliation(
    path: Path,
    *,
    expected_run_id: str,
    expected_subject: dict[str, Any],
    expected_result_digest: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.exists():
        return None, [f"missing reconciliation: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, [f"invalid reconciliation {path}: {exc}"]
    if not isinstance(payload, dict) or payload.get("schema") != "investment.reconciliation":
        return None, [f"invalid reconciliation schema: {path}"]
    if not isinstance(payload.get("run"), dict) or payload["run"].get("run_id") != expected_run_id:
        return None, [f"reconciliation run_id does not match manifest: {path}"]
    if payload.get("subject") != expected_subject:
        return None, [f"reconciliation subject does not match manifest: {path}"]
    if payload.get("result_digest") != expected_result_digest:
        return None, [f"reconciliation result_digest does not match module results: {path}"]
    return {
        "path": str(path),
        "conflicts": payload.get("conflicts", []),
        "warnings": payload.get("warnings", []),
        "requires_llm_review": payload.get("requires_llm_review", False),
        "overall_confidence": payload.get("overall_confidence", "unknown"),
    }, []


def _load_evidence_index(
    path: Path,
    *,
    expected_run_id: str,
    expected_subject: dict[str, Any],
    expected_input_digest: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.exists():
        return None, [f"missing evidence index: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, [f"invalid evidence index {path}: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"evidence index must be an object: {path}"]
    errors = []
    if payload.get("schema") != "investment.evidence_index":
        errors.append(f"invalid evidence index schema: {path}")
    if not isinstance(payload.get("run"), dict) or payload["run"].get("run_id") != expected_run_id:
        errors.append(f"evidence index run_id does not match manifest: {path}")
    if payload.get("subject") != expected_subject:
        errors.append(f"evidence index subject does not match manifest: {path}")
    if payload.get("input_digest") != expected_input_digest:
        errors.append(f"evidence index input_digest does not match manifest: {path}")
    return (None, errors) if errors else (payload, [])


def _legacy_report_errors(root: Path, path: Path, expected_ticker: str) -> list[str]:
    if not path.is_file():
        return [f"legacy report is not a regular file: {path}"]
    try:
        if path.stat().st_size == 0 or not path.read_text(encoding="utf-8").strip():
            return [f"legacy report is empty: {path}"]
    except (OSError, UnicodeError) as exc:
        return [f"legacy report is unreadable {path}: {exc}"]
    directory_code = root.name.split("_", 1)[0]
    try:
        directory_ticker = _normalise_ticker(directory_code)
    except ValueError:
        return [f"legacy directory name does not contain a verifiable ticker: {root.name}"]
    if directory_ticker != expected_ticker:
        return [
            f"legacy directory ticker is {directory_ticker!r}; expected {expected_ticker!r}"
        ]
    return []


def resolve_qualitative_input(output_dir: str | Path, *, ticker: str) -> dict[str, Any]:
    """Select a complete structured result set or fall back to the legacy report."""

    root = Path(output_dir)
    if not root.is_dir():
        raise ValueError(f"output directory does not exist: {root}")
    expected_ticker = _normalise_ticker(ticker)
    legacy_path = root / "qualitative_report.md"
    structured_errors: list[str] = []
    results: dict[str, dict[str, Any]] = {}
    expected_run_id = None
    expected_subject = None
    expected_input_digest = None
    manifest_path = root / "run_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest must be an object")
            if manifest.get("schema") != "investment.manifest":
                structured_errors.append(f"invalid manifest schema: {manifest_path}")
            else:
                expected_run_id = manifest.get("run_id")
                if not isinstance(expected_run_id, str) or not expected_run_id:
                    structured_errors.append(f"manifest run_id is invalid: {manifest_path}")
                manifest_subject = manifest.get("subject")
                if not isinstance(manifest_subject, dict):
                    raise ValueError("manifest subject must be an object")
                manifest_ticker = _normalise_ticker(str(manifest_subject.get("ticker", "")))
                manifest_subject["ticker"] = manifest_ticker
                if not all(
                    isinstance(manifest_subject.get(field), str) and manifest_subject[field].strip()
                    for field in ("company", "market")
                ):
                    raise ValueError("manifest subject company and market must be non-empty strings")
                expected_subject = manifest_subject
                expected_input_digest = manifest.get("input_digest")
                if manifest_ticker != expected_ticker:
                    structured_errors.append(
                        f"manifest ticker is {manifest_ticker!r}; expected {expected_ticker!r}"
                    )
                if not isinstance(expected_input_digest, str) or not expected_input_digest:
                    structured_errors.append(f"manifest input_digest is invalid: {manifest_path}")
                structured_errors.extend(validate_manifest_inputs(manifest))
                structured_errors.extend(validate_manifest_artifacts(manifest))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            structured_errors.append(f"invalid manifest {manifest_path}: {exc}")
    else:
        structured_errors.append(f"missing run manifest: {manifest_path}")

    evidence_index = None
    if expected_run_id and expected_subject and expected_input_digest:
        evidence_index, evidence_errors = _load_evidence_index(
            root / "evidence" / "index.json",
            expected_run_id=expected_run_id,
            expected_subject=expected_subject,
            expected_input_digest=expected_input_digest,
        )
        structured_errors.extend(evidence_errors)

    for module, result_type in CORE_MODULES.items():
        result, errors = _load_candidate(
            root / "modules" / module / "result.json",
            expected_type=result_type,
            ticker=expected_ticker,
            expected_run_id=expected_run_id,
            expected_subject=expected_subject,
        )
        structured_errors.extend(errors)
        if result is not None:
            results[module] = result

    base = {
        "schema": "investment.qualitative_input",
        "schema_version": "1.0",
        "subject": {"ticker": expected_ticker},
    }

    if len(results) == len(CORE_MODULES):
        structured_errors.extend(validate_result_set(results.values()))
        if evidence_index is not None:
            structured_errors.extend(
                f"{module}: {error}"
                for module, result in results.items()
                for error in validate_result_evidence(result, evidence_index)
            )

    optional_results: dict[str, dict[str, Any]] = {}
    optional_warnings: list[str] = []
    for module, result_type in OPTIONAL_MODULES.items():
        path = root / "modules" / module / "result.json"
        if not path.exists():
            continue
        result, errors = _load_candidate(
            path,
            expected_type=result_type,
            ticker=expected_ticker,
            optional=True,
            expected_run_id=expected_run_id,
            expected_subject=expected_subject,
        )
        if result is not None and evidence_index is not None:
            evidence_errors = validate_result_evidence(result, evidence_index)
            errors.extend(evidence_errors)
            if evidence_errors:
                result = None
        optional_warnings.extend(errors)
        if result is not None:
            optional_results[module] = result

    artifact_results = [*results.values(), *optional_results.values()]
    reconciliation = None
    upstream_digest = result_set_digest(artifact_results) if len(results) == len(CORE_MODULES) else None
    if expected_run_id and expected_subject and upstream_digest:
        reconciliation, reconciliation_errors = _load_reconciliation(
            root / "synthesis" / "reconciliation.json",
            expected_run_id=expected_run_id,
            expected_subject=expected_subject,
            expected_result_digest=upstream_digest,
        )
        structured_errors.extend(reconciliation_errors)

    synthesis = None
    if expected_run_id and expected_subject:
        synthesis_result, synthesis_errors = _load_candidate(
            root / "synthesis" / "result.json",
            expected_type="qualitative.synthesis",
            ticker=expected_ticker,
            expected_run_id=expected_run_id,
            expected_subject=expected_subject,
        )
        structured_errors.extend(synthesis_errors)
        if (
            synthesis_result is not None
            and upstream_digest is not None
            and synthesis_result.get("upstream_digest") != upstream_digest
        ):
            structured_errors.append(
                f"synthesis upstream_digest does not match module results: {root / 'synthesis' / 'result.json'}"
            )
            synthesis_result = None
        if synthesis_result is not None and evidence_index is not None:
            synthesis_evidence_errors = validate_result_evidence(synthesis_result, evidence_index)
            structured_errors.extend(
                f"synthesis: {error}" for error in synthesis_evidence_errors
            )
            if not synthesis_evidence_errors:
                synthesis = compact_result(synthesis_result)

    if len(results) != len(CORE_MODULES) or structured_errors:
        legacy_errors = _legacy_report_errors(root, legacy_path, expected_ticker) if legacy_path.exists() else []
        if not manifest_path.exists() and legacy_path.exists() and not legacy_errors:
            return {
                **base,
                "source": "legacy",
                "parameters": {},
                "parameter_sources": {},
                "modules": {},
                "synthesis": None,
                "reconciliation": None,
                "missing_modules": sorted(set(CORE_MODULES) - set(results)),
                "legacy_report": {"path": str(legacy_path), "role": "primary"},
                "warnings": [
                    "Structured qualitative result set is incomplete; using legacy report atomically.",
                    "Legacy report freshness cannot be verified because this compatibility directory has no run manifest.",
                    *structured_errors,
                ],
            }
        return {
            **base,
            "source": "unavailable",
            "parameters": {},
            "parameter_sources": {},
            "modules": {},
            "synthesis": None,
            "reconciliation": None,
            "missing_modules": sorted(set(CORE_MODULES) - set(results)),
            "legacy_report": None,
            "warnings": [*structured_errors, *legacy_errors],
        }

    warnings: list[str] = optional_warnings
    results.update(optional_results)
    parameters: dict[str, Any] = {}
    parameter_sources: dict[str, str] = {}
    conflicted_parameters: set[str] = set()
    for module, result in results.items():
        status = result.get("run", {}).get("status")
        if status == "partial":
            warnings.append(f"{module} result is partial")
        for key, value in result.get("parameters", {}).items():
            if key in conflicted_parameters:
                continue
            owner = PARAMETER_OWNERS.get(key)
            if owner is not None and owner != result.get("result_type"):
                warnings.append(
                    f"parameter {key!r} is owned by {owner}; ignoring value from {module}"
                )
                continue
            if key in parameters and parameters[key] != value:
                warnings.append(
                    f"parameter {key!r} conflicts between {parameter_sources[key]} and {module}; "
                    "omitting the unresolved value"
                )
                parameters.pop(key, None)
                parameter_sources.pop(key, None)
                conflicted_parameters.add(key)
                continue
            parameters[key] = value
            parameter_sources[key] = module

    return {
        **base,
        "source": "structured",
        "parameters": parameters,
        "parameter_sources": parameter_sources,
        "modules": {module: compact_result(result) for module, result in results.items()},
        "synthesis": synthesis,
        "reconciliation": reconciliation,
        "missing_modules": sorted(set(OPTIONAL_MODULES) - set(optional_results)),
        "legacy_report": None,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve qualitative inputs for downstream strategies")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    try:
        payload = resolve_qualitative_input(args.output_dir, ticker=args.ticker)
    except ValueError as exc:
        parser.error(str(exc))
    output_path = Path(args.output) if args.output else Path(args.output_dir) / "qualitative_input.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Qualitative input written: {output_path} (source={payload['source']})")
    if payload["source"] == "unavailable":
        raise SystemExit(3)


if __name__ == "__main__":
    main()
