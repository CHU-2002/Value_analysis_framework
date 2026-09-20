"""Tests for the structured result and bounded-context pipeline."""

# 覆盖需求：REQ-006.1（财报分析端到端实跑加固）—— AC-1.3 prepare 的 run_id 一致性、
# AC-1.4 period_delta 作为可选模块参与 digest、AC-1.5 上下文预算与丢卡可见性、
# AC-1.7 证据边界检查器

import json
import sys
from pathlib import Path

import pytest

from results.context import build_module_context
from results.evidence import (
    build_evidence_index,
    bundle_evidence_ids,
    chunk_text,
    select_evidence,
    validate_bundle_evidence,
    validate_result_evidence,
)
from results.manifest import build_manifest, describe_input
from results.prepare import prepare_run
from results.resolve_qualitative import main as resolve_main, resolve_qualitative_input
from results.synthesis import build_synthesis_context
from results.reconcile_results import main as reconcile_main, reconcile_results
from results.schema import ResultValidationError, compact_result, result_set_digest, validate_result
from results.validate_result import main as validate_main


DEFAULT_PARAMETERS = {
    "qualitative.business_moat": {
        "business_model_clarity": "清晰且已验证",
        "capital_intensity": "capital-light",
        "collection_mode": "先款后货",
        "cash_impact": "正贡献",
        "market_structure": "寡头",
        "market_cr4": 60.0,
        "entry_barrier": "高",
        "roe_5y_avg": 12.5,
        "moat_existence": "存在",
        "moat_evidence_strength": "强证据",
        "moat_type": "[非技术] 品牌 + [技术] 不适用",
        "moat_framework_primary": "B",
        "supply_side_rating": "较强",
        "demand_side_rating": "强",
        "scale_economy_rating": "较强",
        "moat_flywheel": True,
        "false_advantages": [],
        "competitors": [],
        "competitor_ranking": "行业前列",
        "advantage_gap_sustainability": "高",
        "pricing_power": "强",
        "human_capital_dep": "系统型",
        "moat_sustainability": "高可持续",
        "moat_rating": "强",
        "moat_monitor_kpis": [],
    },
    "qualitative.environment": {
        "cyclicality": "弱周期",
        "cycle_position": "不适用",
        "regulatory_risk": "低",
        "industry_keywords": [],
    },
    "qualitative.governance": {
        "governance_flags": [],
        "management_rating": "合格",
        "integrity_rating": "可靠",
        "promise_delivery": "高",
        "valuation_confidence_impact": "轻微",
        "capital_allocation_record": "稳定",
        "related_party_risk": "低",
    },
    "qualitative.mda_quality": {
        "mda_credibility": "高",
        "mda_impact": "正面",
        "mda_forward_guidance": "有量化",
        "distribution_signal": "稳定分红",
    },
    "qualitative.holding_structure": {
        "holding_structure": False,
        "sotp_value_mm": None,
        "sotp_discount_pct": None,
    },
}

DEFAULT_PARAMETERS["qualitative.period_delta"] = {
    "report_period": "2026H1",
    "comparable_period": "2025H1",
    "business_trend": "稳定",
    "conclusion_change": "维持",
    "change_significance": "一般",
    "guidance_delivery": "无指引",
    "requires_full_rerun": False,
    "revenue_yoy_pct": 5.0,
    "net_profit_yoy_pct": 4.0,
    "gross_margin_change_pct": 0.5,
    "operating_cashflow_to_profit": 1.0,
}

RESULT_SCOPES = {
    "qualitative.business_moat": ["D1", "D2"],
    "qualitative.environment": ["D3"],
    "qualitative.governance": ["D4"],
    "qualitative.mda_quality": ["D5"],
    "qualitative.holding_structure": ["D6"],
    "qualitative.period_delta": ["D7"],
    "qualitative.synthesis": ["D1", "D2", "D3", "D4", "D5", "D6"],
}


def make_result(
    result_type="qualitative.business_moat",
    *,
    as_of="2025-12-31",
    parameters=None,
    run_id="test-run",
    ticker="600000.SH",
    upstream_digest="upstream-digest",
):
    resolved_parameters = dict(DEFAULT_PARAMETERS.get(result_type, {}))
    if parameters:
        resolved_parameters.update(parameters)
    result = {
        "schema": "investment.result",
        "schema_version": "1.0",
        "result_type": result_type,
        "run": {
            "run_id": run_id,
            "generated_at": "2026-09-06T00:00:00Z",
            "as_of": as_of,
            "status": "complete",
        },
        "subject": {"ticker": ticker, "company": "Example Co", "market": "CN"},
        "scope": RESULT_SCOPES[result_type],
        "summary": {"thesis": "The business is understandable.", "confidence": "medium"},
        "parameters": resolved_parameters,
        "metrics": {"roe_5y_avg": 12.5},
        "claims": [
            {
                "claim_id": "C-001",
                "statement": "Returns have been stable.",
                "type": "judgement",
                "confidence": "medium",
                "evidence_ids": ["E-001"],
            }
        ],
        "risks": [],
        "watchlist": [],
        "evidence": [
            {
                "evidence_id": "E-001",
                "source_id": "market_data",
                "locator": {"section": "12"},
                "quote": "Five-year ROE remained stable.",
            }
        ],
        "quality": {
            "completeness": 0.9,
            "missing_inputs": [],
            "warnings": [],
            "unresolved_questions": [],
        },
    }
    if result_type == "qualitative.synthesis":
        result["upstream_digest"] = upstream_digest
    return result


def make_index(entries, *, run_id="test-run", ticker="600000.SH", input_digest="digest"):
    return {
        "schema": "investment.evidence_index",
        "schema_version": "1.0",
        "run": {"run_id": run_id},
        "subject": {"ticker": ticker, "company": "Example Co", "market": "CN"},
        "input_digest": input_digest,
        "entries": entries,
    }


def test_valid_result_passes_strict_validation():
    assert validate_result(make_result()) == []


def test_complete_module_requires_its_parameter_contract():
    result = make_result("qualitative.environment")
    result["parameters"].pop("regulatory_risk")
    assert any("missing required keys" in error for error in validate_result(result))


