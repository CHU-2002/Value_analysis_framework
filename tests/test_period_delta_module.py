
# 覆盖需求：REQ-004（增量更新分析）—— AC-1 契约三方一致、AC-2 prior_analysis 优先选入
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

    def test_business_trend_basis_is_the_year_ago_period(self):
        """AC-2.7 / F30：`business_trend` 的基准必须写明是「本期 vs 上年同期」。

        实测同一份 2026H1 数据，上一版按「run 间相对口径」记 `稳定`、本轮按 D7 定义记
        `恶化`，而 `reconcile_results` 0 冲突——因为规格没写基准。规格与 schema 参考
        两处都必须把基准钉死，且不得把它与 `conclusion_change`（对上次结论的调整）混同。
        """

        prompt = (ROOT / "shared/qualitative/agents/modules/period_delta.md").read_text(encoding="utf-8")
        assert "business_trend" in prompt
        for needle in ("上年同期", "comparable_period", "conclusion_change"):
            assert needle in prompt, needle
        assert "不是" in prompt

        schema = (ROOT / "shared/qualitative/references/output_schema.md").read_text(encoding="utf-8")
        row = next(line for line in schema.splitlines() if line.startswith("| business_trend "))
        assert "上年同期" in row, row
        assert "上一次 run" in row, row

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


class TestPriorAnalysisBudget:
    def _wide_index(self):
        entries = []

        def add(source_id, section):
            evidence_id = f"{source_id}:{section}:001"
            entries.append(
                {
                    "evidence_id": evidence_id,
                    "source_id": source_id,
                    "section": section,
                    "chunk_number": 1,
                    "quote": f"{source_id} {section} 摘录内容",
                    "locator": {"path": f"{source_id}.md", "section": section, "chunk": 1},
                    "content_hash": "hash-" + evidence_id,
                }
            )

        for section in ("MDA", "MATTERS", "P13", "P3", "P6"):
            add("pdf_sections", section)
        for section in ("3", "3P", "4", "4P", "5", "6", "12", "17"):
            add("market_data", section)
        for section in ("summary", "parameters", "claims", "risks", "watchlist", "quality"):
            add("prior_analysis", section)
        return {
            "schema": "investment.evidence_index",
            "schema_version": "1.0",
            "run": {"run_id": RUN_ID},
            "subject": dict(SUBJECT),
            "entries": entries,
        }

    def test_wide_index_does_not_starve_prior_analysis(self, tmp_path):
        # Regression: a full data pack and PDF section set used to consume every
        # evidence slot, silently dropping the previous run's conclusions.
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(self._wide_index(), ensure_ascii=False), encoding="utf-8")

        bundle = build_module_context(
            "period_delta",
            evidence_index_path=index_path,
            max_chars=24000,
            run_id=RUN_ID,
            subject=SUBJECT,
        )

        prior_ids = {
            item["evidence_id"] for item in bundle["evidence"] if item["source_id"] == "prior_analysis"
        }
        assert len(prior_ids) == len(MODULE_CONFIG["period_delta"]["prior_analysis"])
        assert bundle["selection"]["missing_prior_analysis"] == []

    def test_wide_index_saturates_the_limit_without_losing_other_sources(self, tmp_path):
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(self._wide_index(), ensure_ascii=False), encoding="utf-8")

        bundle = build_module_context(
            "period_delta",
            evidence_index_path=index_path,
            max_chars=24000,
            run_id=RUN_ID,
            subject=SUBJECT,
        )

        sources = {item["source_id"] for item in bundle["evidence"]}
        # 6 prior sections + 21 other candidates exceed the module's evidence slots,
        # so the limit must be exactly saturated while still covering this period's data.
        # 槽位数取模块自己的预算（period_delta 为同时装下必选节与对比基准抬到 16，
        # REQ-006.2 AC-2.5；其余模块仍走 DEFAULT_MAX_EVIDENCE=12）。
        assert len(bundle["evidence"]) == MODULE_CONFIG["period_delta"]["max_evidence"]
        assert "prior_analysis" in sources
        assert sources & {"market_data", "pdf_sections"}

    def test_omitted_prior_sections_are_reported(self, tmp_path):
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(self._wide_index(), ensure_ascii=False), encoding="utf-8")

        bundle = build_module_context(
            "period_delta",
            evidence_index_path=index_path,
            max_chars=24000,
            max_evidence=8,
            run_id=RUN_ID,
            subject=SUBJECT,
        )

        prior_ids = [
            item["evidence_id"] for item in bundle["evidence"] if item["source_id"] == "prior_analysis"
        ]
        # AC-2.5 之后第一遍是「必选节 → prior_analysis → 其余来源」轮转：8 个槽位里
        # 必选节（§3/§4/§5/§12）先各拿 1 条，prior 拿 1 条，余下 3 条再按档轮转——
        # 所以 prior 不再是「先拿满」，但至少不会被整档饿死。
        assert 0 < len(prior_ids) < len(MODULE_CONFIG["period_delta"]["prior_analysis"])
        assert any(item["source_id"] == "market_data" for item in bundle["evidence"])
        omitted = set(MODULE_CONFIG["period_delta"]["prior_analysis"]) - {
            evidence_id.split(":")[1] for evidence_id in prior_ids
        }
        assert set(bundle["selection"]["omitted_prior_analysis"]) == omitted
        assert bundle["selection"]["missing_prior_analysis"] == []
        assert omitted

    def test_other_modules_do_not_carry_prior_keys(self, tmp_path):
        index_path = tmp_path / "index.json"
        index_path.write_text(json.dumps(self._wide_index(), ensure_ascii=False), encoding="utf-8")
        bundle = build_module_context(
            "business_moat", evidence_index_path=index_path, max_chars=24000, run_id=RUN_ID, subject=SUBJECT
        )
        assert "prior_analysis_sections" not in bundle["selection"]
        assert "missing_prior_analysis" not in bundle["selection"]


