
# 覆盖需求：REQ-002（同比可比期与按期次章节包）—— AC-3 ``--primary-period`` 指定主期次、AC-4 兼容 inputs/ 快照
"""PR2 tests: ``prepare --primary-period`` and the ``inputs/`` snapshot layout.

Covers:

* ``<output-dir>/inputs/`` is used as the input root when it exists, with the
  same evidence/manifest/context structure as the legacy flat layout.
* ``annual_report:{stem}`` source ids never change; only a ``period`` metadata
  field is added next to them.
* ``pdf_sections_{period}.json`` is preferred for the requested primary period,
  with a recorded warning when only a mismatching ``pdf_sections.json`` exists.
* The manifest gains the additive ``primary_period`` field (schema_version 1.0).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from results.prepare import _normalize_primary_period, _period_arg, prepare_run

REPO_ROOT = Path(__file__).resolve().parents[1]

DATA_PACK = "## 1. Basic information\n普通运营公司\n## 12. Metrics\nStable\n"


def _write_pdf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 dummy annual report")


def _write_sections(path: Path, *, period: str | None, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "metadata": {
            "pdf_file": "600887_2025_年报.pdf",
            "total_pages": 10,
            "sections_found": 2,
            "sections_total": 9,
        },
        "MDA": f"{marker} 管理层讨论与分析",
        "P13": f"{marker} 非经常性损益",
    }
    if period is not None:
        payload["metadata"]["period"] = period
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _prepare(root: Path, **kwargs):
    return prepare_run(root, ticker="600887.SH", company="伊利股份", run_id="test-run", **kwargs)


def _manifest(root: Path) -> dict:
    return json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))


def _evidence(root: Path) -> dict:
    return json.loads((root / "evidence" / "index.json").read_text(encoding="utf-8"))


def _source_ids(items) -> list[str]:
    return sorted(item["source_id"] for item in items)


def _pdf_section_quotes(root: Path) -> str:
    index = _evidence(root)
    return "\n".join(
        entry["quote"] for entry in index["entries"] if entry["source_id"] == "pdf_sections"
    )


# ============================================================
# PDF source ids and period metadata
# ============================================================

class TestPdfSourcePeriodMetadata:
    """``annual_report:{stem}`` is unchanged; ``period`` is added when known."""

    def test_legacy_layout_pdf_source_id_and_period(self, tmp_path):
        root = tmp_path / "600887_伊利"
        root.mkdir()
        (root / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        _write_pdf(root / "600887_2025_年报.pdf")

        result = _prepare(root)

        manifest = _manifest(root)
        assert result["d6_triggered"] is False
        assert manifest["schema_version"] == "1.0"
        assert "primary_period" not in manifest
        assert "warnings" not in manifest

        pdf_inputs = [item for item in manifest["inputs"] if item["source_id"].startswith("annual_report:")]
        assert [item["source_id"] for item in pdf_inputs] == ["annual_report:600887_2025_年报"]
        assert pdf_inputs[0]["period"] == "2025FY"
        assert Path(pdf_inputs[0]["path"]) == (root / "600887_2025_年报.pdf").resolve()

        index = _evidence(root)
        pdf_sources = [item for item in index["sources"] if item["source_id"].startswith("annual_report:")]
        assert [item["source_id"] for item in pdf_sources] == ["annual_report:600887_2025_年报"]
        assert pdf_sources[0]["period"] == "2025FY"
        assert pdf_sources[0]["exists"] is True

    def test_quarterly_pdf_period_is_recorded(self, tmp_path):
        root = tmp_path / "600887_伊利"
        root.mkdir()
        (root / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        _write_pdf(root / "600887_2026_一季报.pdf")

        _prepare(root)

        manifest = _manifest(root)
        item = next(i for i in manifest["inputs"] if i["source_id"].startswith("annual_report:"))
        assert item["period"] == "2026Q1"

    def test_unresolvable_pdf_name_has_no_period_key(self, tmp_path):
        root = tmp_path / "600887_伊利"
        root.mkdir()
        (root / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        _write_pdf(root / "扫描件.pdf")

        _prepare(root)

        manifest = _manifest(root)
        item = next(i for i in manifest["inputs"] if i["source_id"].startswith("annual_report:"))
        assert item["source_id"] == "annual_report:扫描件"
        assert "period" not in item
        index = _evidence(root)
        source = next(i for i in index["sources"] if i["source_id"] == "annual_report:扫描件")
        assert "period" not in source

    def test_period_metadata_does_not_change_input_digest_contract(self, tmp_path):
        """``period`` is additive: it must not participate in the input digest."""
        from results.manifest import input_set_digest

        root = tmp_path / "600887_伊利"
        root.mkdir()
        (root / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        _write_pdf(root / "600887_2025_年报.pdf")

        _prepare(root)
        manifest = _manifest(root)

        assert len(manifest["input_digest"]) == 64
        assert any("period" in item for item in manifest["inputs"])
        stripped = [
            {key: value for key, value in item.items() if key != "period"}
            for item in manifest["inputs"]
        ]
        # Digest identity fields (source_id/path/exists/sha256) are untouched, so
        # a manifest written before this change stays verifiable.
        assert input_set_digest(stripped) == manifest["input_digest"]
        assert input_set_digest(manifest["inputs"]) == manifest["input_digest"]


# ============================================================
# inputs/ snapshot directory
# ============================================================

class TestInputsSnapshotDirectory:
    """``<output-dir>/inputs/`` is preferred when it exists."""

    def test_inputs_directory_takes_precedence(self, tmp_path):
        root = tmp_path / "runs" / "test-run"
        inputs = root / "inputs"
        inputs.mkdir(parents=True)
        (inputs / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        (inputs / "data_pack_report.md").write_text("## P13\nfootnotes\n", encoding="utf-8")
        _write_sections(inputs / "pdf_sections.json", period="2025FY", marker="INPUTS")
        _write_pdf(inputs / "600887_2025_年报.pdf")

        # Decoys in the run root must be ignored while inputs/ exists.
        (root / "data_pack_market.md").write_text("## 1. Basic information\nDECOY\n", encoding="utf-8")
        _write_pdf(root / "600887_2024_年报.pdf")

        _prepare(root)

        manifest = _manifest(root)
        assert _source_ids(manifest["inputs"]) == [
            "annual_report:600887_2025_年报",
            "market_data",
            "pdf_footnotes",
            "pdf_sections",
        ]
        inputs_root = str(inputs.resolve())
        assert all(item["path"].startswith(inputs_root) for item in manifest["inputs"])
        assert "INPUTS" in _pdf_section_quotes(root)

    def test_structure_matches_legacy_layout(self, tmp_path):
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        (legacy / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        (legacy / "data_pack_report.md").write_text("## P13\nfootnotes\n", encoding="utf-8")
        _write_sections(legacy / "pdf_sections.json", period="2025FY", marker="SAME")
        _write_pdf(legacy / "600887_2025_年报.pdf")

        snapshot = tmp_path / "snapshot"
        inputs = snapshot / "inputs"
        inputs.mkdir(parents=True)
        (inputs / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        (inputs / "data_pack_report.md").write_text("## P13\nfootnotes\n", encoding="utf-8")
        _write_sections(inputs / "pdf_sections.json", period="2025FY", marker="SAME")
        _write_pdf(inputs / "600887_2025_年报.pdf")

        legacy_result = _prepare(legacy)
        snapshot_result = _prepare(snapshot)

        legacy_manifest = _manifest(legacy)
        snapshot_manifest = _manifest(snapshot)

        assert legacy_result["d6_triggered"] is False
        assert snapshot_result["d6_triggered"] is False
        assert _source_ids(legacy_manifest["inputs"]) == _source_ids(snapshot_manifest["inputs"])
        assert [item.get("period") for item in legacy_manifest["inputs"]] == [
            item.get("period") for item in snapshot_manifest["inputs"]
        ]

        legacy_index = _evidence(legacy)
        snapshot_index = _evidence(snapshot)
        assert list(legacy_index.keys()) == list(snapshot_index.keys())
        assert _source_ids(legacy_index["sources"]) == _source_ids(snapshot_index["sources"])
        assert [entry["evidence_id"] for entry in legacy_index["entries"]] == [
            entry["evidence_id"] for entry in snapshot_index["entries"]
        ]

        legacy_dirs = sorted(path.name for path in (legacy / "contexts").glob("*.json"))
        snapshot_dirs = sorted(path.name for path in (snapshot / "contexts").glob("*.json"))
        assert legacy_dirs == snapshot_dirs
        for name in legacy_dirs:
            legacy_context = json.loads((legacy / "contexts" / name).read_text(encoding="utf-8"))
            snapshot_context = json.loads((snapshot / "contexts" / name).read_text(encoding="utf-8"))
            assert list(legacy_context.keys()) == list(snapshot_context.keys())

        assert [item["role"] for item in legacy_manifest["artifacts"]] == [
            item["role"] for item in snapshot_manifest["artifacts"]
        ]


# ============================================================
# primary period -> pdf_sections selection
# ============================================================

class TestPrimaryPeriodSections:
    """``--primary-period`` selects the matching per-period sections file."""

    def test_per_period_file_is_used(self, tmp_path):
        root = tmp_path / "runs" / "test-run"
        inputs = root / "inputs"
        inputs.mkdir(parents=True)
        (inputs / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        _write_sections(inputs / "pdf_sections_2026H1.json", period="2026H1", marker="PERIOD-H1")
        _write_sections(inputs / "pdf_sections.json", period="2025FY", marker="LEGACY-FY")

        result = _prepare(root, primary_period="2026H1")

        manifest = _manifest(root)
        assert manifest["primary_period"] == "2026H1"
        assert "warnings" not in manifest
        assert result["primary_period"] == "2026H1"
        assert result["warnings"] == []

        index = _evidence(root)
        sections_source = next(item for item in index["sources"] if item["source_id"] == "pdf_sections")
        assert Path(sections_source["path"]) == (inputs / "pdf_sections_2026H1.json").resolve()
        assert "PERIOD-H1" in _pdf_section_quotes(root)
        assert "LEGACY-FY" not in _pdf_section_quotes(root)

    def test_contexts_consume_the_period_file(self, tmp_path):
        root = tmp_path / "runs" / "test-run"
        inputs = root / "inputs"
        inputs.mkdir(parents=True)
        (inputs / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        _write_sections(inputs / "pdf_sections_2026H1.json", period="2026H1", marker="PERIOD-H1")
        _write_sections(inputs / "pdf_sections.json", period="2025FY", marker="LEGACY-FY")

        _prepare(root, primary_period="2026H1")

        context = json.loads((root / "contexts" / "business_moat.json").read_text(encoding="utf-8"))
        assert any(path.endswith("pdf_sections_2026H1.json") for path in context["inputs"])
        assert "PERIOD-H1" in context["context_text"]
        assert "LEGACY-FY" not in context["context_text"]

    def test_per_period_file_is_used_in_legacy_layout(self, tmp_path):
        root = tmp_path / "600887_伊利"
        root.mkdir()
        (root / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        _write_sections(root / "pdf_sections_2026H1.json", period="2026H1", marker="PERIOD-H1")
        _write_sections(root / "pdf_sections.json", period="2025FY", marker="LEGACY-FY")

        result = _prepare(root, primary_period="2026H1")

        manifest = _manifest(root)
        assert manifest["primary_period"] == "2026H1"
        assert "warnings" not in manifest
        assert result["warnings"] == []
        index = _evidence(root)
        sections_source = next(item for item in index["sources"] if item["source_id"] == "pdf_sections")
        assert Path(sections_source["path"]) == (root / "pdf_sections_2026H1.json").resolve()
        assert "PERIOD-H1" in _pdf_section_quotes(root)

    def test_mismatching_legacy_sections_record_a_warning(self, tmp_path):
        root = tmp_path / "runs" / "test-run"
        inputs = root / "inputs"
        inputs.mkdir(parents=True)
        (inputs / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        _write_sections(inputs / "pdf_sections.json", period="2025FY", marker="LEGACY-FY")

        result = _prepare(root, primary_period="2026H1")

        manifest = _manifest(root)
        assert manifest["primary_period"] == "2026H1"
        warnings = manifest["warnings"]
        assert len(warnings) == 1
        assert "2025FY" in warnings[0]
        assert "2026H1" in warnings[0]
        assert result["warnings"] == warnings

        index = _evidence(root)
        sections_source = next(item for item in index["sources"] if item["source_id"] == "pdf_sections")
        assert Path(sections_source["path"]) == (inputs / "pdf_sections.json").resolve()
        assert "LEGACY-FY" in _pdf_section_quotes(root)

    def test_matching_legacy_sections_do_not_warn(self, tmp_path):
        root = tmp_path / "runs" / "test-run"
        inputs = root / "inputs"
        inputs.mkdir(parents=True)
        (inputs / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        _write_sections(inputs / "pdf_sections.json", period="2026H1", marker="MATCH")

        result = _prepare(root, primary_period="2026H1")

        manifest = _manifest(root)
        assert manifest["primary_period"] == "2026H1"
        assert "warnings" not in manifest
        assert result["warnings"] == []
        assert "MATCH" in _pdf_section_quotes(root)

    def test_sections_without_period_metadata_warn(self, tmp_path):
        # Unknown provenance cannot be trusted to match the requested period.
        root = tmp_path / "runs" / "test-run"
        inputs = root / "inputs"
        inputs.mkdir(parents=True)
        (inputs / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        _write_sections(inputs / "pdf_sections.json", period=None, marker="NOPERIOD")

        result = _prepare(root, primary_period="2026H1")

        assert _manifest(root)["primary_period"] == "2026H1"
        assert len(result["warnings"]) == 1
        assert "unknown" in result["warnings"][0]
        assert "2026H1" in result["warnings"][0]
        assert "NOPERIOD" in _pdf_section_quotes(root)

    def test_per_period_file_with_mismatching_metadata_warns(self, tmp_path):
        # Regression: the selected per-period file's own metadata was never checked.
        root = tmp_path / "runs" / "test-run"
        inputs = root / "inputs"
        inputs.mkdir(parents=True)
        (inputs / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        _write_sections(inputs / "pdf_sections_2026H1.json", period="2025FY", marker="WRONG")

        result = _prepare(root, primary_period="2026H1")

        warnings = result["warnings"]
        assert len(warnings) == 1
        assert "pdf_sections_2026H1.json" in warnings[0]
        assert "2025FY" in warnings[0]
        # The requested file is still used; the warning is what makes it audible.
        assert "WRONG" in _pdf_section_quotes(root)

    def test_inputs_directory_empty_is_still_used(self, tmp_path):
        root = tmp_path / "runs" / "test-run"
        (root / "inputs").mkdir(parents=True)
        (root / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")

        _prepare(root)

        manifest = _manifest(root)
        search_root = str((root / "inputs").resolve())
        market = next(item for item in manifest["inputs"] if item["source_id"] == "market_data")
        assert market["path"].startswith(search_root)
        assert market["exists"] is False

    def test_primary_period_without_sections_file_is_safe(self, tmp_path):
        root = tmp_path / "runs" / "test-run"
        inputs = root / "inputs"
        inputs.mkdir(parents=True)
        (inputs / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")

        result = _prepare(root, primary_period="2026FY")

        manifest = _manifest(root)
        assert manifest["primary_period"] == "2026FY"
        assert result["warnings"] == []
        index = _evidence(root)
        sections_source = next(item for item in index["sources"] if item["source_id"] == "pdf_sections")
        assert sections_source["exists"] is False

    def test_without_primary_period_the_per_period_file_is_ignored(self, tmp_path):
        root = tmp_path / "600887_伊利"
        root.mkdir()
        (root / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
        _write_sections(root / "pdf_sections_2026H1.json", period="2026H1", marker="PERIOD-H1")
        _write_sections(root / "pdf_sections.json", period="2025FY", marker="LEGACY-FY")

        result = _prepare(root)

        manifest = _manifest(root)
        assert "primary_period" not in manifest
        assert "warnings" not in manifest
        assert result["primary_period"] == ""
        assert "LEGACY-FY" in _pdf_section_quotes(root)
        assert "PERIOD-H1" not in _pdf_section_quotes(root)


# ============================================================
# manifest compatibility + period validation
# ============================================================

class TestManifestCompatibility:
    """The manifest change is additive only."""

    def test_primary_period_is_the_only_added_field(self, tmp_path):
        def build(base: Path, **kwargs):
            base.mkdir()
            (base / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
            _write_sections(base / "pdf_sections.json", period="2026H1", marker="X")
            return _prepare(base, **kwargs)

        without = build(tmp_path / "without")
        with_primary = build(tmp_path / "with", primary_period="2026H1")

        without_manifest = _manifest(tmp_path / "without")
        with_manifest = _manifest(tmp_path / "with")

        assert set(with_manifest) - set(without_manifest) == {"primary_period"}
        assert not set(without_manifest) - set(with_manifest)
        assert without_manifest["schema_version"] == with_manifest["schema_version"] == "1.0"
        assert "primary_period" not in without_manifest
        assert with_manifest["primary_period"] == "2026H1"
        assert without["primary_period"] == ""

    def test_primary_period_is_normalized(self, tmp_path):
        root = tmp_path / "run"
        root.mkdir()
        _prepare(root, primary_period="2026h1")
        assert _manifest(root)["primary_period"] == "2026H1"

    def test_invalid_primary_period_raises(self, tmp_path):
        root = tmp_path / "run"
        root.mkdir()
        with pytest.raises(ValueError, match="Invalid primary period"):
            _prepare(root, primary_period="2026Q2")
        with pytest.raises(ValueError):
            _normalize_primary_period("2026")

    def test_period_arg_parser_type(self):
        assert _period_arg("2026h1") == "2026H1"
        assert _period_arg(" 2025FY ") == "2025FY"
        with pytest.raises(argparse.ArgumentTypeError, match="invalid period"):
            _period_arg("2026Q2")

    def test_cli_rejects_invalid_period(self, tmp_path):
        completed = subprocess.run(
            [
                sys.executable, "-m", "scripts.results.prepare",
                "--output-dir", str(tmp_path / "run"),
                "--ticker", "600887.SH",
                "--company", "伊利股份",
                "--primary-period", "2026Q2",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 2
        assert "invalid period" in completed.stderr
        assert "Traceback" not in completed.stderr
