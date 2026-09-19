"""Contract and context tests for the qualitative.period_delta module."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from results.context import MODULE_CONFIG, build_module_context
from results.schema import RESULT_TYPE_CONTRACTS, validate_result

ROOT = Path(__file__).resolve().parents[1]

SUBJECT = {"ticker": "600887.SH", "company": "伊利股份", "market": "CN"}
RUN_ID = "20260919T221000000000Z"


def _period_delta_result(evidence_ids=("market_data:3:001",), *, status="complete", **parameter_overrides):
    parameters = {
        "report_period": "2026H1",
        "comparable_period": "2025H1",
        "business_trend": "稳定",
        "conclusion_change": "维持",
        "change_significance": "一般",
        "guidance_delivery": "兑现",
        "requires_full_rerun": False,
        "revenue_yoy_pct": 8.3,
        "net_profit_yoy_pct": 5.1,
        "gross_margin_change_pct": -0.4,
        "operating_cashflow_to_profit": 1.12,
    }
    parameters.update(parameter_overrides)
    if status != "complete":
        for key in list(parameters):
            if parameters[key] is None:
                parameters.pop(key)
    evidence = [
        {
            "evidence_id": evidence_id,
            "source_id": "market_data",
            "locator": {"path": "market_data.md", "section": "3", "chunk": 1},
            "quote": "营业收入 64,330.94 百万元（2026H1）",
        }
        for evidence_id in evidence_ids
    ]
    return {
        "schema": "investment.result",
        "schema_version": "1.0",
        "result_type": "qualitative.period_delta",
        "run": {"run_id": RUN_ID, "generated_at": "2026-09-19T22:00:00Z", "as_of": "2026-08-30", "status": status},
        "subject": dict(SUBJECT),
        "scope": ["D7"],
        "summary": {"thesis": "本期收入同比小幅增长，毛利率承压。", "decision_relevance": "维持长期判断。", "confidence": "medium"},
        "parameters": parameters,
        "metrics": {"report_period": "2026H1", "comparable_period": "2025H1", "revenue_yoy_pct": 8.3},
        "claims": [
            {
                "claim_id": "period_delta-001",
                "statement": "收入同比增长但毛利率下降。",
                "type": "judgement",
                "confidence": "medium",
                "evidence_ids": list(evidence_ids),
            }
        ],
        "risks": [{"risk": "毛利率继续下行", "severity": "medium", "monitoring_indicator": "单季毛利率"}],
        "watchlist": ["下一期单季毛利率"],
        "evidence": evidence,
        "quality": {"completeness": 0.8, "missing_inputs": [], "warnings": [], "unresolved_questions": []},
    }


class TestPeriodDeltaContract:
    def test_contract_registered(self):
        contract = RESULT_TYPE_CONTRACTS["qualitative.period_delta"]
        assert contract["scope"] == ["D7"]
        assert set(contract["parameters"]) == {
            "report_period",
            "comparable_period",
            "business_trend",
            "conclusion_change",
            "change_significance",
            "guidance_delivery",
            "requires_full_rerun",
            "revenue_yoy_pct",
            "net_profit_yoy_pct",
            "gross_margin_change_pct",
            "operating_cashflow_to_profit",
        }

    def test_complete_result_validates(self):
        assert validate_result(_period_delta_result()) == []

    def test_invalid_enum_is_rejected(self):
        errors = validate_result(_period_delta_result(business_trend="大幅改善"))
        assert any("business_trend" in error for error in errors)

    def test_missing_required_parameter_is_rejected(self):
        result = _period_delta_result()
        del result["parameters"]["conclusion_change"]
        errors = validate_result(result)
        assert any("conclusion_change" in error for error in errors)

    def test_nullable_metric_accepts_null(self):
        assert validate_result(_period_delta_result(revenue_yoy_pct=None)) == []

    def test_wrong_scope_is_rejected(self):
        result = _period_delta_result()
        result["scope"] = ["D5"]
        assert any("scope" in error for error in validate_result(result))

    def test_module_prompt_exists_and_names_parameters(self):
        prompt = (ROOT / "shared/qualitative/agents/modules/period_delta.md").read_text(encoding="utf-8")
        for parameter in RESULT_TYPE_CONTRACTS["qualitative.period_delta"]["parameters"]:
            assert parameter in prompt, parameter
        assert "prior_analysis" in prompt

    def test_change_report_prompt_exists(self):
        prompt = (ROOT / "shared/qualitative/agents/change_report.md").read_text(encoding="utf-8")
        assert "change_report_{period}.md" in prompt
        assert "investment.change_report" in prompt
        assert "本期未披露" in prompt

    def test_coordinator_update_exists_and_wires_the_flow(self):
        content = (ROOT / "shared/qualitative/coordinator_update.md").read_text(encoding="utf-8")
        for needle in (
            "analysis_status.py",
            "download_report.py",
            "runs.py new",
            "prepare",
            "--primary-period",
            "--prior-analysis",
            "period_delta",
            "change_report",
            "runs.py finish",
        ):
            assert needle in content, needle

    def test_update_command_documented_in_both_dirs(self):
        for command_dir in (".claude/commands", ".opencode/commands"):
            content = (ROOT / command_dir / "update-analysis.md").read_text(encoding="utf-8")
            assert "$ARGUMENTS" in content
            assert "scripts/analysis_status.py" in content
            assert "scripts.results.change_report" in content
            assert "period_delta" in content


class TestPeriodDeltaContext:
    def _index(self):
        def entry(evidence_id, source_id, section, quote):
            return {
                "evidence_id": evidence_id,
                "source_id": source_id,
                "section": section,
                "chunk_number": 1,
                "quote": quote,
                "locator": {"path": f"{source_id}.md", "section": section, "chunk": 1},
                "content_hash": "hash-" + evidence_id,
            }

        return {
            "schema": "investment.evidence_index",
            "schema_version": "1.0",
            "run": {"run_id": RUN_ID},
            "subject": dict(SUBJECT),
            "entries": [
                entry("market_data:3:001", "market_data", "3", "营业收入 64,330.94 百万元（2026H1）"),
                entry("market_data:3P:001", "market_data", "3P", "母公司营业收入 58,752.72 百万元"),
                entry("market_data:5:001", "market_data", "5", "经营活动现金流净额 6,000 百万元"),
                entry("market_data:17:001", "market_data", "17", "单季拆分与同比衍生指标"),
                entry("pdf_sections:MDA:001", "pdf_sections", "MDA", "本期毛利率下降主要由于原奶价格波动"),
                entry("prior_analysis:summary:001", "prior_analysis", "summary", "上次结论：护城河维持，关注毛利率"),
                entry("prior_analysis:parameters:001", "prior_analysis", "parameters", "moat_rating: 强"),
                entry("prior_analysis:claims:001", "prior_analysis", "claims", "上次判断：护城河稳固"),
                entry("prior_analysis:risks:001", "prior_analysis", "risks", "上次风险：原奶价格波动"),
                entry("prior_analysis:watchlist:001", "prior_analysis", "watchlist", "下一期单季毛利率"),
                entry("prior_analysis:quality:001", "prior_analysis", "quality", "上次质量：completeness 0.9"),
            ],
        }

    def _write(self, tmp_path):
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(self._index(), ensure_ascii=False), encoding="utf-8")
        return index_path

    def test_module_config_declares_prior_analysis(self):
        config = MODULE_CONFIG["period_delta"]
        assert config["scope"] == ["D7"]
        assert config["prior_analysis"]

    def test_context_selects_prior_analysis_evidence(self, tmp_path):
        index_path = self._write(tmp_path)
        bundle = build_module_context(
            "period_delta",
            evidence_index_path=index_path,
            max_chars=24000,
            run_id=RUN_ID,
            subject=SUBJECT,
        )
        source_ids = {item["source_id"] for item in bundle["evidence"]}
        assert "prior_analysis" in source_ids
        assert bundle["selection"]["missing_prior_analysis"] == []
        assert "prior_analysis:summary" in bundle["selection"]["evidence_coverage"]
        assert bundle["selection"]["prior_analysis_sections"]

    def test_context_reports_missing_prior_analysis(self, tmp_path):
        payload = self._index()
        payload["entries"] = [item for item in payload["entries"] if item["source_id"] != "prior_analysis"]
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        bundle = build_module_context(
            "period_delta",
            evidence_index_path=index_path,
            max_chars=24000,
            run_id=RUN_ID,
            subject=SUBJECT,
        )
        assert bundle["selection"]["missing_prior_analysis"] == MODULE_CONFIG["period_delta"]["prior_analysis"]

    def test_other_modules_are_unaffected(self, tmp_path):
        index_path = self._write(tmp_path)
        bundle = build_module_context(
            "business_moat",
            evidence_index_path=index_path,
            max_chars=24000,
            run_id=RUN_ID,
            subject=SUBJECT,
        )
        assert "prior_analysis_sections" not in bundle["selection"] or bundle["selection"]["prior_analysis_sections"] == []
        assert all(item["source_id"] != "prior_analysis" for item in bundle["evidence"])