def test_partial_module_allows_missing_but_not_invalid_parameters():
    result = make_result("qualitative.environment")
    result["run"]["status"] = "partial"
    result["parameters"] = {"regulatory_risk": "impossible"}
    errors = validate_result(result)
    assert not any("missing required keys" in error for error in errors)
    assert "parameters.regulatory_risk has an invalid value: 'impossible'" in errors


@pytest.mark.parametrize("value", [[], {}])
@pytest.mark.parametrize(
    "path",
    [
        ("result_type",),
        ("run", "status"),
        ("summary", "confidence"),
        ("parameters", "moat_rating"),
        ("claims", 0, "type"),
        ("claims", 0, "confidence"),
    ],
)
def test_invalid_enum_types_return_validation_errors(path, value):
    result = make_result()
    target = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    assert validate_result(result)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True])
def test_numeric_parameters_reject_nonfinite_values_and_booleans(value):
    result = make_result(parameters={"roe_5y_avg": value})
    assert any("parameters.roe_5y_avg" in error for error in validate_result(result))


@pytest.mark.parametrize(
    "parameters",
    [{"holding_structure": True}, {"sotp_value_mm": 100}, {"sotp_discount_pct": 10}],
)
def test_not_applicable_holding_result_rejects_contradictory_parameters(parameters):
    result = make_result("qualitative.holding_structure", parameters=parameters)
    result["run"]["status"] = "not_applicable"
    assert validate_result(result)


def test_unknown_evidence_reference_is_rejected():
    result = make_result()
    result["claims"][0]["evidence_ids"] = ["E-missing"]
    errors = validate_result(result)
    assert any("unknown evidence" in error for error in errors)


def test_forged_evidence_quote_is_rejected_against_index():
    result = make_result()
    index = make_index(
        [
            {
                "evidence_id": "E-001",
                "source_id": "market_data",
                "locator": {"section": "12"},
                "quote": "The indexed source says something else.",
            }
        ]
    )

    assert any("quote does not match" in error for error in validate_result_evidence(result, index))


def test_parameters_must_be_an_object():
    result = make_result()
    result["parameters"] = []
    assert "parameters must be an object" in validate_result(result)


def test_claim_type_must_use_protocol_value():
    result = make_result()
    result["claims"][0]["type"] = "opinion"
    assert any("claims[0].type" in error for error in validate_result(result))


def test_empty_required_objects_fail_strict_validation():
    result = make_result()
    result["run"] = {}
    result["subject"] = {}
    result["summary"] = {}
    result["quality"] = {}
    errors = validate_result(result)
    assert "run.run_id must be a non-empty string" not in errors
    assert "run_id must be a non-empty string" in errors
    assert "ticker must be a non-empty string" in errors
    assert "summary.confidence must be one of ['high', 'low', 'medium', 'unknown']" in errors
    assert "quality.completeness must be a number between 0 and 1" in errors


def test_compact_result_limits_handoff_payload():
    result = make_result()
    result["claims"] = result["claims"] * 10
    result["evidence"] = result["evidence"] * 10
    compact = compact_result(result, max_claims=2, max_evidence=3)
    assert len(compact["claims"]) == 2
    assert len(compact["evidence"]) == 1
    assert compact["claims"][0]["evidence_ids"][0] == compact["evidence"][0]["evidence_id"]


def test_chunk_text_honors_size_limit():
    chunks = chunk_text("alpha beta gamma\n" * 100, max_chars=100, overlap=10)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 100 for chunk in chunks)