class TestUpdateFlowDocumentation:
    """The documented update flow must match the tools it calls."""

    def test_documented_sections_path_matches_the_tool_default(self):
        from pdf_preprocessor import resolve_output_path

        # The tool writes next to the PDF by default; the flow must use exactly
        # this path for both the parser and the run snapshot.
        assert (
            resolve_output_path("/x/sources/pdf/600887_2026_半年报.pdf", "2026H1", None)
            == "/x/sources/pdf/pdf_sections_2026H1.json"
        )

        coordinator = (ROOT / "shared/qualitative/coordinator_update.md").read_text(encoding="utf-8")
        assert "sources/pdf_sections/" not in coordinator
        assert coordinator.count("sources/pdf/pdf_sections_{period}.json") >= 2  # step2 --output, step3 --input
        assert "pdf_sections_{period}.json" in coordinator  # layout diagram
        assert "tushare_collector.py" in coordinator

        for command_dir in (".claude/commands", ".opencode/commands"):
            command = (ROOT / command_dir / "update-analysis.md").read_text(encoding="utf-8")
            assert "sources/pdf_sections/" not in command
            assert "sources/pdf/pdf_sections_{period}.json" in command
            assert "tushare_collector.py" in command

    def test_documented_exit_codes_match_the_detector(self):
        coordinator = (ROOT / "shared/qualitative/coordinator_update.md").read_text(encoding="utf-8")
        # inputs_changed maps to a full rerun (exit 3), not to a plain new report.
        assert "退出码 `1`：存在**新报告**" in coordinator
        assert "输入变化" in coordinator.split("退出码 `3`")[1].split("\n")[0]
        for command_dir in (".claude/commands", ".opencode/commands"):
            command = (ROOT / command_dir / "update-analysis.md").read_text(encoding="utf-8")
            assert "| 1 | a new report is available |" in command
            exit3_row = next(line for line in command.splitlines() if line.startswith("| 3 |"))
            assert "changed inputs" in exit3_row
