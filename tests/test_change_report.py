
# 覆盖需求：REQ-004（增量更新分析）—— AC-4 失效证据降级而非拒绝、AC-5 变化报告产出
# 覆盖需求：REQ-006.1 —— AC-1.5 变化报告默认预算按真实载荷、降级必须显式
"""Tests for scripts/results/change_report.py context builder."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from results.change_report import build_change_report_context, main
from results.schema import RESULT_TYPE_CONTRACTS

SUBJECT = {"ticker": "600887.SH", "company": "伊利股份", "market": "CN"}
RUN_ID = "20260919T221000000000Z"
PRIOR_RUN_ID = "20260401T101010101010Z"

DELTA_QUOTE = "营业收入 64,330.94 百万元（2026H1）"
PRIOR_QUOTE = "上次结论：护城河维持，关注毛利率"


def _entry(evidence_id, source_id, section, quote):
    return {
        "evidence_id": evidence_id,
        "source_id": source_id,
        "section": section,
        "chunk_number": 1,
        "quote": quote,
        "locator": {"path": f"{source_id}.json", "section": section, "chunk": 1},
        "content_hash": "hash-" + evidence_id,
    }


def _evidence(evidence_id):
    """Mirror one index entry so the result passes excerpt validation."""

    for entry in INDEX_ENTRIES:
        if entry["evidence_id"] == evidence_id:
            return {
                "evidence_id": entry["evidence_id"],
                "source_id": entry["source_id"],
                "locator": entry["locator"],
                "quote": entry["quote"],
            }
    raise KeyError(evidence_id)


INDEX_ENTRIES = [
    _entry("market_data:3:001", "market_data", "3", DELTA_QUOTE),
    _entry("prior_analysis:summary:001", "prior_analysis", "summary", PRIOR_QUOTE),
]


def _write_index(tmp_path, run_id=RUN_ID, subject=None, entries=None):
    payload = {
        "schema": "investment.evidence_index",
        "schema_version": "1.0",
        "run": {"run_id": run_id},
        "subject": dict(subject or SUBJECT),
        "entries": list(INDEX_ENTRIES) if entries is None else entries,
    }
    path = tmp_path / "index.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _delta_result(evidence_ids=("market_data:3:001",), *, period="2026H1", comparable="2025H1"):
    return {
        "schema": "investment.result",
        "schema_version": "1.0",
        "result_type": "qualitative.period_delta",
        "run": {"run_id": RUN_ID, "generated_at": "2026-09-19T22:00:00Z", "as_of": "2026-08-30", "status": "complete"},
        "subject": dict(SUBJECT),
        "scope": ["D7"],
        "summary": {"thesis": "收入增长但毛利率承压。", "decision_relevance": "维持长期判断。", "confidence": "medium"},
        "parameters": {
            "report_period": period,
            "comparable_period": comparable,
            "business_trend": "稳定",
            "conclusion_change": "维持",
            "change_significance": "一般",
            "guidance_delivery": "兑现",
            "requires_full_rerun": False,
            "revenue_yoy_pct": 8.3,
            "net_profit_yoy_pct": 5.1,
            "gross_margin_change_pct": -0.4,
            "operating_cashflow_to_profit": 1.12,
        },
        "metrics": {"report_period": period, "comparable_period": comparable},
        "claims": [
            {
                "claim_id": "period_delta-001",
                "statement": "收入同比增长。",
                "type": "judgement",
                "confidence": "medium",
                "evidence_ids": list(evidence_ids),
            }
        ],
        "risks": [{"risk": "毛利率下行", "severity": "medium", "monitoring_indicator": "单季毛利率"}],
        "watchlist": ["下一期单季毛利率"],
        "evidence": [_evidence(eid) for eid in evidence_ids],
        "quality": {"completeness": 0.8, "missing_inputs": [], "warnings": [], "unresolved_questions": []},
    }


def _synthesis_result(run_id, evidence_ids=("prior_analysis:summary:001",), thesis="维持长期判断"):
    return {
        "schema": "investment.result",
        "schema_version": "1.0",
        "result_type": "qualitative.synthesis",
        "run": {"run_id": run_id, "generated_at": "2026-09-19T22:00:00Z", "as_of": "2026-08-30", "status": "complete"},
        "subject": dict(SUBJECT),
        "scope": ["D1", "D2", "D3", "D4", "D5", "D6"],
        "summary": {"thesis": thesis, "decision_relevance": "估值假设不变。", "confidence": "medium"},
        "parameters": {"moat_rating": "强"},
        "metrics": {"revenue": 64330.94},
        "claims": [
            {
                "claim_id": "synthesis-001",
                "statement": "护城河维持。",
                "type": "judgement",
                "confidence": "medium",
                "evidence_ids": list(evidence_ids),
            }
        ],
        "risks": [{"risk": "原奶价格", "severity": "medium", "monitoring_indicator": "原奶价格"}],
        "watchlist": ["单季毛利率"],
        "evidence": [
            _evidence(eid) for eid in evidence_ids
        ],
        "upstream_digest": "digest-" + run_id,
        "quality": {"completeness": 0.9, "missing_inputs": [], "warnings": [], "unresolved_questions": []},
    }


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class TestBuildChangeReportContext:
    def test_happy_path(self, tmp_path):
        index = _write_index(tmp_path)
        delta = _write(tmp_path, "delta.json", _delta_result())
        synthesis = _write(tmp_path, "synthesis.json", _synthesis_result(RUN_ID, ("market_data:3:001",)))
        prior = _write(tmp_path, "prior.json", _synthesis_result(PRIOR_RUN_ID))

        payload = build_change_report_context(
            delta_result_path=delta,
            evidence_index_path=index,
            synthesis_result_path=synthesis,
            prior_synthesis_path=prior,
        )

        assert payload["schema"] == "investment.change_report_context"
        assert payload["report_period"] == "2026H1"
        assert payload["comparable_period"] == "2025H1"
        assert payload["run"]["run_id"] == RUN_ID
        assert payload["period_delta"]["result_type"] == "qualitative.period_delta"
        assert payload["prior_synthesis"]["summary"]["thesis"] == "维持长期判断"
        assert payload["instructions"]["cumulative_vs_single_quarter"]
        assert payload["degraded"] == {
            "prior_synthesis_dropped": False,
            "synthesis_dropped": False,
            "prior_synthesis_claims": 6,
            "synthesis_claims": 6,
            "prior_evidence_unavailable": 0,
            "missing_inputs": [],
            "unusable_inputs": [],
        }

    def test_optional_inputs_absent(self, tmp_path):
        index = _write_index(tmp_path)
        delta = _write(tmp_path, "delta.json", _delta_result())

        payload = build_change_report_context(delta_result_path=delta, evidence_index_path=index)

        assert payload["synthesis"] is None
        assert payload["prior_synthesis"] is None
        assert payload["degraded"] == {
            "prior_synthesis_dropped": False,
            "synthesis_dropped": False,
            "prior_synthesis_claims": 0,
            "synthesis_claims": 0,
            "prior_evidence_unavailable": 0,
            "missing_inputs": [],
            "unusable_inputs": [],
        }

    def test_rejects_non_delta_result(self, tmp_path):
        index = _write_index(tmp_path)
        wrong = _write(tmp_path, "wrong.json", _synthesis_result(RUN_ID))
        with pytest.raises(ValueError, match="qualitative.period_delta"):
            build_change_report_context(delta_result_path=wrong, evidence_index_path=index)

    def test_rejects_evidence_index_run_mismatch(self, tmp_path):
        index = _write_index(tmp_path, run_id="another-run")
        delta = _write(tmp_path, "delta.json", _delta_result())
        with pytest.raises(ValueError, match="run_id"):
            build_change_report_context(delta_result_path=delta, evidence_index_path=index)

    def test_rejects_subject_mismatch(self, tmp_path):
        index = _write_index(tmp_path, subject={"ticker": "000858.SZ", "company": "五粮液", "market": "CN"})
        delta = _write(tmp_path, "delta.json", _delta_result())
        with pytest.raises(ValueError, match="subject"):
            build_change_report_context(delta_result_path=delta, evidence_index_path=index)

    def test_rejects_missing_evidence(self, tmp_path):
        index = _write_index(tmp_path)
        result = _delta_result()
        result["claims"][0]["evidence_ids"] = ["market_data:404:001"]
        result["evidence"] = [
            {
                "evidence_id": "market_data:404:001",
                "source_id": "market_data",
                "locator": {"path": "market_data.json", "section": "404", "chunk": 1},
                "quote": "该摘录不在索引中",
            }
        ]
        delta = _write(tmp_path, "delta.json", result)
        with pytest.raises(ValueError, match="missing from the index"):
            build_change_report_context(delta_result_path=delta, evidence_index_path=index)

    def test_invalid_evidence_index_schema(self, tmp_path):
        bad_index = tmp_path / "bad.json"
        bad_index.write_text('{"schema": "nope"}', encoding="utf-8")
        delta = _write(tmp_path, "delta.json", _delta_result())
        with pytest.raises(ValueError, match="invalid schema"):
            build_change_report_context(delta_result_path=delta, evidence_index_path=bad_index)

    def test_budget_degrades_then_fits(self, tmp_path):
        index = _write_index(tmp_path)
        delta = _write(tmp_path, "delta.json", _delta_result())
        synthesis = _write(tmp_path, "synthesis.json", _synthesis_result(RUN_ID, ("market_data:3:001",)))
        prior = _write(tmp_path, "prior.json", _synthesis_result(PRIOR_RUN_ID))

        full = build_change_report_context(
            delta_result_path=delta,
            evidence_index_path=index,
            synthesis_result_path=synthesis,
            prior_synthesis_path=prior,
        )
        tight = build_change_report_context(
            delta_result_path=delta,
            evidence_index_path=index,
            synthesis_result_path=synthesis,
            prior_synthesis_path=prior,
            max_chars=full["budget"]["actual_chars"] - 200,
        )

        assert tight["budget"]["actual_chars"] <= full["budget"]["actual_chars"] - 200
        assert (
            tight["degraded"]["prior_synthesis_dropped"] or tight["degraded"]["synthesis_dropped"]
        )

    def test_impossible_budget_raises(self, tmp_path):
        index = _write_index(tmp_path)
        delta = _write(tmp_path, "delta.json", _delta_result())
        with pytest.raises(ValueError, match="Unable to fit"):
            build_change_report_context(delta_result_path=delta, evidence_index_path=index, max_chars=200)

    def test_reconciliation_included_only_for_same_run(self, tmp_path):
        index = _write_index(tmp_path)
        delta = _write(tmp_path, "delta.json", _delta_result())
        recon = _write(
            tmp_path,
            "recon.json",
            {
                "schema": "investment.reconciliation",
                "schema_version": "1.0",
                "run": {"run_id": RUN_ID},
                "subject": dict(SUBJECT),
                "result_digest": "x",
                "conflicts": [],
                "warnings": [],
            },
        )

        payload = build_change_report_context(
            delta_result_path=delta, evidence_index_path=index, reconciliation_path=recon
        )
        assert payload["reconciliation"]["run"]["run_id"] == RUN_ID

        other = _write(
            tmp_path,
            "recon_other.json",
            {
                "schema": "investment.reconciliation",
                "schema_version": "1.0",
                "run": {"run_id": "other"},
                "subject": dict(SUBJECT),
                "result_digest": "x",
                "conflicts": [],
                "warnings": [],
            },
        )
        payload = build_change_report_context(
            delta_result_path=delta, evidence_index_path=index, reconciliation_path=other
        )
        assert payload["reconciliation"] is None


class TestCli:
    def test_main_writes_context(self, tmp_path, capsys):
        index = _write_index(tmp_path)
        delta = _write(tmp_path, "delta.json", _delta_result())
        output = tmp_path / "ctx.json"

        main(["--delta", str(delta), "--evidence-index", str(index), "--output", str(output)])

        assert output.is_file()
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["report_period"] == "2026H1"
        assert "Change-report context written" in capsys.readouterr().out


def test_contract_scope_is_d7():
    assert RESULT_TYPE_CONTRACTS["qualitative.period_delta"]["scope"] == ["D7"]


class TestPriorEvidenceDowngrade:
    def test_stale_prior_evidence_is_downgraded_not_fatal(self, tmp_path):
        # A previous run's excerpts point at that run's snapshot. After a new
        # reporting period the inputs change, so those excerpts must not match
        # this run's index -- and that must not make the update impossible.
        index = _write_index(tmp_path)
        delta = _write(tmp_path, "delta.json", _delta_result())
        prior_payload = _synthesis_result(PRIOR_RUN_ID)
        prior_payload["evidence"][0]["locator"] = {
            "path": "/old/snapshot/prior_analysis.json",
            "section": "summary",
            "chunk": 1,
        }
        prior_payload["evidence"][0]["quote"] = "旧快照中的另一段原文"
        prior = _write(tmp_path, "prior.json", prior_payload)

        payload = build_change_report_context(
            delta_result_path=delta,
            evidence_index_path=index,
            prior_synthesis_path=prior,
        )

        assert payload["degraded"]["prior_evidence_unavailable"] == 1
        assert payload["prior_synthesis"]["summary"]["thesis"]
        assert payload["prior_synthesis"]["evidence"] == []
        assert payload["prior_synthesis"]["claims"][0]["evidence_ids"] == []
        assert payload["prior_synthesis"]["claims"][0]["evidence_status"] == "unavailable"
        assert payload["prior_synthesis"]["claims"][0]["unavailable_evidence_ids"]

    def test_compatible_prior_evidence_is_kept(self, tmp_path):
        index = _write_index(tmp_path)
        delta = _write(tmp_path, "delta.json", _delta_result())
        prior = _write(tmp_path, "prior.json", _synthesis_result(PRIOR_RUN_ID))

        payload = build_change_report_context(
            delta_result_path=delta,
            evidence_index_path=index,
            prior_synthesis_path=prior,
        )

        assert payload["degraded"]["prior_evidence_unavailable"] == 0
        assert payload["prior_synthesis"]["evidence"]


class TestBudgetAndDegradationSemantics:
    @staticmethod
    def _size(payload):
        return len(json.dumps(payload, ensure_ascii=False, indent=2)) + 1

    def test_degraded_block_is_counted_in_the_budget(self, tmp_path):
        index = _write_index(tmp_path)
        delta = _write(tmp_path, "delta.json", _delta_result())
        synthesis = _write(tmp_path, "synthesis.json", _synthesis_result(RUN_ID, ("market_data:3:001",)))
        prior = _write(tmp_path, "prior.json", _synthesis_result(PRIOR_RUN_ID))

        full = build_change_report_context(
            delta_result_path=delta,
            evidence_index_path=index,
            synthesis_result_path=synthesis,
            prior_synthesis_path=prior,
        )
        budget = full["budget"]["actual_chars"] - 50
        tight = build_change_report_context(
            delta_result_path=delta,
            evidence_index_path=index,
            synthesis_result_path=synthesis,
            prior_synthesis_path=prior,
            max_chars=budget,
        )

        assert self._size(tight) <= budget
        assert tight["budget"]["actual_chars"] == self._size(tight)

    def test_given_but_missing_synthesis_is_an_error(self, tmp_path):
        index = _write_index(tmp_path)
        delta = _write(tmp_path, "delta.json", _delta_result())
        with pytest.raises(ValueError, match="file not found"):
            build_change_report_context(
                delta_result_path=delta,
                evidence_index_path=index,
                synthesis_result_path=tmp_path / "nope.json",
            )

    def test_missing_prior_is_reported_not_fatal(self, tmp_path):
        index = _write_index(tmp_path)
        delta = _write(tmp_path, "delta.json", _delta_result())

        payload = build_change_report_context(
            delta_result_path=delta,
            evidence_index_path=index,
            prior_synthesis_path=tmp_path / "nope.json",
        )

        assert payload["degraded"]["missing_inputs"] == [str(tmp_path / "nope.json")]
        assert payload["prior_synthesis"] is None

    def test_wrong_type_prior_is_downgraded(self, tmp_path):
        index = _write_index(tmp_path)
        delta = _write(tmp_path, "delta.json", _delta_result())
        wrong = _write(tmp_path, "wrong.json", _delta_result())  # period_delta, not synthesis

        payload = build_change_report_context(
            delta_result_path=delta,
            evidence_index_path=index,
            prior_synthesis_path=wrong,
        )

        assert payload["prior_synthesis"] is None
        assert payload["degraded"]["unusable_inputs"]
        assert "qualitative.synthesis" in payload["degraded"]["unusable_inputs"][0]["error"]

    def test_prior_evidence_count_is_zero_when_card_is_dropped(self, tmp_path):
        index = _write_index(tmp_path)
        delta = _write(tmp_path, "delta.json", _delta_result())
        prior_payload = _synthesis_result(PRIOR_RUN_ID)
        prior_payload["evidence"][0]["locator"] = {"path": "/old/x.json", "section": "summary", "chunk": 1}
        prior_payload["evidence"][0]["quote"] = "旧快照摘录"
        prior = _write(tmp_path, "prior.json", prior_payload)

        # A budget that fits the delta but forces the prior card out entirely.
        minimal = build_change_report_context(
            delta_result_path=delta, evidence_index_path=index
        )["budget"]["actual_chars"]
        payload = build_change_report_context(
            delta_result_path=delta,
            evidence_index_path=index,
            prior_synthesis_path=prior,
            max_chars=minimal,
        )

        assert payload["prior_synthesis"] is None
        assert payload["degraded"]["prior_synthesis_dropped"] is True
        assert payload["degraded"]["prior_evidence_unavailable"] == 0


def test_default_budget_covers_the_measured_real_payload():
    """AC-1.5：默认预算按实跑真实载荷设定（D7 单卡 26,141；两卡俱全实测要 ~150k）。"""
    import inspect

    default = inspect.signature(build_change_report_context).parameters["max_chars"].default
    assert default >= 150000, f"默认预算 {default} 小于实跑测得的 150k，会静默丢卡"


def test_main_reports_the_degradation_to_the_operator(tmp_path, capsys):
    """AC-1.5：降级不能只在 JSON 里，CLI 也要说出来。"""
    index = _write_index(tmp_path)
    delta = _write(tmp_path, "delta.json", _delta_result())
    synthesis = _write(tmp_path, "synthesis.json", _synthesis_result(RUN_ID, ("market_data:3:001",)))
    prior = _write(tmp_path, "prior.json", _synthesis_result(PRIOR_RUN_ID))
    full = build_change_report_context(
        delta_result_path=delta,
        evidence_index_path=index,
        synthesis_result_path=synthesis,
        prior_synthesis_path=prior,
    )
    output = tmp_path / "context.json"
    main(
        [
            "--delta", str(delta),
            "--evidence-index", str(index),
            "--synthesis", str(synthesis),
            "--prior-synthesis", str(prior),
            "--max-chars", str(full["budget"]["actual_chars"] - 200),
            "--output", str(output),
        ]
    )
    assert "已降级" in capsys.readouterr().out