def test_evidence_index_and_selection(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("brand advantage\n" * 20 + "governance risk\n" * 20, encoding="utf-8")
    index = build_evidence_index(
        [{"source_id": "annual_report", "path": str(source)}],
        chunk_chars=120,
        overlap_chars=10,
    )
    selected = select_evidence(index, keywords=["governance"], limit=2)
    assert index["schema"] == "investment.evidence_index"
    assert selected
    assert all("governance" in item["quote"] for item in selected)


def test_context_builder_selects_only_module_sections(tmp_path):
    data_pack = tmp_path / "data_pack.md"
    data_pack.write_text(
        "# Pack\n\n"
        "## 1. Basic information\nBASIC_MARKER\n"
        "## 7. Governance\nGOVERNANCE_MARKER\n"
        "## 8. Industry\nINDUSTRY_MARKER\n"
        "## 12. Metrics\nMETRIC_MARKER\n",
        encoding="utf-8",
    )
    bundle = build_module_context("business_moat", data_pack_path=data_pack, max_chars=2000)
    assert "BASIC_MARKER" in bundle["context_text"]
    assert "INDUSTRY_MARKER" in bundle["context_text"]
    assert "METRIC_MARKER" in bundle["context_text"]
    assert "GOVERNANCE_MARKER" not in bundle["context_text"]
    assert all("content" not in section for section in bundle["data_sections"])


def test_context_builder_enforces_hard_character_budget(tmp_path):
    data_pack = tmp_path / "data_pack.md"
    data_pack.write_text("## 1. Basic information\n" + "x" * 5000, encoding="utf-8")
    bundle = build_module_context("business_moat", data_pack_path=data_pack, max_chars=2000)
    assert bundle["budget"]["actual_chars"] <= 2000
    assert bundle["budget"]["actual_chars"] == len(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"
    )
    assert bundle["budget"]["truncated"] is True


def test_manifest_records_input_hash(tmp_path):
    source = tmp_path / "input.txt"
    source.write_text("stable input", encoding="utf-8")
    described = describe_input(source, source_id="market_data")
    manifest = build_manifest(
        run_id="run-1",
        subject={"ticker": "600000.SH", "company": "Example Co", "market": "CN"},
        inputs=[described],
    )
    assert manifest["inputs"][0]["exists"] is True
    assert len(manifest["inputs"][0]["sha256"]) == 64
    assert len(manifest["input_digest"]) == 64


def test_reconciliation_flags_date_and_parameter_conflicts():
    first = make_result(as_of="2024-12-31", parameters={"moat_rating": "较强"})
    second = make_result(
        "qualitative.governance",
        as_of="2025-12-31",
        parameters={"moat_rating": "弱"},
    )
    reconciliation = reconcile_results([first, second])
    conflict_types = {item["type"] for item in reconciliation["conflicts"]}
    assert "temporal" in conflict_types
    assert "parameter" in conflict_types
    assert reconciliation["requires_llm_review"] is True


def test_evidence_json_sections_keep_section_locator(tmp_path):
    source = tmp_path / "pdf_sections.json"
    source.write_text(json.dumps({"metadata": {}, "MDA": "management discussion"}), encoding="utf-8")
    index = build_evidence_index([{"source_id": "annual_report", "path": str(source)}])
    assert index["entries"][0]["section"] == "MDA"


def test_pack_evidence_keeps_heading_scope_and_units(tmp_path):
    source = tmp_path / "data_pack_report.md"
    source.write_text(
        "# Pack\n## P4. Related parties\nUnits: million\nProcurement\n"
        "## P6. Guarantees\nUnits: million\nOverdue 47.5624\n",
        encoding="utf-8",
    )
    index = build_evidence_index([{"source_id": "pdf_footnotes", "path": str(source)}])
    p6 = next(item for item in index["entries"] if item["section"] == "P6")
    assert p6["evidence_id"] == "pdf_footnotes:P6:001"
    assert p6["locator"]["section"] == "P6"
    assert "Units: million" in p6["quote"]
    assert "Procurement" not in p6["quote"]


def test_repeated_pack_headings_do_not_duplicate_evidence_ids(tmp_path):
    source = tmp_path / "data_pack_report.md"
    source.write_text("## P6. Guarantees\nCurrent period\n## P6. Guarantees\nPrior period\n", encoding="utf-8")
    index = build_evidence_index([{"source_id": "pdf_footnotes", "path": str(source)}])
    assert [item["evidence_id"] for item in index["entries"]] == ["pdf_footnotes:P6:001", "pdf_footnotes:P6:002"]


def test_repeated_policy_keywords_do_not_outweigh_topic_coverage():
    entries = [
        {"evidence_id": "policy", "quote": "risk " * 200},
        {"evidence_id": "business", "quote": "brand competition risk"},
    ]
    assert select_evidence(make_index(entries), keywords=["risk", "brand", "competition"], limit=1)[0]["evidence_id"] == "business"


@pytest.mark.parametrize("module", ["business_moat", "governance", "mda_quality"])
def test_large_market_tables_cannot_starve_pdf_or_citable_sections(tmp_path, module):
    data = tmp_path / "data_pack_market.md"
    data.write_text("".join(f"## {i}. Section\n" + "Financial row\n" * 900 for i in range(1, 18)), encoding="utf-8")
    pdf = tmp_path / "pdf_sections.json"
    pdf.write_text(json.dumps({
        key: (f"{key} source text\n" * 700)
        for key in ("MDA", "SUB", "P3", "P4", "P6", "P2", "P13", "GOV", "MATTERS")
    }), encoding="utf-8")
    notes = tmp_path / "data_pack_report.md"
    notes.write_text("## P6. Guarantees\n担保逾期金额 47.5624 百万元\n" + "## P4. Related parties\n采购总额\n", encoding="utf-8")
    index = build_evidence_index([
        {"source_id": "market_data", "path": str(data)},
        {"source_id": "pdf_sections", "path": str(pdf)},
        {"source_id": "pdf_footnotes", "path": str(notes)},
    ])
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    bundle = build_module_context(module, data_pack_path=data, pdf_sections_path=pdf, evidence_index_path=index_path)
    assert bundle["budget"]["actual_chars"] == len(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
    assert bundle["budget"]["actual_chars"] <= 24000
    assert all(item["selected_chars"] >= 500 for item in bundle["pdf_sections"])
    assert validate_result_evidence(bundle, index) == []
    coverage = bundle["selection"]["evidence_coverage"]
    assert "omitted" not in coverage.values()
    if module == "governance":
        assert coverage["pdf_footnotes:P6"] == "pdf_footnotes:P6:001"
        assert "47.5624" in next(item["quote"] for item in bundle["evidence"] if item["evidence_id"] == coverage["pdf_footnotes:P6"])
    else:
        assert coverage["market_data:3"].startswith("market_data:3:")
        assert coverage["market_data:5"].startswith("market_data:5:")
        assert coverage["pdf_sections:MDA"].startswith("pdf_sections:MDA:")
    # Metadata must describe the actual serialized excerpts after final fitting.
    for item in bundle["pdf_sections"]:
        text = bundle["context_text"].split(f"[PDF {item['section']}]\n", 1)[1]
        text = text.split("\n\n[PDF ", 1)[0]
        assert len(text) == item["selected_chars"]


def test_module_context_marks_missing_and_omitted_evidence(tmp_path):
    source = tmp_path / "pdf_sections.json"
    source.write_text(json.dumps({"MDA": "Industry", "P13": "Adjustments"}), encoding="utf-8")
    index = build_evidence_index([{"source_id": "pdf_sections", "path": str(source)}])
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    bundle = build_module_context("environment", pdf_sections_path=source, evidence_index_path=index_path, max_evidence=1)
    coverage = bundle["selection"]["evidence_coverage"]
    assert coverage["pdf_sections:P13"] == "omitted"
    assert coverage["market_data:3"] == "missing"
    assert bundle["budget"]["truncated"] is True


def test_prepare_run_creates_standard_workspace(tmp_path):
    output_dir = tmp_path / "stock"
    (output_dir).mkdir()
    (output_dir / "data_pack_market.md").write_text(
        "## 1. Basic information\nBASIC\n## 12. Metrics\nMETRICS\n",
        encoding="utf-8",
    )
    result = prepare_run(output_dir, ticker="600000.SH", company="Example Co")
    assert (output_dir / "run_manifest.json").exists()
    assert (output_dir / "evidence/index.json").exists()
    assert (output_dir / "contexts/business_moat.json").exists()
    assert (output_dir / "modules/business_moat").is_dir()
    assert (output_dir / "modules/holding_structure").is_dir()
    assert result["contexts"]["governance"].endswith("contexts/governance.json")
    context = json.loads((output_dir / "contexts/business_moat.json").read_text(encoding="utf-8"))
    assert context["run"]["run_id"]
    assert context["subject"]["ticker"] == "600000.SH"
    manifest = json.loads((output_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert all(len(artifact["sha256"]) == 64 for artifact in manifest["artifacts"])


def test_prepare_run_generates_unique_default_run_ids(tmp_path):
    first = prepare_run(tmp_path / "first", ticker="600000.SH", company="Example Co")
    second = prepare_run(tmp_path / "second", ticker="600000.SH", company="Example Co")
    first_manifest = json.loads((tmp_path / "first" / "run_manifest.json").read_text(encoding="utf-8"))
    second_manifest = json.loads((tmp_path / "second" / "run_manifest.json").read_text(encoding="utf-8"))

    assert first["run_manifest"] != second["run_manifest"]
    assert first_manifest["run_id"] != second_manifest["run_id"]


def test_prepare_run_records_d6_routing_decision(tmp_path):
    output_dir = tmp_path / "stock"
    output_dir.mkdir()
    (output_dir / "data_pack_market.md").write_text(
        "## 1. Basic information\n普通运营公司\n## 9. Business\n主营业务\n",
        encoding="utf-8",
    )
    result = prepare_run(output_dir, ticker="600000.SH", company="Example Co")
    assert result["d6_triggered"] is False
    trigger = json.loads((output_dir / "d6_trigger.json").read_text(encoding="utf-8"))
    assert trigger["triggered"] is False


def test_prepare_run_marks_d6_unknown_without_market_data(tmp_path):
    output_dir = tmp_path / "stock"
    result = prepare_run(output_dir, ticker="600000.SH", company="Example Co")
    trigger = json.loads((output_dir / "d6_trigger.json").read_text(encoding="utf-8"))

    assert result["d6_triggered"] is None
    assert trigger["status"] == "unknown"


def test_synthesis_context_is_bounded_and_deduplicates_evidence(tmp_path):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(make_result(), ensure_ascii=False), encoding="utf-8")
    second_result_path = tmp_path / "second_result.json"
    second_result_path.write_text(
        json.dumps(make_result("qualitative.governance"), ensure_ascii=False),
        encoding="utf-8",
    )
    reconciliation_path = tmp_path / "reconciliation.json"
    reconciliation_path.write_text(
        json.dumps(reconcile_results([make_result(), make_result("qualitative.governance")])),
        encoding="utf-8",
    )
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        json.dumps(
            make_index(
                [
                    {
                        "evidence_id": "E-001",
                        "source_id": "market_data",
                        "locator": {"section": "12"},
                        "quote": "Five-year ROE remained stable." * 30,
                    }
                ]
            )
        ),
        encoding="utf-8",
    )
    context = build_synthesis_context(
        [result_path],
        optional_result_paths=(path for path in (second_result_path, tmp_path / "missing.json")),
        reconciliation_path=reconciliation_path,
        evidence_index_path=evidence_path,
        max_chars=8000,
    )
    assert context["budget"]["actual_chars"] <= 8000
    assert len(context["modules"]) == 2
    assert context["missing_modules"] == [str(tmp_path / "missing.json")]
    assert len(context["evidence"]) == 1
    assert len(context["evidence"][0]["quote"]) <= 300
    assert validate_result_evidence(context, json.loads(evidence_path.read_text(encoding="utf-8"))) == []
    serialized = json.dumps(context, ensure_ascii=False, indent=2) + "\n"
    assert context["budget"]["actual_chars"] == len(serialized)


def test_synthesis_selects_evidence_from_retained_cards(tmp_path):
    first = make_result()
    first["claims"] = []
    first["evidence"] = []
    entries = []
    for index in range(30):
        evidence_id = f"E-{index:03d}"
        first["claims"].append(
            {
                "claim_id": f"C-{index:03d}",
                "statement": f"Claim {index}",
                "type": "fact",
                "confidence": "medium",
                "evidence_ids": [evidence_id],
            }
        )
        evidence = {
            "evidence_id": evidence_id,
            "source_id": "market_data",
            "locator": {"section": str(index)},
            "quote": f"Evidence {index}",
        }
        first["evidence"].append(evidence)
        entries.append(evidence)

    second = make_result("qualitative.governance")
    second["claims"][0]["evidence_ids"] = ["E-final"]
    second["evidence"][0]["evidence_id"] = "E-final"
    entries.append(second["evidence"][0])

    paths = []
    for name, result in (("first", first), ("second", second)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        paths.append(path)
    reconciliation_path = tmp_path / "reconciliation.json"
    reconciliation_path.write_text(
        json.dumps(reconcile_results([first, second])), encoding="utf-8"
    )
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(make_index(entries)), encoding="utf-8")

    context = build_synthesis_context(
        paths,
        reconciliation_path=reconciliation_path,
        evidence_index_path=evidence_path,
    )

    assert "E-final" in {item["evidence_id"] for item in context["evidence"]}


def _synthesis_fixture(tmp_path, results, entries=None):
    paths = []
    for result in results:
        path = tmp_path / f"{result['result_type']}.json"
        path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        paths.append(path)
    reconciliation = tmp_path / "reconciliation.json"
    reconciliation.write_text(json.dumps(reconcile_results(results), ensure_ascii=False), encoding="utf-8")
    index = tmp_path / "index.json"
    if entries is None:
        entries = list({item["evidence_id"]: item for result in results for item in result["evidence"]}.values())
    index.write_text(json.dumps(make_index(entries), ensure_ascii=False), encoding="utf-8")
    return {"result_paths": paths, "reconciliation_path": reconciliation, "evidence_index_path": index}


def test_synthesis_preserves_module_excerpts_past_chunk_prefix_and_shared_alternatives(tmp_path):
    first = make_result()
    second = make_result("qualitative.governance")
    first["evidence"][0]["quote"] = "Guarantees total 5732.9531; not a recognized loss."
    second["evidence"][0]["quote"] = "Overdue amount 47.5624; recovery remains unverified."
    entry = {**first["evidence"][0], "quote": "Unrelated accounting policy. " * 30 + first["evidence"][0]["quote"] + "\n" + second["evidence"][0]["quote"]}
    args = _synthesis_fixture(tmp_path, [first, second], [entry])
    context = build_synthesis_context(**args)
    assert len(context["evidence"]) == 1
    evidence = context["evidence"][0]
    assert evidence["quote"] == first["evidence"][0]["quote"]
    assert evidence["alternative_quotes"] == [second["evidence"][0]["quote"]]
    assert context["modules"][0]["evidence"][0]["quote_index"] == 0
    assert context["modules"][1]["evidence"][0]["quote_index"] == 1
    assert all(quote in entry["quote"] for quote in [evidence["quote"], *evidence["alternative_quotes"]])


def test_synthesis_budget_covers_metrics_quality_and_preserves_protected_content(tmp_path):
    results = []
    for result_type in DEFAULT_PARAMETERS:
        result = make_result(result_type)
        result["run"]["status"] = "partial"
        result["parameters"]["long_parameter"] = "Verified parameter; " * 50
        result["metrics"] = {
            "basis": {"currency": "CNY", "unit": "million", "as_of": "2025-12-31"},
            "oversized": {"series": list(range(3000))},
            "guarantee": {"value": 5732.9531, "unit": "million", "period": "2025", "evidence_ids": ["E-001"]},
            "receivables": 47.5624,
        }
        result["risks"] = [{"risk": "Material credit exposure", "severity": "high", "evidence_ids": ["E-001"]}]
        for key in ("missing_inputs", "warnings", "unresolved_questions"):
            result["quality"][key] = [f"{key} {i}: " + "Further verification needed. " * 20 for i in range(30)]
        result["quality"]["extra_detail"] = "Unbounded quality extension. " * 3000
        results.append(result)
    args = _synthesis_fixture(tmp_path, results)
    context = build_synthesis_context(**args)
    assert context == build_synthesis_context(**args)
    assert context["budget"]["actual_chars"] == len(json.dumps(context, ensure_ascii=False, indent=2) + "\n")
    assert context["budget"]["actual_chars"] <= 40000
    # 预算不足时必须留下丢弃记录：条数是完整的，字段名只留样本（REQ-006.1 AC-1.5）
    assert context["budget"]["dropped_count"] > 0
    assert context["budget"]["dropped"], "丢弃的模块名必须列出来"
    assert all(len(fields) <= 3 for fields in context["budget"]["dropped"].values())
    assert context["budget"]["dropped_count"] >= sum(
        len(fields) for fields in context["budget"]["dropped"].values()
    )
    assert context["reconciliation"] == json.loads(args["reconciliation_path"].read_text(encoding="utf-8"))
    assert context["upstream_digest"] == result_set_digest(results)
    for card, result in zip(context["modules"], results):
        assert card["parameters"] == result["parameters"]
        assert card["risks"] == result["risks"]
        assert card["claims"][0] == result["claims"][0]
        assert card["run"] == result["run"]
        assert card["metrics"]["guarantee"] == result["metrics"]["guarantee"]
        assert card["metrics"]["basis"] == result["metrics"]["basis"]
        assert card["quality"]["completeness"] == result["quality"]["completeness"]
        assert card["omitted"]["metrics"] >= 1
        assert card["omitted"]["quality"]["warnings"] > 0
        assert card["omitted"]["quality"]["extra_detail"] == 1


def test_synthesis_keeps_evidence_dependencies_of_risks_and_metrics(tmp_path):
    result = make_result()
    result["evidence"].extend([
        {**result["evidence"][0], "evidence_id": "risk", "quote": "Actual risk finding"},
        {**result["evidence"][0], "evidence_id": "metric", "quote": "Amount 47.5624 million"},
    ])
    result["risks"] = [{"risk": "Risk without claim reference", "severity": "medium", "evidence_ids": ["risk"]}]
    result["metrics"]["overdue"] = {"value": 47.5624, "unit": "million", "evidence_ids": ["metric"]}
    context = build_synthesis_context(**_synthesis_fixture(tmp_path, [result]))
    assert {item["evidence_id"] for item in context["evidence"]} == {"E-001", "risk", "metric"}
    assert context["modules"][0]["risks"] == result["risks"]


def test_synthesis_refuses_to_discard_protected_parameters(tmp_path):
    result = make_result(parameters={"large_parameter": "Verified parameter " * 2000})
    with pytest.raises(ValueError, match="protected synthesis content"):
        build_synthesis_context(**_synthesis_fixture(tmp_path, [result]))


def test_synthesis_rejects_risk_reference_without_precise_module_excerpt(tmp_path):
    result = make_result()
    result["risks"] = [{"risk": "Requires evidence", "severity": "high", "evidence_ids": ["uncited"]}]
    with pytest.raises(ValueError, match="without a module excerpt"):
        build_synthesis_context(**_synthesis_fixture(tmp_path, [result]))


@pytest.mark.parametrize("corruption", [None, "quote", "locator", "source_id", "run", "subject", "malformed"])
def test_validator_cli_checks_frozen_index(tmp_path, monkeypatch, capsys, corruption):
    result = make_result()
    args = _synthesis_fixture(tmp_path, [result])
    index = json.loads(args["evidence_index_path"].read_text(encoding="utf-8"))
    if corruption in {"quote", "locator", "source_id"}:
        index["entries"][0][corruption] = "Incorrect"
    elif corruption == "run":
        index["run"] = {"run_id": "another-run"}
    elif corruption == "subject":
        index["subject"]["ticker"] = "000858.SZ"
    elif corruption == "malformed":
        index = []
    args["evidence_index_path"].write_text(json.dumps(index), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["validate_result", str(args["result_paths"][0]), "--evidence-index", str(args["evidence_index_path"])])
    if corruption:
        with pytest.raises(SystemExit) as exc:
            validate_main()
        assert exc.value.code == 1
        assert "Invalid result evidence" in capsys.readouterr().err
    else:
        validate_main()
        assert "Valid result" in capsys.readouterr().out


def test_missing_optional_result_requires_llm_review(tmp_path, monkeypatch):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(make_result(), ensure_ascii=False), encoding="utf-8")
    output_path = tmp_path / "reconciliation.json"
    missing_path = tmp_path / "optional.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reconcile_results",
            "--input",
            str(result_path),
            "--optional-input",
            str(missing_path),
            "--output",
            str(output_path),
        ],
    )

    reconcile_main()

    reconciliation = json.loads(output_path.read_text(encoding="utf-8"))
    assert reconciliation["requires_llm_review"] is True
    assert reconciliation["missing_information"] == [
        {"path": str(missing_path), "status": "not_applicable_or_not_produced"}
    ]


