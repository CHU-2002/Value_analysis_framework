#!/usr/bin/env python3
"""Validation and compaction helpers for the cross-module result protocol.

The protocol deliberately uses only the Python standard library. LLM output is
treated as untrusted input: callers should validate it before passing it to a
downstream strategy or synthesis agent.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .evidence_ref import (
    SUB_EXCERPT_SEPARATOR,
    evidence_reference_base,
    format_evidence_reference,
    split_evidence_reference,
)


RESULT_SCHEMA_ID = "investment.result"
RESULT_SCHEMA_VERSION = "1.0"
RECONCILIATION_SCHEMA = "investment.reconciliation"
VALID_STATUSES = {"complete", "partial", "failed", "stale", "not_applicable"}
VALID_CONFIDENCE = {"high", "medium", "low", "unknown"}
VALID_CLAIM_TYPES = {"fact", "inference", "judgement"}

RESULT_TYPE_CONTRACTS: dict[str, dict[str, Any]] = {
    "qualitative.business_moat": {
        "scope": ["D1", "D2"],
        "parameters": {
            "business_model_clarity": {"清晰且已验证", "清晰但未充分验证", "模糊"},
            "capital_intensity": {"capital-light", "capital-hungry"},
            "collection_mode": {"先款后货", "订阅预收", "先货后款", "垫资回收"},
            "cash_impact": {"正贡献", "中性", "负担"},
            "market_structure": {"垄断", "寡头", "垄断竞争", "充分竞争"},
            "market_cr4": "nullable_number",
            "entry_barrier": {"高", "中", "低"},
            "roe_5y_avg": "number",
            "moat_existence": {"存在", "可能存在", "不存在"},
            "moat_evidence_strength": {"强证据", "中等证据", "弱证据"},
            "moat_type": "string",
            "moat_framework_primary": {"A", "B"},
            "supply_side_rating": {"强", "较强", "中等", "弱", "不适用"},
            "demand_side_rating": {"强", "较强", "中等", "弱", "不适用"},
            "scale_economy_rating": {"强", "较强", "中等", "弱", "不适用"},
            "moat_flywheel": "boolean",
            "false_advantages": "list",
            "competitors": "list",
            "competitor_ranking": "string",
            "advantage_gap_sustainability": {"高", "中", "低"},
            "pricing_power": {"强", "中", "弱"},
            "human_capital_dep": {"系统型", "人才型"},
            "moat_sustainability": {"高可持续", "中等可持续", "低可持续"},
            "moat_rating": {"强", "较强", "中", "弱"},
            "moat_monitor_kpis": "list",
        },
    },
    "qualitative.environment": {
        "scope": ["D3"],
        "parameters": {
            # REQ-006.2 AC-2.7 / F24：模块规格允许「不知道」，枚举必须能表达它，
            # 否则 agent 只能把「未知」写成「不适用」（语义不同）。
            "cyclicality": {"强周期", "弱周期", "非周期", "unknown"},
            "cycle_position": {"底部", "中段", "顶部", "不适用", "unknown"},
            "regulatory_risk": {"低", "中", "高", "unknown"},
            "industry_keywords": "list",
        },
    },
    "qualitative.governance": {
        "scope": ["D4"],
        "parameters": {
            "governance_flags": "list",
            "management_rating": {"优秀", "合格", "损害价值", "观察期"},
            "integrity_rating": {"可靠", "存疑", "不可靠"},
            "promise_delivery": {"高", "中", "低"},
            "valuation_confidence_impact": {"轻微", "明显", "严重"},
            "capital_allocation_record": "string",
            "related_party_risk": {"低", "中", "高"},
        },
    },
    "qualitative.mda_quality": {
        "scope": ["D5"],
        "parameters": {
            "mda_credibility": {"高", "中", "低"},
            "mda_impact": {"正面", "中性", "负面"},
            "mda_forward_guidance": {"有量化", "仅方向性", "无"},
            "distribution_signal": "string",
        },
    },
    "qualitative.holding_structure": {
        "scope": ["D6"],
        "parameters": {
            "holding_structure": "boolean",
            "sotp_value_mm": "nullable_number",
            "sotp_discount_pct": "nullable_number",
        },
    },
    "qualitative.period_delta": {
        "scope": ["D7"],
        "parameters": {
            "report_period": "string",
            "comparable_period": "string",
            "business_trend": {"改善", "稳定", "恶化", "不确定"},
            "conclusion_change": {"维持", "上调", "下调", "证据不足"},
            "change_significance": {"重大", "一般", "轻微"},
            "guidance_delivery": {"兑现", "部分兑现", "未兑现", "无法验证", "无指引"},
            "requires_full_rerun": "boolean",
            "revenue_yoy_pct": "nullable_number",
            "net_profit_yoy_pct": "nullable_number",
            "gross_margin_change_pct": "nullable_number",
            "operating_cashflow_to_profit": "nullable_number",
        },
    },
}

PARAMETER_OWNERS = {
    key: result_type
    for result_type, contract in RESULT_TYPE_CONTRACTS.items()
    for key in contract["parameters"]
}


class ResultValidationError(ValueError):
    """Raised when a result does not satisfy the result contract."""


def _is_mapping(value: Any) -> bool:
    return isinstance(value, dict)


def _require_mapping(parent: dict[str, Any], key: str, errors: list[str]) -> dict[str, Any]:
    value = parent.get(key)
    if not _is_mapping(value):
        errors.append(f"{key} must be an object")
        return {}
    return value


def _require_string(parent: dict[str, Any], key: str, errors: list[str]) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{key} must be a non-empty string")
        return ""
    return value


def _valid_parameter_value(value: Any, rule: Any) -> bool:
    if isinstance(rule, set):
        return isinstance(value, str) and value in rule
    if rule == "string":
        return isinstance(value, str) and bool(value.strip())
    if rule == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))
        )
    if rule == "nullable_number":
        return value is None or _valid_parameter_value(value, "number")
    if rule == "boolean":
        return isinstance(value, bool)
    if rule == "list":
        return isinstance(value, list)
    return False


def _validate_as_of(run: dict[str, Any]) -> list[str]:
    """`run.as_of` 的取值规则（REQ-006.2 AC-2.7）。

    规则：`as_of` 是「本模块结论的观察时点」，必须是 ``YYYY-MM-DD``，且**不得晚于**
    `generated_at` 的日期（不能拿未来的日期当结论时点）。实测反例：六个模块给出
    ``2026-07`` / ``2026-09-18`` / ``2026-06-30`` 三种口径并触发 high 级 DATE-001。
    """

    errors: list[str] = []
    as_of = run.get("as_of")
    if not isinstance(as_of, str) or not as_of.strip():
        return errors
    value = as_of.strip()
    try:
        observed = datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        errors.append(f"run.as_of must be YYYY-MM-DD, got {as_of!r}")
        return errors
    generated_at = run.get("generated_at")
    if isinstance(generated_at, str) and generated_at.strip():
        try:
            generated = datetime.strptime(generated_at.strip()[:10], "%Y-%m-%d")
        except ValueError:
            return errors
        if observed > generated:
            errors.append(
                f"run.as_of {value} is later than run.generated_at {generated_at!r}"
            )
    return errors


def validate_result(result: dict[str, Any], *, strict: bool = True) -> list[str]:
    """Return validation errors for a module result.

    ``strict=False`` keeps compatibility with partial legacy sidecars while
    still checking the fields that downstream consumers cannot safely infer.
    """

    errors: list[str] = []
    if not _is_mapping(result):
        return ["result must be an object"]

    if result.get("schema") != RESULT_SCHEMA_ID:
        errors.append(f"schema must be {RESULT_SCHEMA_ID!r}")
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {RESULT_SCHEMA_VERSION!r}")

    result_type = _require_string(result, "result_type", errors)
    run = _require_mapping(result, "run", errors)
    subject = _require_mapping(result, "subject", errors)
    summary = _require_mapping(result, "summary", errors)
    quality = _require_mapping(result, "quality", errors)
    if strict or "parameters" in result:
        _require_mapping(result, "parameters", errors)
    if strict or "metrics" in result:
        _require_mapping(result, "metrics", errors)

    if _is_mapping(result.get("run")):
        _require_string(run, "run_id", errors)
        _require_string(run, "generated_at", errors)
        _require_string(run, "as_of", errors)
        if not isinstance(run.get("status"), str) or run["status"] not in VALID_STATUSES:
            errors.append(f"run.status must be one of {sorted(VALID_STATUSES)}")
        errors.extend(_validate_as_of(run))

    if _is_mapping(result.get("subject")):
        _require_string(subject, "ticker", errors)
        _require_string(subject, "company", errors)
        _require_string(subject, "market", errors)

    if _is_mapping(result.get("summary")):
        if run.get("status") not in ("failed", "not_applicable"):
            _require_string(summary, "thesis", errors)
        confidence = summary.get("confidence")
        if not isinstance(confidence, str) or confidence not in VALID_CONFIDENCE:
            errors.append(f"summary.confidence must be one of {sorted(VALID_CONFIDENCE)}")

    scopes = result.get("scope")
    if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
        errors.append("scope must be a list of strings")

    contract = RESULT_TYPE_CONTRACTS.get(result_type)
    parameters = result.get("parameters") if isinstance(result.get("parameters"), dict) else {}
    if contract:
        if scopes != contract["scope"]:
            errors.append(f"scope for {result_type} must be {contract['scope']!r}")
        required_parameters = set(contract["parameters"])
        if run.get("status") in ("complete", "not_applicable"):
            missing_parameters = sorted(required_parameters - set(parameters))
            if missing_parameters:
                errors.append(
                    f"parameters for {result_type} are missing required keys: {missing_parameters}"
                )
        for key, value in parameters.items():
            rule = contract["parameters"].get(key)
            if rule is not None and not _valid_parameter_value(value, rule):
                errors.append(f"parameters.{key} has an invalid value: {value!r}")
    if result_type == "qualitative.holding_structure":
        if run.get("status") == "not_applicable" and parameters.get("holding_structure") is not False:
            errors.append("not_applicable holding_structure must be false")
        if parameters.get("holding_structure") is False and any(
            parameters.get(key) is not None for key in ("sotp_value_mm", "sotp_discount_pct")
        ):
            errors.append("inapplicable holding_structure must have null SOTP fields")
    if result_type == "qualitative.synthesis":
        _require_string(result, "upstream_digest", errors)

    claims = result.get("claims", [])
    if not isinstance(claims, list):
        errors.append("claims must be a list")
        claims = []

    risks = result.get("risks", [])
    if not isinstance(risks, list):
        errors.append("risks must be a list")

    watchlist = result.get("watchlist", [])
    if not isinstance(watchlist, list) or not all(isinstance(item, str) for item in watchlist):
        errors.append("watchlist must be a list of strings")

    evidence = result.get("evidence", [])
    if not isinstance(evidence, list):
        errors.append("evidence must be a list")
        evidence = []

    evidence_ids: set[str] = set()
    base_evidence_ids: set[str] = set()
    for index, item in enumerate(evidence):
        if not _is_mapping(item):
            errors.append(f"evidence[{index}] must be an object")
            continue
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            errors.append(f"evidence[{index}].evidence_id must be a non-empty string")
        elif evidence_id in evidence_ids:
            errors.append(f"duplicate evidence_id: {evidence_id}")
        else:
            evidence_ids.add(evidence_id)
            # 允许 `base#1` 与 `base#2` 各占一条：具名子段本来就是「同一块的不同摘录」，
            # 唯一性判在**引用**上而不是块 id 上（REQ-006.2 AC-2.5 / F25）。
            base_evidence_ids.add(evidence_reference_base(evidence_id))
        if not isinstance(item.get("source_id"), str) or not item["source_id"].strip():
            errors.append(f"evidence[{index}].source_id must be a non-empty string")
        if not isinstance(item.get("locator"), dict) or not item["locator"]:
            errors.append(f"evidence[{index}].locator must be a non-empty object")
        if not isinstance(item.get("quote"), str) or not item["quote"].strip():
            errors.append(f"evidence[{index}].quote must be a non-empty string")
        elif len(item["quote"]) > 300:
            errors.append(f"evidence[{index}].quote must be no more than 300 characters")

    claim_ids: set[str] = set()
    for index, item in enumerate(claims):
        if not _is_mapping(item):
            errors.append(f"claims[{index}] must be an object")
            continue
        if not isinstance(item.get("claim_id"), str) or not item["claim_id"].strip():
            errors.append(f"claims[{index}].claim_id must be a non-empty string")
        elif item["claim_id"] in claim_ids:
            errors.append(f"duplicate claim_id: {item['claim_id']}")
        else:
            claim_ids.add(item["claim_id"])
        if not isinstance(item.get("statement"), str) or not item["statement"].strip():
            errors.append(f"claims[{index}].statement must be a non-empty string")
        if not isinstance(item.get("type"), str) or item["type"] not in VALID_CLAIM_TYPES:
            errors.append(f"claims[{index}].type must be one of {sorted(VALID_CLAIM_TYPES)}")
        claim_confidence = item.get("confidence")
        if not isinstance(claim_confidence, str) or claim_confidence not in VALID_CONFIDENCE:
            errors.append(f"claims[{index}].confidence is invalid")
        references = item.get("evidence_ids", [])
        if not isinstance(references, list) or not all(isinstance(ref, str) for ref in references):
            errors.append(f"claims[{index}].evidence_ids must be a list of strings")
        elif strict and not references and item.get("type") != "fact":
            errors.append(f"claims[{index}] must cite evidence for non-fact claims")
        elif strict:
            # 引用可以写成 `base` 或 `base#n`（具名子段）；两种都按块 id 归属校验。
            missing = sorted(
                {
                    ref
                    for ref in references
                    if evidence_reference_base(ref) not in base_evidence_ids
                }
            )
            if missing:
                errors.append(f"claims[{index}] references unknown evidence: {missing}")

    if _is_mapping(result.get("quality")):
        completeness = quality.get("completeness")
        if not _valid_parameter_value(completeness, "number") or not 0 <= completeness <= 1:
            errors.append("quality.completeness must be a number between 0 and 1")
        for key in ("missing_inputs", "warnings", "unresolved_questions"):
            if not isinstance(quality.get(key, []), list):
                errors.append(f"quality.{key} must be a list")

    return errors


def validate_result_set(results: Iterable[dict[str, Any]]) -> list[str]:
    """Check that independently valid results belong to one subject and run."""

    result_list = list(results)
    if not result_list:
        return ["result set must not be empty"]

    errors: list[str] = []
    first = result_list[0]
    expected_run_id = first.get("run", {}).get("run_id")
    expected_subject = first.get("subject", {})
    seen_types: set[str] = set()
    for index, result in enumerate(result_list):
        result_type = result.get("result_type")
        if result_type in seen_types:
            errors.append(f"results[{index}] duplicates result_type {result_type!r}")
        elif isinstance(result_type, str):
            seen_types.add(result_type)
        run_id = result.get("run", {}).get("run_id")
        if run_id != expected_run_id:
            errors.append(
                f"results[{index}].run.run_id is {run_id!r}; expected {expected_run_id!r}"
            )
        for field in ("ticker", "company", "market"):
            value = result.get("subject", {}).get(field)
            expected = expected_subject.get(field)
            if value != expected:
                errors.append(
                    f"results[{index}].subject.{field} is {value!r}; expected {expected!r}"
                )
    return errors


def require_consistent_result_set(
    results: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a materialized result set or raise when identities conflict."""

    result_list = list(results)
    errors = validate_result_set(result_list)
    if errors:
        raise ResultValidationError("Inconsistent investment result set:\n- " + "\n- ".join(errors))
    return result_list


