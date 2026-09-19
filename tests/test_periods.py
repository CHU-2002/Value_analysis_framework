
# 覆盖需求：REQ-001（定期报告发现与下载）—— AC-3 期次标识与 (year, type) 双向互转
"""Tests for scripts/periods.py"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from periods import (
    comparable_period,
    filename_to_period,
    is_valid_period,
    list_periods_between,
    make_period,
    months_since,
    normalize_report_type,
    parse_period,
    parse_period_from_title,
    period_sort_key,
    period_to_filename,
    previous_period,
    report_type_keywords,
    single_quarter_base,
    sort_periods,
)


class TestNormalizeReportType:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("年报", "年报"),
            ("annual", "年报"),
            ("FY", "年报"),
            ("y", "y"),  # unknown values pass through unchanged
            ("interim", "中报"),
            ("h1", "中报"),
            ("半年报", "中报"),
            ("一季报", "一季报"),
            ("q1", "一季报"),
            ("Q3", "三季报"),
            ("third quarter", "三季报"),
        ],
    )
    def test_aliases(self, raw, expected):
        assert normalize_report_type(raw) == expected


class TestMakeParsePeriod:
    @pytest.mark.parametrize(
        ("year", "report_type", "expected"),
        [
            (2026, "年报", "2026FY"),
            (2026, "annual", "2026FY"),
            (2026, "中报", "2026H1"),
            (2026, "一季报", "2026Q1"),
            (2026, "三季报", "2026Q3"),
        ],
    )
    def test_make_period(self, year, report_type, expected):
        assert make_period(year, report_type) == expected

    def test_make_period_rejects_unknown_type(self):
        with pytest.raises(ValueError):
            make_period(2026, "季报")

    def test_parse_period_round_trip(self):
        assert parse_period("2026H1") == (2026, "中报")

    def test_parse_period_is_case_insensitive(self):
        assert parse_period("2026q1") == (2026, "一季报")
        assert is_valid_period("2026h1") is True

    def test_parse_period_rejects_garbage(self):
        with pytest.raises(ValueError):
            parse_period("2026-X")

    def test_is_valid_period(self):
        assert is_valid_period("2026FY") is True
        assert is_valid_period("2026Q2") is False
        assert is_valid_period("FY2026") is False
        assert is_valid_period(None) is False

    def test_months_since_covers_older_periods(self):
        from datetime import date

        assert months_since("2026Q1", today=date(2026, 9, 19)) == 9
        assert months_since("2020Q1", today=date(2026, 9, 19)) == 81
        assert months_since("2026Q3", today=date(2026, 9, 19)) == 9

    def test_report_type_keywords_shared_with_title_parsing(self):
        assert "三季度报告" in report_type_keywords("三季报")
        assert "半年报" in report_type_keywords("中报")
        assert "年報" in report_type_keywords("年报")
        assert report_type_keywords("季报") == ()


class TestPeriodOrdering:
    def test_sort_key_is_chronological_within_year(self):
        assert period_sort_key("2026Q1") == (2026, 1)
        assert period_sort_key("2026H1") == (2026, 2)
        assert period_sort_key("2026Q3") == (2026, 3)
        assert period_sort_key("2026FY") == (2026, 4)

    def test_sort_periods_newest_first(self):
        periods = ["2025FY", "2026Q1", "2024FY", "2026H1", "bogus"]
        assert sort_periods(periods) == ["2026H1", "2026Q1", "2025FY", "2024FY"]

    def test_list_periods_between_inclusive_newest_first(self):
        assert list_periods_between("2025FY", "2026H1") == [
            "2026H1",
            "2026Q1",
            "2025FY",
        ]

    def test_list_periods_between_accepts_reversed_bounds(self):
        assert list_periods_between("2026H1", "2025FY") == list_periods_between(
            "2025FY", "2026H1"
        )


class TestPeriodNeighbours:
    def test_comparable_period(self):
        assert comparable_period("2026H1") == "2025H1"
        assert comparable_period("2026FY") == "2025FY"

    @pytest.mark.parametrize(
        ("period", "expected"),
        [
            ("2026Q1", "2025FY"),
            ("2026H1", "2026Q1"),
            ("2026Q3", "2026H1"),
            ("2026FY", "2026Q3"),
        ],
    )
    def test_previous_period(self, period, expected):
        assert previous_period(period) == expected

    @pytest.mark.parametrize(
        ("period", "expected"),
        [
            ("2026Q1", None),
            ("2026H1", "2026Q1"),
            ("2026Q3", "2026H1"),
            ("2026FY", "2026Q3"),
        ],
    )
    def test_single_quarter_base(self, period, expected):
        assert single_quarter_base(period) == expected


class TestParsePeriodFromTitle:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("五粮液：2025年年度报告", "2025FY"),
            ("中国移动：2025年年度报告摘要", "2025FY"),
            ("五粮液：2025年半年度报告（更新后）", "2025H1"),
            ("伊利股份2026年第一季度报告", "2026Q1"),
            ("伊利股份2026年第三季度报告", "2026Q3"),
            ("2025年中期报告", "2025H1"),
            ("2025年半年报", "2025H1"),
            ("2025年第一季報", "2025Q1"),
            ("2025年半年度報告", "2025H1"),
            ("宁德时代2025年年度报告", "2025FY"),
        ],
    )
    def test_parses_common_titles(self, title, expected):
        assert parse_period_from_title(title) == expected

    def test_half_year_is_not_parsed_as_annual(self):
        # "半年度报告" contains "年度报告" and "半年报" contains "年报".
        assert parse_period_from_title("2025年半年度报告") == "2025H1"
        assert parse_period_from_title("2025年半年报") == "2025H1"

    def test_report_type_filter_rejects_other_types(self):
        assert parse_period_from_title("2025年年度报告", "中报") is None
        assert parse_period_from_title("2025年半年度报告", "中报") == "2025H1"

    def test_ignores_unrelated_year(self):
        # Only the year adjacent to the report label counts.
        assert parse_period_from_title("关于2024年融资计划及2025年年度报告的公告") == "2025FY"

    def test_returns_none_without_year_or_label(self):
        assert parse_period_from_title("年度报告") is None
        assert parse_period_from_title("2025年董事会决议") is None
        assert parse_period_from_title("") is None


class TestFilenameMapping:
    @pytest.mark.parametrize(
        ("period", "expected"),
        [
            ("2025FY", "600887_2025_年报.pdf"),
            ("2026H1", "600887_2026_中报.pdf"),
            ("2026Q1", "600887_2026_一季报.pdf"),
            ("2026Q3", "600887_2026_三季报.pdf"),
        ],
    )
    def test_period_to_filename(self, period, expected):
        assert period_to_filename("SH600887", period) == expected

    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("600887_2025_年报.pdf", "2025FY"),
            ("000858_2026_中报.pdf", "2026H1"),
            ("300750_2026_一季报.pdf", "2026Q1"),
            ("600941_2026_三季报.pdf", "2026Q3"),
            ("600887_2025_annual.pdf", "2025FY"),
            ("600887_2025年年度报告.pdf", "2025FY"),
        ],
    )
    def test_filename_to_period(self, filename, expected):
        assert filename_to_period(filename) == expected

    def test_filename_to_period_returns_none_for_unrelated_name(self):
        assert filename_to_period("1988edf2-2e42-46ff-8263-767fac04b07b.pdf") is None