def _write_core_results(output_dir, *, ticker="600000.SH", run_id="test-run"):
    result_types = {
        "business_moat": "qualitative.business_moat",
        "environment": "qualitative.environment",
        "governance": "qualitative.governance",
        "mda_quality": "qualitative.mda_quality",
    }
    for index, (module, result_type) in enumerate(result_types.items()):
        result = make_result(
            result_type,
            parameters={f"parameter_{index}": index},
            ticker=ticker,
            run_id=run_id,
        )
        _use_prepared_evidence(result, output_dir)
        path = output_dir / "modules" / module / "result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def _use_prepared_evidence(result, output_dir):
    index_path = output_dir / "evidence" / "index.json"
    if not index_path.exists():
        return
    entries = json.loads(index_path.read_text(encoding="utf-8"))["entries"]
    if not entries:
        return
    entry = entries[0]
    result["evidence"] = [
        {
            key: entry[key]
            for key in ("evidence_id", "source_id", "locator", "quote", "content_hash")
        }
    ]
    for claim in result["claims"]:
        claim["evidence_ids"] = [entry["evidence_id"]]


def _write_synthesis_artifacts(output_dir, *, ticker="600000.SH"):
    module_names = ["business_moat", "environment", "governance", "mda_quality"]
    for optional in ("holding_structure", "period_delta"):
        if (output_dir / "modules" / optional / "result.json").exists():
            module_names.append(optional)
    module_results = [
        json.loads((output_dir / "modules" / module / "result.json").read_text(encoding="utf-8"))
        for module in module_names
    ]
    reconciliation = reconcile_results(module_results)
    reconciliation_path = output_dir / "synthesis" / "reconciliation.json"
    reconciliation_path.write_text(json.dumps(reconciliation, ensure_ascii=False), encoding="utf-8")
    synthesis = make_result(
        "qualitative.synthesis",
        ticker=ticker,
        run_id="test-run",
        upstream_digest=result_set_digest(module_results),
    )
    _use_prepared_evidence(synthesis, output_dir)
    (output_dir / "synthesis" / "result.json").write_text(
        json.dumps(synthesis, ensure_ascii=False), encoding="utf-8"
    )