def result_set_digest(results: Iterable[dict[str, Any]]) -> str:
    """Hash a complete module set independent of input ordering."""

    ordered = sorted(results, key=lambda item: str(item.get("result_type", "")))
    encoded = json.dumps(
        ordered,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_valid_result(result: dict[str, Any], *, strict: bool = True) -> dict[str, Any]:
    """Validate and return a result, raising a useful exception on failure."""

    errors = validate_result(result, strict=strict)
    if errors:
        raise ResultValidationError("Invalid investment result:\n- " + "\n- ".join(errors))
    return result


def load_result(path: str | Path, *, strict: bool = True) -> dict[str, Any]:
    """Load and validate a result JSON file."""

    result = json.loads(Path(path).read_text(encoding="utf-8"))
    return require_valid_result(result, strict=strict)


def _limit_items(items: Iterable[Any], limit: int) -> list[Any]:
    return list(items)[:limit]


def _shorten(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars].rstrip() + "..."
    if isinstance(value, list):
        return [_shorten(item, max_chars) for item in value]
    if isinstance(value, dict):
        return {key: _shorten(item, max_chars) for key, item in value.items()}
    return value


def compact_result(
    result: dict[str, Any],
    *,
    max_claims: int = 8,
    max_evidence: int = 12,
    max_risks: int = 8,
    max_watchlist: int = 8,
) -> dict[str, Any]:
    """Return the small result card intended for a synthesis prompt.

    The full result remains available on disk for audit. This function removes
    large or implementation-specific fields before cross-module handoff.
    """

    evidence_by_id = {
        item.get("evidence_id"): item
        for item in result.get("evidence", [])
        if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
    }
    selected_claims: list[dict[str, Any]] = []
    referenced_ids: list[str] = []
    for claim in result.get("claims", []):
        if len(selected_claims) >= max_claims or not isinstance(claim, dict):
            break
        claim_references = list(dict.fromkeys(claim.get("evidence_ids", [])))
        new_references = [ref for ref in claim_references if ref not in referenced_ids]
        if len(referenced_ids) + len(new_references) > max_evidence:
            continue
        selected_claims.append(claim)
        referenced_ids.extend(new_references)

    selected_evidence = [evidence_by_id[ref] for ref in referenced_ids if ref in evidence_by_id]
    if len(selected_evidence) < max_evidence:
        selected_ids = set(referenced_ids)
        selected_evidence.extend(
            item
            for item in result.get("evidence", [])
            if isinstance(item, dict) and item.get("evidence_id") not in selected_ids
        )
        selected_evidence = selected_evidence[:max_evidence]

    compact = {
        "schema": result.get("schema"),
        "schema_version": result.get("schema_version"),
        "result_type": result.get("result_type"),
        "run": result.get("run", {}),
        "subject": result.get("subject", {}),
        "scope": result.get("scope", []),
        "summary": result.get("summary", {}),
        "parameters": _shorten(result.get("parameters", {}), 600),
        "metrics": _shorten(result.get("metrics", {}), 300),
        "claims": selected_claims,
        "risks": _limit_items(result.get("risks", []), max_risks),
        "watchlist": _limit_items(result.get("watchlist", []), max_watchlist),
        "evidence": selected_evidence,
        "quality": result.get("quality", {}),
    }
    return compact


RESULT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": RESULT_SCHEMA_ID,
    "type": "object",
    "required": [
        "schema",
        "schema_version",
        "result_type",
        "run",
        "subject",
        "scope",
        "summary",
        "parameters",
        "metrics",
        "claims",
        "risks",
        "watchlist",
        "evidence",
        "quality",
    ],
    "description": "See validate_result() for runtime checks and compatibility rules.",
}
