"""Tests for prepare's --prior-analysis evidence source (incremental updates)."""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from results.context import build_module_context
from results.prepare import prepare_run

TICKER = "600887.SH"
SUBJECT = {"ticker": TICKER, "company": "伊利股份", "market": "CN"}


def _prior_result(run_id="20260401T101010101010Z"):
    return {
        "schema": "investment.result",
        "schema_version": "1.0",
        "result_type": "qualitative.synthesis",
        "run": {"run_id": run_id, "generated_at": "2026-04-01T10:00:00Z", "as_of": "2026-03-31", "status": "complete"},
        "subject": dict(SUBJECT),
        "scope": ["D1", "D2", "D3", "D4", "D5", "D6"],
        "summary": {"thesis": "护城河维持，关注原奶价格与毛利率。", "confidence": "medium"},
        "parameters": {"moat_rating": "强", "integrity_rating": "可靠"},
        "metrics": {"revenue": 115636.23},
        "claims": [
            {
                "claim_id": "synthesis-001",
                "statement": "护城河稳固。",
                "type": "judgement",
                "confidence": "medium",
                "evidence_ids": [],
            }
        ],
        "risks": [{"risk": "原奶价格上行", "severity": "high", "monitoring_indicator": "原奶价格"}],
        "watchlist": ["单季毛利率"],
        "evidence": [],
        "upstream_digest": "prior-digest",
        "quality": {"completeness": 0.9, "missing_inputs": [], "warnings": [], "unresolved_questions": []},
    }


def _company_dir(tmp_path):
    root = tmp_path / "600887_伊利"
    root.mkdir()
    (root / "data_pack_market.md").write_text("# pack\n", encoding="utf-8")
    return root


class TestPreparePriorAnalysis:
    def test_registers_prior_analysis_source(self, tmp_path):
        root = _company_dir(tmp_path)
        prior = root / "prior_result.json"
        prior.write_text(json.dumps(_prior_result(), ensure_ascii=False), encoding="utf-8")

        result = prepare_run(
            root,
            ticker=TICKER,
            company="伊利股份",
            primary_period="2026H1",
            prior_analysis=prior,
        )

        index = json.loads(Path(result["evidence_index"]).read_text(encoding="utf-8"))
        source_ids = {source["source_id"] for source in index["sources"]}
        assert "prior_analysis" in source_ids
        sections = {entry["section"] for entry in index["entries"] if entry["source_id"] == "prior_analysis"}
        assert {"summary", "parameters", "claims", "risks", "watchlist", "quality"} <= sections
        assert result["prior_analysis"] == str(prior.resolve())
        assert result["warnings"] == []

    def test_manifest_records_prior_analysis_input(self, tmp_path):
        root = _company_dir(tmp_path)
        prior = root / "prior_result.json"
        prior.write_text(json.dumps(_prior_result(), ensure_ascii=False), encoding="utf-8")

        result = prepare_run(root, ticker=TICKER, company="伊利股份", prior_analysis=prior)

        manifest = json.loads(Path(result["run_manifest"]).read_text(encoding="utf-8"))
        source_ids = {item["source_id"] for item in manifest["inputs"]}
        assert "prior_analysis" in source_ids
        assert manifest["schema_version"] == "1.0"

    def test_period_delta_context_uses_prior_analysis(self, tmp_path):
        root = _company_dir(tmp_path)
        prior = root / "prior_result.json"
        prior.write_text(json.dumps(_prior_result(), ensure_ascii=False), encoding="utf-8")

        result = prepare_run(root, ticker=TICKER, company="伊利股份", prior_analysis=prior)

        bundle = build_module_context(
            "period_delta",
            evidence_index_path=result["evidence_index"],
            max_chars=24000,
        )
        prior_evidence = [item for item in bundle["evidence"] if item["source_id"] == "prior_analysis"]
        assert prior_evidence
        assert bundle["selection"]["missing_prior_analysis"] == []
        assert any("护城河" in item["quote"] for item in prior_evidence)

    def test_missing_prior_analysis_is_a_warning_not_a_crash(self, tmp_path):
        root = _company_dir(tmp_path)

        result = prepare_run(
            root,
            ticker=TICKER,
            company="伊利股份",
            prior_analysis=root / "does-not-exist.json",
        )

        assert result["prior_analysis"] == ""
        assert any("prior analysis not found" in warning for warning in result["warnings"])
        manifest = json.loads(Path(result["run_manifest"]).read_text(encoding="utf-8"))
        assert "prior_analysis" not in {item["source_id"] for item in manifest["inputs"]}
        assert "prior_analysis" in " ".join(manifest.get("warnings", []))

    def test_absent_prior_analysis_keeps_legacy_behaviour(self, tmp_path):
        root = _company_dir(tmp_path)
        result = prepare_run(root, ticker=TICKER, company="伊利股份")
        assert result["prior_analysis"] == ""
        manifest = json.loads(Path(result["run_manifest"]).read_text(encoding="utf-8"))
        assert "prior_analysis" not in {item["source_id"] for item in manifest["inputs"]}

    def test_cli_accepts_prior_analysis(self, tmp_path):
        root = _company_dir(tmp_path)
        prior = root / "prior_result.json"
        prior.write_text(json.dumps(_prior_result(), ensure_ascii=False), encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.results.prepare",
                "--output-dir",
                str(root),
                "--ticker",
                TICKER,
                "--company",
                "伊利股份",
                "--primary-period",
                "2026H1",
                "--prior-analysis",
                str(prior),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        payload = json.loads(completed.stdout)
        assert payload["prior_analysis"] == str(prior.resolve())
        assert payload["primary_period"] == "2026H1"