def _write_complete_structured_run(output_dir, *, ticker="600000.SH"):
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "data_pack_market.md").write_text(
        "## 1. Basic information\nExample\n## 12. Metrics\nStable\n",
        encoding="utf-8",
    )
    prepare_run(
        output_dir,
        ticker=ticker,
        company="Example Co",
        run_id="test-run",
    )
    _write_core_results(output_dir, ticker=ticker, run_id="test-run")
    _write_synthesis_artifacts(output_dir, ticker=ticker)


def test_qualitative_resolver_prefers_complete_structured_set(tmp_path):
    output_dir = tmp_path / "600000_Example"
    _write_complete_structured_run(output_dir)
    (output_dir / "qualitative_report.md").write_text("legacy", encoding="utf-8")

    payload = resolve_qualitative_input(output_dir, ticker="600000")

    assert payload["source"] == "structured"
    assert len(payload["modules"]) == 4
    assert payload["parameters"]["parameter_3"] == 3
    assert payload["legacy_report"] is None
    assert payload["synthesis"]["result_type"] == "qualitative.synthesis"


def test_qualitative_resolver_falls_back_atomically_to_legacy(tmp_path):
    output_dir = tmp_path / "600000_Example"
    output_dir.mkdir()
    _write_core_results(output_dir)
    (output_dir / "modules/governance/result.json").unlink()
    (output_dir / "qualitative_report.md").write_text("legacy", encoding="utf-8")

    payload = resolve_qualitative_input(output_dir, ticker="600000.SH")

    assert payload["source"] == "legacy"
    assert payload["modules"] == {}
    assert payload["parameters"] == {}
    assert any("governance" in warning for warning in payload["warnings"])


