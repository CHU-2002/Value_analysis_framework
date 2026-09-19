
# 覆盖需求：REQ-002（同比可比期与按期次章节包）—— AC-1 补上年同期列、AC-2 ``pdf_sections_{period}.json`` 互不覆盖
"""PR2 tests: prior-year comparable display periods + per-period PDF sections.

Covers two deliverables of ``docs/PERIODIC_UPDATE_PLAN.md`` §4/§6:

1. ``InfrastructureMixin._prepare_display_periods`` now adds the prior-year
   same-period column (``2026H1`` -> ``2025H1``) without changing the values or
   the relative order of the pre-existing interim/annual columns.
2. ``scripts/pdf_preprocessor.py`` resolves a report period (explicit
   ``--period`` or inferred from the PDF filename) and names its output
   ``pdf_sections_{period}.json`` while staying backward compatible.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from scripts.pdf_preprocessor import (
    DEFAULT_OUTPUT,
    main as pdf_main,
    parse_args,
    resolve_output_path,
    run_pipeline,
    write_output,
)
from tushare_modules.infrastructure import InfrastructureMixin

REPO_ROOT = Path(__file__).resolve().parents[1]


class _StubClient(InfrastructureMixin):
    """Minimal host exposing the mixin method under test."""

    def __init__(self, fy_end_month: int = 12) -> None:
        self._fy_end_month = fy_end_month


def _frame(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame([{"end_date": end_date, "revenue": revenue} for end_date, revenue in rows])


def _display(rows, fy_end_month: int = 12):
    return _StubClient(fy_end_month)._prepare_display_periods(_frame(rows))


def _labels(rows, fy_end_month: int = 12) -> list[str]:
    return _display(rows, fy_end_month)[1]


# ============================================================
# _prepare_display_periods: prior-year comparables
# ============================================================

class TestComparableDisplayPeriods:
    """Interim periods pull in their prior-year counterpart column."""

    def test_acceptance_example_labels(self):
        """The PR acceptance example: interim, their prior year, then annuals."""
        labels = _labels([
            ("20260630", 7),   # 2026H1
            ("20260331", 8),   # 2026Q1
            ("20251231", 1),   # 2025 annual
            ("20250930", 9),   # 2025Q3 (older than latest annual -> not an interim)
            ("20250630", 10),  # 2025H1 (prior year of 2026H1)
            ("20250331", 11),  # 2025Q1 (prior year of 2026Q1)
            ("20241231", 2),
            ("20231231", 3),
            ("20221231", 4),
            ("20211231", 5),
            ("20201231", 6),
        ])
        assert labels == [
            "2026H1", "2026Q1", "2025H1", "2025Q1",
            "2025", "2024", "2023", "2022", "2021",
        ]

    def test_existing_columns_keep_values_and_relative_order(self):
        """Only new columns are added; the old columns keep values and order."""
        rows = [
            ("20260630", 7),
            ("20260331", 8),
            ("20250630", 10),
            ("20250331", 11),
            ("20251231", 1),
            ("20241231", 2),
            ("20231231", 3),
        ]
        display_df, labels = _display(rows)
        values = dict(zip(display_df["end_date"], display_df["revenue"]))

        # Prior-year columns were inserted before the annual block.
        assert labels == ["2026H1", "2026Q1", "2025H1", "2025Q1", "2025", "2024", "2023"]

        # Dropping the prior-year columns reproduces the pre-PR2 output exactly.
        comparable_labels = {"2025H1", "2025Q1"}
        kept = [(label, values[end_date]) for label, end_date in zip(labels, display_df["end_date"])
                if label not in comparable_labels]
        assert kept == [
            ("2026H1", 7),
            ("2026Q1", 8),
            ("2025", 1),
            ("2024", 2),
            ("2023", 3),
        ]

    def test_values_map_to_the_input_rows(self):
        """Every displayed row carries the values of its own end_date."""
        rows = [
            ("20260630", 7),
            ("20250630", 10),
            ("20251231", 1),
            ("20241231", 2),
        ]
        display_df, labels = _display(rows)
        assert labels == ["2026H1", "2025H1", "2025", "2024"]
        assert display_df["revenue"].tolist() == [7, 10, 1, 2]

    def test_no_comparable_when_prior_year_absent(self):
        """A missing prior-year period adds nothing."""
        labels = _labels([
            ("20260630", 1),
            ("20251231", 2),
            ("20241231", 3),
        ])
        assert labels == ["2026H1", "2025", "2024"]

    def test_q3_prior_year_comparable(self):
        """2026Q3 pulls in 2025Q3, which sits between interim and annual."""
        labels = _labels([
            ("20260930", 1),
            ("20250930", 2),
            ("20251231", 3),
            ("20241231", 4),
        ])
        assert labels == ["2026Q3", "2025Q3", "2025", "2024"]

    def test_comparable_already_in_interim_is_not_duplicated(self):
        """When the prior-year period is itself an interim, it is not repeated."""
        display_df, labels = _display([
            ("20260630", 1),
            ("20250630", 2),
            ("20241231", 3),
            ("20231231", 4),
        ])
        assert labels == ["2026H1", "2025H1", "2024", "2023"]
        assert len(display_df) == len(set(display_df["end_date"]))
        assert display_df["end_date"].is_unique

    def test_cascading_comparables_are_added_once(self):
        """Every retained interim contributes at most one extra column."""
        labels = _labels([
            ("20260630", 1),
            ("20250630", 2),
            ("20240630", 3),
            ("20241231", 4),
            ("20231231", 5),
            ("20221231", 6),
        ])
        assert labels == ["2026H1", "2025H1", "2024H1", "2024", "2023", "2022"]
        assert len(labels) == len(set(labels))

    def test_duplicate_input_end_dates_are_deduplicated(self):
        """Duplicate end_date rows never leak into the display columns."""
        rows = [
            ("20260630", 1),
            ("20260630", 1),
            ("20250630", 2),
            ("20251231", 3),
            ("20251231", 3),
            ("20241231", 4),
        ]
        display_df, labels = _display(rows)
        assert labels == ["2026H1", "2025H1", "2025", "2024"]
        assert display_df["end_date"].is_unique
        assert len(display_df) == 4

    def test_unsorted_input_is_order_stable(self):
        """Row order in the input does not change the resulting columns."""
        rows = [
            ("20260630", 1),
            ("20260331", 2),
            ("20250630", 3),
            ("20250331", 4),
            ("20251231", 5),
            ("20241231", 6),
            ("20231231", 7),
        ]
        expected = ["2026H1", "2026Q1", "2025H1", "2025Q1", "2025", "2024", "2023"]
        assert _labels(rows) == expected
        assert _labels(list(reversed(rows))) == expected
        shuffled = pd.DataFrame(rows, columns=["end_date", "revenue"]).sample(frac=1, random_state=7)
        shuffled_rows = list(zip(shuffled["end_date"], shuffled["revenue"]))
        assert _labels(shuffled_rows) == expected

    def test_non_december_fiscal_year(self):
        """September fiscal year: interim comparables use the same month/day."""
        rows = [
            ("20250630", 1),   # 2025H1
            ("20240630", 2),   # 2024H1 (prior year comparable)
            ("20240930", 3),   # FY2024
            ("20230930", 4),   # FY2023
        ]
        display_df, labels = _display(rows, fy_end_month=9)
        assert labels == ["2025H1", "2024H1", "2024", "2023"]
        # The FY end month is still classified as annual, never as interim.
        assert labels[2:] == ["2024", "2023"]

    def test_june_fiscal_year_labels_stay_consistent_with_existing_format(self):
        """A June FY keeps the existing ``YYYY_MMDD`` label for off-cycle rows."""
        rows = [
            ("20251231", 1),
            ("20241231", 2),
            ("20250630", 3),   # FY2025 (June)
            ("20240630", 4),   # FY2024 (June)
        ]
        labels = _labels(rows, fy_end_month=6)
        assert labels == ["2025_1231", "2024_1231", "2025", "2024"]

    def test_max_annual_bound_is_preserved(self):
        """Adding comparables does not extend the annual history window."""
        rows = [
            ("20260630", 1),
            ("20250630", 2),
            ("20251231", 3),
            ("20241231", 4),
            ("20231231", 5),
            ("20221231", 6),
            ("20211231", 7),
            ("20201231", 8),
        ]
        labels = _labels(rows)
        assert labels == ["2026H1", "2025H1", "2025", "2024", "2023", "2022", "2021"]
        assert labels.count("2020") == 0

    def test_interim_only_dataframe(self):
        """Without annual data the interim rows are kept and compared."""
        labels = _labels([
            ("20260630", 1),
            ("20250630", 2),
        ])
        assert labels == ["2026H1", "2025H1"]

    def test_empty_dataframe_is_safe(self):
        empty = pd.DataFrame(columns=["end_date", "revenue"])
        result_df, labels = _StubClient()._prepare_display_periods(empty)
        assert labels == []
        assert result_df.empty

    def test_missing_end_date_column_is_safe(self):
        result_df, labels = _StubClient()._prepare_display_periods(pd.DataFrame([{"revenue": 1}]))
        assert labels == []
        assert list(result_df.columns) == ["revenue"]

    def test_real_client_inherits_the_behavior(self):
        """The behavior is wired into the real TushareClient, not just the mixin."""
        from tushare_collector import TushareClient

        with patch("tushare_collector.ts") as mock_ts:
            mock_ts.pro_api.return_value = MagicMock()
            client = TushareClient("test_token")

        labels = client._prepare_display_periods(_frame([
            ("20260630", 1),
            ("20250630", 2),
            ("20251231", 3),
            ("20241231", 4),
        ]))[1]
        assert labels == ["2026H1", "2025H1", "2025", "2024"]


# ============================================================
# pdf_preprocessor: period resolution and output naming
# ============================================================

class TestPdfPreprocessorPeriodOutput:
    """``--period`` / filename inference drive the output file name."""

    def test_explicit_output_always_wins(self):
        assert resolve_output_path("/tmp/x/600887_2025_年报.pdf", "2025FY", "custom.json") == "custom.json"

    def test_period_defaults_next_to_the_pdf(self):
        assert (
            resolve_output_path("/tmp/x/600887_2025_年报.pdf", "2025FY", None)
            == "/tmp/x/pdf_sections_2025FY.json"
        )

    def test_unresolved_period_keeps_the_legacy_default(self):
        assert resolve_output_path("report.pdf", "", None) == DEFAULT_OUTPUT

    def test_write_output_records_period_metadata(self, tmp_path):
        output_path = tmp_path / "pdf_sections_2026H1.json"
        result = write_output({"P2": "受限资产"}, "/tmp/600887_2026_中报.pdf", 10, str(output_path),
                              period="2026H1")
        assert result["metadata"]["period"] == "2026H1"
        assert result["metadata"]["pdf_file"] == "600887_2026_中报.pdf"
        on_disk = json.loads(output_path.read_text(encoding="utf-8"))
        assert on_disk["metadata"]["period"] == "2026H1"
        assert on_disk["metadata"]["total_pages"] == 10

    def test_write_output_unresolved_period_is_empty_string(self, tmp_path):
        """Unresolvable periods are recorded as an empty string (documented choice)."""
        output_path = tmp_path / "out.json"
        result = write_output({"P2": "受限资产"}, "report.pdf", 10, str(output_path))
        assert result["metadata"]["period"] == ""

    def test_existing_metadata_fields_are_preserved(self, tmp_path):
        output_path = tmp_path / "out.json"
        result = write_output({"P2": "x"}, "report.pdf", 42, str(output_path), period="2025FY")
        assert set(["pdf_file", "total_pages", "extract_time", "sections_found",
                    "sections_total", "period"]).issubset(result["metadata"].keys())
        assert result["metadata"]["sections_found"] == 1


class TestPdfPreprocessorParseArgsPeriods:
    """``parse_args`` resolves the period and the default output path."""

    def test_legacy_default_when_period_unresolvable(self):
        args = parse_args(["--pdf", "test.pdf"])
        assert args.period == ""
        assert args.output == DEFAULT_OUTPUT

    def test_period_inferred_from_filename(self):
        args = parse_args(["--pdf", "/tmp/x/600887_2025_年报.pdf"])
        assert args.period == "2025FY"
        assert args.output == "/tmp/x/pdf_sections_2025FY.json"

    def test_period_inferred_from_interim_filename(self):
        args = parse_args(["--pdf", "600887_2026_中报.pdf"])
        assert args.period == "2026H1"
        assert args.output == "pdf_sections_2026H1.json"

    def test_period_inference_ignores_parent_directory_year(self):
        # Regression: a year in the parent directory used to win over the
        # report label in the filename, silently mislabelling every section.
        args = parse_args(["--pdf", "/data/2025年报分析/600887_2026_中报.pdf"])
        assert args.period == "2026H1"
        assert args.output == "/data/2025年报分析/pdf_sections_2026H1.json"

    def test_period_inference_ignores_parent_directory_without_filename_period(self):
        args = parse_args(["--pdf", "/data/2024年报归档/report.pdf"])
        assert args.period == ""
        assert args.output == DEFAULT_OUTPUT

    def test_explicit_period_wins_over_filename(self):
        args = parse_args(["--pdf", "600887_2025_年报.pdf", "--period", "2026H1"])
        assert args.period == "2026H1"
        assert args.output == "pdf_sections_2026H1.json"

    def test_explicit_output_wins_over_period(self):
        args = parse_args(["--pdf", "600887_2025_年报.pdf", "--output", "custom.json"])
        assert args.period == "2025FY"
        assert args.output == "custom.json"

    def test_period_is_normalized(self):
        args = parse_args(["--pdf", "report.pdf", "--period", "2026h1"])
        assert args.period == "2026H1"

    def test_empty_output_argument_is_rejected(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            parse_args(["--pdf", "report.pdf", "--output", ""])
        assert excinfo.value.code == 2
        assert "must not be empty" in capsys.readouterr().err

    @pytest.mark.parametrize("bad", ["2026Q2", "2026", "H1", "2026M6", ""])
    def test_invalid_period_exits_non_zero_without_traceback(self, bad):
        with pytest.raises(SystemExit) as excinfo:
            parse_args(["--pdf", "report.pdf", "--period", bad])
        assert excinfo.value.code == 2

    def test_invalid_period_cli_message(self, capsys):
        with pytest.raises(SystemExit):
            parse_args(["--pdf", "report.pdf", "--period", "2026Q2"])
        captured = capsys.readouterr()
        assert "invalid period" in captured.err
        assert "2026Q2" in captured.err

    def test_invalid_period_subprocess_exit_code(self):
        completed = subprocess.run(
            [sys.executable, "scripts/pdf_preprocessor.py", "--pdf", "report.pdf", "--period", "2026Q2"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 2
        assert "invalid period" in completed.stderr
        assert "Traceback" not in completed.stderr

    def test_dry_run_reports_inferred_output(self):
        completed = subprocess.run(
            [sys.executable, "scripts/pdf_preprocessor.py", "--pdf", "600887_2025_年报.pdf", "--dry-run"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0
        assert "Period: 2025FY" in completed.stdout
        assert "pdf_sections_2025FY.json" in completed.stdout

    def test_dry_run_keeps_legacy_default_for_unresolvable_pdf(self):
        completed = subprocess.run(
            [sys.executable, "scripts/pdf_preprocessor.py", "--pdf", "report.pdf", "--dry-run"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0
        assert "Period: (unresolved)" in completed.stdout
        assert f"Output: {DEFAULT_OUTPUT}" in completed.stdout


class TestPdfPreprocessorPipelinePeriods:
    """The pipeline records the period and can write next to the PDF."""

    @patch("scripts.pdf_preprocessor.extract_all_pages")
    @patch("scripts.config.validate_pdf")
    def test_run_pipeline_records_period(self, mock_validate, mock_extract, tmp_path):
        mock_validate.return_value = (True, "Valid PDF")
        mock_extract.return_value = [(1, "管理层讨论与分析 经营情况回顾")]
        output_path = tmp_path / "pdf_sections_2026H1.json"
        result = run_pipeline("/tmp/600887_2026_中报.pdf", str(output_path), period="2026H1")
        assert result["metadata"]["period"] == "2026H1"
        on_disk = json.loads(output_path.read_text(encoding="utf-8"))
        assert on_disk["metadata"]["period"] == "2026H1"

    @patch("scripts.pdf_preprocessor.extract_all_pages")
    @patch("scripts.config.validate_pdf")
    def test_main_writes_pdf_sections_period_file(self, mock_validate, mock_extract, tmp_path, monkeypatch):
        mock_validate.return_value = (True, "Valid PDF")
        mock_extract.return_value = [(1, "非经常性损益明细 政府补贴")]
        pdf_path = tmp_path / "600887_2026_中报.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 dummy")

        monkeypatch.setattr(sys, "argv", ["pdf_preprocessor.py", "--pdf", str(pdf_path)])
        pdf_main()

        output_path = tmp_path / "pdf_sections_2026H1.json"
        assert output_path.is_file()
        assert json.loads(output_path.read_text(encoding="utf-8"))["metadata"]["period"] == "2026H1"

    @patch("scripts.pdf_preprocessor.extract_all_pages")
    @patch("scripts.config.validate_pdf")
    def test_main_keeps_legacy_default_for_unresolvable_pdf(self, mock_validate, mock_extract, tmp_path, monkeypatch):
        mock_validate.return_value = (True, "Valid PDF")
        mock_extract.return_value = [(1, "非经常性损益明细 政府补贴")]
        pdf_path = tmp_path / "report.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 dummy")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["pdf_preprocessor.py", "--pdf", str(pdf_path)])
        pdf_main()

        expected = tmp_path / "output" / "pdf_sections.json"
        assert expected.is_file()
        assert json.loads(expected.read_text(encoding="utf-8"))["metadata"]["period"] == ""