def test_qualitative_resolver_rejects_mismatched_ticker(tmp_path):
    output_dir = tmp_path / "600000_Example"
    _write_complete_structured_run(output_dir)
    _write_core_results(output_dir, ticker="000858.SZ")

    payload = resolve_qualitative_input(output_dir, ticker="600000.SH")

    assert payload["source"] == "unavailable"
    assert any("expected '600000.SH'" in warning for warning in payload["warnings"])


def test_qualitative_resolver_surfaces_partial_and_optional_status(tmp_path):
    output_dir = tmp_path / "600000_Example"
    _write_complete_structured_run(output_dir)
    environment_path = output_dir / "modules/environment/result.json"
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment["run"]["status"] = "partial"
    environment_path.write_text(json.dumps(environment, ensure_ascii=False), encoding="utf-8")

    holding = make_result("qualitative.holding_structure")
    holding["run"]["status"] = "not_applicable"
    _use_prepared_evidence(holding, output_dir)
    holding_path = output_dir / "modules/holding_structure/result.json"
    holding_path.parent.mkdir(parents=True, exist_ok=True)
    holding_path.write_text(json.dumps(holding, ensure_ascii=False), encoding="utf-8")
    _write_synthesis_artifacts(output_dir)

    payload = resolve_qualitative_input(output_dir, ticker="600000.SH")

    assert payload["source"] == "structured"
    assert payload["parameters"]["holding_structure"] is False
    # D7 是登记过的可选模块（REQ-006.1 AC-1.4），本夹具没有提供它，所以它应当出现在 missing 里
    assert payload["missing_modules"] == ["period_delta"]
    assert "environment result is partial" in payload["warnings"]


def test_qualitative_resolver_rejects_results_from_previous_manifest_run(tmp_path):
    output_dir = tmp_path / "600000_Example"
    prepare_run(
        output_dir,
        ticker="600000.SH",
        company="Example Co",
        run_id="current-run",
    )
    _write_core_results(output_dir)
    (output_dir / "qualitative_report.md").write_text("legacy", encoding="utf-8")

    payload = resolve_qualitative_input(output_dir, ticker="600000.SH")

    assert payload["source"] == "unavailable"
    assert payload["modules"] == {}
    assert any("expected 'current-run'" in warning for warning in payload["warnings"])


def test_qualitative_resolver_rejects_changed_manifest_input(tmp_path):
    output_dir = tmp_path / "600000_Example"
    _write_complete_structured_run(output_dir)
    (output_dir / "data_pack_market.md").write_text("changed after analysis", encoding="utf-8")

    payload = resolve_qualitative_input(output_dir, ticker="600000.SH")

    assert payload["source"] == "unavailable"
    assert any("input hash changed" in warning for warning in payload["warnings"])


def test_qualitative_resolver_rejects_input_created_after_preparation(tmp_path):
    output_dir = tmp_path / "600000_Example"
    _write_complete_structured_run(output_dir)
    (output_dir / "data_pack_report.md").write_text("late footnotes", encoding="utf-8")

    payload = resolve_qualitative_input(output_dir, ticker="600000.SH")

    assert payload["source"] == "unavailable"
    assert any("input changed existence" in warning for warning in payload["warnings"])


def test_market_refresh_copy_does_not_invalidate_qualitative_evidence(tmp_path):
    output_dir = tmp_path / "600000_Example"
    _write_complete_structured_run(output_dir)
    original_path = output_dir / "data_pack_market.md"
    original = original_path.read_bytes()
    assert resolve_qualitative_input(output_dir, ticker="600000.SH")["source"] == "structured"

    (output_dir / "data_pack_market_current.md").write_text("refreshed market prices", encoding="utf-8")

    assert original_path.read_bytes() == original
    assert resolve_qualitative_input(output_dir, ticker="600000.SH")["source"] == "structured"


def test_prepared_paths_are_independent_of_consumer_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_complete_structured_run(Path("600000_Example"))
    manifest = json.loads((tmp_path / "600000_Example/run_manifest.json").read_text(encoding="utf-8"))
    assert all(Path(item["path"]).is_absolute() for item in manifest["inputs"])
    assert all(Path(item["path"]).is_absolute() for item in manifest["artifacts"])

    monkeypatch.chdir(tmp_path.parent)
    payload = resolve_qualitative_input(tmp_path / "600000_Example", ticker="600000.SH")

    assert payload["source"] == "structured"


@pytest.mark.parametrize("artifact", ["run_manifest.json", "evidence/index.json", "synthesis/reconciliation.json"])
@pytest.mark.parametrize("malformation", ["array", "invalid_run"])
def test_resolver_rejects_malformed_artifacts_without_traceback(tmp_path, artifact, malformation):
    output_dir = tmp_path / "600000_Example"
    _write_complete_structured_run(output_dir)
    path = output_dir / artifact
    payload = json.loads(path.read_text(encoding="utf-8"))
    if malformation == "array":
        payload = []
    else:
        payload["run_id" if artifact == "run_manifest.json" else "run"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")

    resolved = resolve_qualitative_input(output_dir, ticker="600000.SH")

    assert resolved["source"] == "unavailable"
    assert resolved["warnings"]


def test_qualitative_resolver_rejects_changed_prepared_artifact(tmp_path):
    output_dir = tmp_path / "600000_Example"
    _write_complete_structured_run(output_dir)
    index_path = output_dir / "evidence" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["entries"][0]["quote"] = "tampered evidence"
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    payload = resolve_qualitative_input(output_dir, ticker="600000.SH")

    assert payload["source"] == "unavailable"
    assert any("artifact hash changed" in warning for warning in payload["warnings"])


def test_qualitative_resolver_rejects_stale_reconciliation_digest(tmp_path):
    output_dir = tmp_path / "600000_Example"
    _write_complete_structured_run(output_dir)
    result_path = output_dir / "modules" / "environment" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["parameters"]["regulatory_risk"] = "高"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    payload = resolve_qualitative_input(output_dir, ticker="600000.SH")

    assert payload["source"] == "unavailable"
    assert any("reconciliation result_digest" in warning for warning in payload["warnings"])


def test_qualitative_resolver_accepts_equivalent_class_share_ticker(tmp_path):
    output_dir = tmp_path / "BRK.B_Berkshire"
    _write_complete_structured_run(output_dir, ticker="BRK.B.US")

    payload = resolve_qualitative_input(output_dir, ticker="brk-b")

    assert payload["source"] == "structured"
    assert payload["subject"]["ticker"] == "BRK.B.US"


def test_legacy_fallback_requires_matching_directory_ticker(tmp_path):
    output_dir = tmp_path / "000858_Other"
    output_dir.mkdir()
    (output_dir / "qualitative_report.md").write_text("legacy", encoding="utf-8")

    payload = resolve_qualitative_input(output_dir, ticker="600000.SH")

    assert payload["source"] == "unavailable"
    assert any("legacy directory ticker" in warning for warning in payload["warnings"])


def test_resolver_cli_uses_distinct_unavailable_exit_status(tmp_path, monkeypatch):
    output_dir = tmp_path / "600000_Example"
    output_dir.mkdir()
    output_path = output_dir / "qualitative_input.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "resolve_qualitative",
            "--output-dir",
            str(output_dir),
            "--ticker",
            "600000.SH",
            "--output",
            str(output_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        resolve_main()

    assert exc_info.value.code == 3
    assert json.loads(output_path.read_text(encoding="utf-8"))["source"] == "unavailable"


def test_synthesis_rejects_mixed_subjects(tmp_path):
    first_path = tmp_path / "first.json"
    first_path.write_text(json.dumps(make_result(), ensure_ascii=False), encoding="utf-8")
    second = make_result("qualitative.governance")
    second["subject"]["ticker"] = "000858.SZ"
    second_path = tmp_path / "second.json"
    second_path.write_text(json.dumps(second, ensure_ascii=False), encoding="utf-8")
    reconciliation_path = tmp_path / "reconciliation.json"
    reconciliation_path.write_text(
        json.dumps(reconcile_results([make_result(), make_result("qualitative.governance")])),
        encoding="utf-8",
    )
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(make_index([])), encoding="utf-8")

    with pytest.raises(ResultValidationError, match="subject.ticker"):
        build_synthesis_context(
            [first_path, second_path],
            reconciliation_path=reconciliation_path,
            evidence_index_path=evidence_path,
        )


# --- REQ-006.1：实跑暴露的接线与预算问题 ---


def _write_period_delta(output_dir, *, ticker="600000.SH"):
    delta = make_result("qualitative.period_delta", ticker=ticker, run_id="test-run")
    _use_prepared_evidence(delta, output_dir)
    path = output_dir / "modules" / "period_delta" / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(delta, ensure_ascii=False), encoding="utf-8")
    return delta


def test_qualitative_resolver_consumes_period_delta_without_digest_mismatch(tmp_path):
    """AC-1.4：D7 登记为可选模块后，文档口径（把 D7 一起喂给 reconcile/synthesis）不再分叉。

    回归（2026-09-20 实跑）：resolver 之前不认 period_delta，一按文档喂进去就
    `reconciliation result_digest does not match module results`、source=unavailable。
    """
    output_dir = tmp_path / "600000_Example"
    _write_complete_structured_run(output_dir)
    delta = _write_period_delta(output_dir)
    _write_synthesis_artifacts(output_dir)  # 夹具按文档口径把 D7 一起算进 digest

    payload = resolve_qualitative_input(output_dir, ticker="600000.SH")

    assert payload["source"] == "structured", payload["warnings"]
    assert "period_delta" in payload["modules"]
    assert payload["missing_modules"] == ["holding_structure"]
    module_results = [
        json.loads((output_dir / "modules" / module / "result.json").read_text(encoding="utf-8"))
        for module in ("business_moat", "environment", "governance", "mda_quality", "period_delta")
    ]
    assert payload["reconciliation"] is not None
    assert result_set_digest(module_results) == json.loads(
        (output_dir / "synthesis" / "reconciliation.json").read_text(encoding="utf-8")
    )["result_digest"]
    assert delta["result_type"] == "qualitative.period_delta"


def test_period_delta_absent_keeps_the_legacy_digest(tmp_path):
    """AC-1.4 的向后兼容面：没有 D7 时 digest 与既有基线一致（仍然只有核心四模块）。"""
    output_dir = tmp_path / "600000_Example"
    _write_complete_structured_run(output_dir)
    payload = resolve_qualitative_input(output_dir, ticker="600000.SH")
    assert payload["source"] == "structured"
    assert "period_delta" not in payload["modules"]
    assert payload["missing_modules"] == ["holding_structure", "period_delta"]


def _fake_run_json(output_dir, run_id):
    """模拟 `runs.py new` 在运行目录里留下的 run.json（prepare 以它为准）。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "run.json").write_text(
        json.dumps({"schema": "investment.run", "run_id": run_id}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_prepare_run_rejects_a_run_id_that_conflicts_with_run_json(tmp_path):
    """AC-1.3：台账 id 与 run 内部 id 不允许分叉（实跑里靠人肉补 --run-id 才自洽）。"""
    output_dir = tmp_path / "run"
    _fake_run_json(output_dir, "run-a")
    with pytest.raises(ValueError, match="run_id"):
        prepare_run(output_dir, ticker="600000.SH", company="Example Co", run_id="run-b")


def test_prepare_run_adopts_the_run_directories_own_run_id(tmp_path):
    """AC-1.3：run.json 已签发 id 时，prepare 不再另生成一个。"""
    output_dir = tmp_path / "run"
    _fake_run_json(output_dir, "run-a")
    prepare_run(output_dir, ticker="600000.SH", company="Example Co")
    recorded = json.loads((output_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert recorded["run_id"] == "run-a", "prepare 必须沿用 run.json 里已签发的 id"


def test_synthesis_context_discloses_dropped_content(tmp_path):
    """AC-1.5：预算装不下时必须列出被丢的模块与字段，不得静默。"""
    results = [make_result("qualitative.business_moat"), make_result("qualitative.governance")]
    for result in results:
        result["quality"]["extra_detail"] = "Unbounded quality extension. " * 500
    args = _synthesis_fixture(tmp_path, results)
    full = build_synthesis_context(**args)
    assert full["budget"]["dropped_count"] == 0
    assert full["budget"]["dropped"] == {}

    tight = build_synthesis_context(**{**args, "max_chars": full["budget"]["actual_chars"] - 500})
    assert tight["budget"]["actual_chars"] <= full["budget"]["actual_chars"] - 500
    assert tight["budget"]["dropped_count"] > 0
    assert tight["budget"]["dropped"], "被丢的模块名必须列出来"


def test_bundle_evidence_checker_rejects_out_of_bundle_citations(tmp_path):
    """AC-1.7：模块结果引用 bundle 之外的证据必须能被检出（实跑观察：大量越界引用）。"""
    bundle = {"evidence": [{"evidence_id": "market_data:3:001", "quote": "q"}]}
    inside = make_result()
    inside["evidence"] = [{"evidence_id": "market_data:3:001"}]
    inside["claims"][0]["evidence_ids"] = ["market_data:3:001"]
    assert bundle_evidence_ids(bundle) == {"market_data:3:001"}
    assert validate_bundle_evidence(inside, bundle) == []

    outside = make_result()
    outside["evidence"] = [{"evidence_id": "pdf_sections:MDA:009"}]
    outside["claims"][0]["evidence_ids"] = ["pdf_sections:MDA:009"]
    problems = validate_bundle_evidence(outside, bundle)
    assert problems and "outside this module's context bundle" in problems[0]


def test_bundle_evidence_checker_flags_a_bundle_without_evidence():
    problems = validate_bundle_evidence(make_result(), {"evidence": []})
    assert problems == ["context bundle contains no evidence ids"]


def test_validate_result_cli_checks_the_bundle(tmp_path, capsys):
    """AC-1.7 的可执行入口：`validate_result.py --context <bundle>`。"""
    result = make_result()
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    bundle_path = tmp_path / "context.json"
    bundle_path.write_text(
        json.dumps({"evidence": [{"evidence_id": "market_data:3:999"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as excinfo:
        validate_main_args = [str(result_path), "--context", str(bundle_path)]
        sys.argv = ["validate_result.py", *validate_main_args]
        validate_main()
    assert excinfo.value.code == 1
    assert "outside this module's context bundle" in capsys.readouterr().err
