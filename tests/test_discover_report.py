"""Tests for scripts/discover_report.py"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from discover_report import (
    CNINFO_ALL_REGULAR_CATEGORIES,
    EXIT_NETWORK_FAILURE,
    EXIT_NO_MATCH,
    EXIT_SUCCESS,
    build_stockpage_url,
    discover_latest_period,
    discover_periods,
    discover_report,
    extract_cninfo_period_candidates,
    extract_pdf_candidates,
    get_cninfo_category,
    is_excluded_title,
    main,
    score_candidate,
    select_best_candidate,
    should_prefer_cninfo,
)


SAMPLE_HTML = """
<ul class="news_list stat" stat="f10_spqk_gsgg">
  <li class="clearfix">
    <span class="news_title fl"><a href="http://notice.10jqka.com.cn/api/pdf/187b8c07ff570bbc.pdf" target="_blank">五 粮 液：2025年年度报告摘要</a></span>
    <span class="news_date"><em>2026-04-30</em></span>
  </li>
  <li class="clearfix">
    <span class="news_title fl"><a href="http://notice.10jqka.com.cn/api/pdf/4db6de71171448c1.pdf" target="_blank">五 粮 液：2025年年度报告</a></span>
    <span class="news_date"><em>2026-04-30</em></span>
  </li>
  <li class="clearfix">
    <span class="news_title fl"><a href="http://notice.10jqka.com.cn/api/pdf/67700c8e3c51b87f.pdf" target="_blank">五 粮 液：2025年半年度报告（更新后）</a></span>
    <span class="news_date"><em>2026-04-30</em></span>
  </li>
</ul>
"""


class TestBuildStockpageUrl:
    def test_builds_a_share_url(self):
        assert build_stockpage_url("SZ000858") == "https://stockpage.10jqka.com.cn/000858/"

    def test_builds_hk_url_from_digits(self):
        assert build_stockpage_url("00700") == "https://stockpage.10jqka.com.cn/00700/"


class TestExtractPdfCandidates:
    def test_extracts_candidates(self):
        candidates = extract_pdf_candidates(SAMPLE_HTML)
        assert len(candidates) == 3
        assert candidates[0]["url"].startswith("https://notice.10jqka.com.cn/api/pdf/")
        assert candidates[0]["title"] == "五粮液：2025年年度报告摘要"


class TestSelectBestCandidate:
    def test_rejects_summary_for_annual(self):
        summary = {"title": "五粮液：2025年年度报告摘要", "url": "u", "date": "d"}
        assert score_candidate(summary, "2025", "年报") is None

    def test_selects_full_annual_report(self):
        candidates = extract_pdf_candidates(SAMPLE_HTML)
        best = select_best_candidate(candidates, "2025", "年报")
        assert best is not None
        assert best["title"] == "五粮液：2025年年度报告"
        assert best["url"] == "https://notice.10jqka.com.cn/api/pdf/4db6de71171448c1.pdf"

    def test_falls_back_to_summary_when_full_annual_missing(self):
        candidates = [
            {"title": "中国移动：2025年年度报告摘要", "url": "summary", "date": "2026-03-27"},
            {"title": "中国移动：2025年末期利润分配方案的公告", "url": "dividend", "date": "2026-03-27"},
        ]

        best = select_best_candidate(candidates, "2025", "年报")

        assert best is not None
        assert best["title"] == "中国移动：2025年年度报告摘要"
        assert best["url"] == "summary"
        assert best["match_quality"] == "summary_fallback"


class TestDiscoverReportFallbacks:
    @patch("discover_report.requests.post")
    @patch("discover_report.requests.get")
    def test_uses_cninfo_when_10jqka_only_has_summary(self, mock_get, mock_post):
        page_response = MagicMock()
        page_response.text = """
        <html>
          <title>中国移动(600941)个股行情</title>
          <strong stockname="中国移动"></strong>
          <a href="http://notice.10jqka.com.cn/api/pdf/summary.pdf">中国移动：2025年年度报告摘要</a><em>2026-03-27</em>
        </html>
        """
        page_response.raise_for_status = MagicMock()
        mock_get.return_value = page_response

        cninfo_response = MagicMock()
        cninfo_response.raise_for_status = MagicMock()
        cninfo_response.json.return_value = {
            "announcements": [
                {
                    "announcementTitle": "中国移动：2025年年度报告",
                    "announcementTime": 1774540800000,
                    "adjunctUrl": "finalpage/2026-03-27/1225036280.PDF",
                }
            ]
        }
        mock_post.return_value = cninfo_response

        _, candidates, best = discover_report("600941", "2025", "年报")

        assert best is not None
        assert best["title"] == "中国移动：2025年年度报告"
        assert best["url"] == "https://static.cninfo.com.cn/finalpage/2026-03-27/1225036280.PDF"
        assert any(candidate.get("source") == "cninfo" for candidate in candidates)

    @patch("discover_report.requests.post")
    @patch("discover_report.requests.get")
    def test_cninfo_retries_with_company_name_variant(self, mock_get, mock_post):
        page_response = MagicMock()
        page_response.text = """
        <html>
          <title>五粮液(000858)个股行情</title>
          <strong stockname="五粮液"></strong>
        </html>
        """
        page_response.raise_for_status = MagicMock()
        mock_get.return_value = page_response

        empty_response = MagicMock()
        empty_response.raise_for_status = MagicMock()
        empty_response.json.return_value = {"announcements": []}

        success_response = MagicMock()
        success_response.raise_for_status = MagicMock()
        success_response.json.return_value = {
            "announcements": [
                {
                    "announcementTitle": "2025年年度报告",
                    "announcementTime": 1772236800000,
                    "adjunctUrl": "finalpage/2026-02-28/1234567890.PDF",
                }
            ]
        }
        mock_post.side_effect = [empty_response, empty_response, success_response]

        _, _, best = discover_report("000858", "2025", "年报")

        assert best is not None
        assert best["url"] == "https://static.cninfo.com.cn/finalpage/2026-02-28/1234567890.PDF"
        posted_keywords = [call.kwargs["data"]["searchkey"] for call in mock_post.call_args_list]
        assert posted_keywords == ["000858", "五粮液", "五 粮 液"]

    @patch("discover_report.requests.post")
    @patch("discover_report.requests.get")
    def test_a_share_annual_still_works_when_stockpage_request_fails(self, mock_get, mock_post):
        import requests

        mock_get.side_effect = requests.exceptions.ConnectionError("stockpage down")

        cninfo_response = MagicMock()
        cninfo_response.raise_for_status = MagicMock()
        cninfo_response.json.return_value = {
            "announcements": [
                {
                    "announcementTitle": "宁德时代2025年年度报告",
                    "announcementTime": 1773014400000,
                    "adjunctUrl": "finalpage/2026-03-08/1225002214.PDF",
                }
            ]
        }
        mock_post.return_value = cninfo_response

        _, candidates, best = discover_report("300750", "2025", "年报")

        assert best is not None
        assert best["title"] == "宁德时代2025年年度报告"
        assert candidates[0]["source"] == "cninfo"


class TestMain:
    @patch("discover_report.requests.post")
    @patch("discover_report.requests.get")
    def test_success_flow(self, mock_get, mock_post, capsys):
        response = MagicMock()
        response.text = SAMPLE_HTML
        response.raise_for_status = MagicMock()
        mock_get.return_value = response

        cninfo_response = MagicMock()
        cninfo_response.raise_for_status = MagicMock()
        cninfo_response.json.return_value = {"announcements": []}
        mock_post.return_value = cninfo_response

        with pytest.raises(SystemExit) as exc_info:
            main(["--stock-code", "000858", "--year", "2025", "--report-type", "年报"])

        assert exc_info.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "status: SUCCESS" in out
        assert "report_url: https://notice.10jqka.com.cn/api/pdf/4db6de71171448c1.pdf" in out

    @patch("discover_report.requests.post")
    @patch("discover_report.requests.get")
    def test_no_match_exit_code(self, mock_get, mock_post):
        response = MagicMock()
        response.text = SAMPLE_HTML
        response.raise_for_status = MagicMock()
        mock_get.return_value = response

        # CNINFO must be mocked too; a real POST would escape to the network.
        cninfo_response = MagicMock()
        cninfo_response.raise_for_status = MagicMock()
        cninfo_response.json.return_value = {"announcements": []}
        mock_post.return_value = cninfo_response

        with pytest.raises(SystemExit) as exc_info:
            main(["--stock-code", "000858", "--year", "2024", "--report-type", "年报"])

        assert exc_info.value.code == EXIT_NO_MATCH

    @patch("discover_report.requests.post")
    @patch("discover_report.requests.get")
    def test_network_failure_exit_code(self, mock_get, mock_post):
        import requests

        mock_get.side_effect = requests.exceptions.ConnectionError("network down")

        cninfo_response = MagicMock()
        cninfo_response.raise_for_status = MagicMock()
        cninfo_response.json.return_value = {"announcements": []}
        mock_post.return_value = cninfo_response

        with pytest.raises(SystemExit) as exc_info:
            main(["--stock-code", "000858", "--year", "2025", "--report-type", "年报"])

        assert exc_info.value.code == EXIT_NETWORK_FAILURE


# --- Periodic (quarterly / interim / annual) discovery ---

PERIODIC_ANNOUNCEMENTS = {
    "announcements": [
        {
            "announcementTitle": "五粮液：2026年半年度报告",
            "announcementTime": 1787875200000,  # 2026-08-28
            "adjunctUrl": "finalpage/2026-08-28/h1-original.PDF",
        },
        {
            "announcementTitle": "五粮液：2026年半年度报告（更新后）",
            "announcementTime": 1788048000000,  # 2026-08-30
            "adjunctUrl": "finalpage/2026-08-30/h1-updated.PDF",
        },
        {
            "announcementTitle": "五粮液：2026年半年度报告摘要",
            "announcementTime": 1787875200000,
            "adjunctUrl": "finalpage/2026-08-28/h1-summary.PDF",
        },
        {
            "announcementTitle": "五粮液：关于2026年半年度报告的更正公告",
            "announcementTime": 1788048000000,
            "adjunctUrl": "finalpage/2026-08-30/h1-correction.PDF",
        },
        {
            "announcementTitle": "五粮液：2026年第一季度报告",
            "announcementTime": 1777132800000,  # 2026-04-25
            "adjunctUrl": "finalpage/2026-04-25/q1.PDF",
        },
        {
            "announcementTitle": "五粮液：2025年年度报告",
            "announcementTime": 1774540800000,  # 2026-03-27
            "adjunctUrl": "finalpage/2026-03-27/fy2025.PDF",
        },
    ]
}


def _cninfo_mock(payload):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = payload
    return response


class TestCninfoCategories:
    @pytest.mark.parametrize(
        ("report_type", "expected"),
        [
            ("年报", "category_ndbg_szsh"),
            ("annual", "category_ndbg_szsh"),
            ("中报", "category_bndbg_szsh"),
            ("半年报", "category_bndbg_szsh"),
            ("一季报", "category_yjdbg_szsh"),
            ("三季报", "category_sjdbg_szsh"),
        ],
    )
    def test_category_mapping(self, report_type, expected):
        assert get_cninfo_category(report_type) == expected

    def test_unknown_report_type_has_no_category(self):
        assert get_cninfo_category("季报") == ""

    def test_all_regular_categories_cover_the_four_types(self):
        for category in (
            "category_ndbg_szsh",
            "category_bndbg_szsh",
            "category_yjdbg_szsh",
            "category_sjdbg_szsh",
        ):
            assert category in CNINFO_ALL_REGULAR_CATEGORIES

    def test_cninfo_preferred_for_non_annual_a_shares(self):
        assert should_prefer_cninfo("600887", "中报") is True
        assert should_prefer_cninfo("000858", "一季报") is True
        assert should_prefer_cninfo("300750", "三季报") is True
        assert should_prefer_cninfo("600887", "年报") is True
        assert should_prefer_cninfo("00700", "中报") is False

    @pytest.mark.parametrize(
        "title",
        [
            "五粮液：2026年半年度报告（英文版）",
            "五粮液：关于取消2026年第一季度报告的公告",
            "五粮液：2026年半年度报告业绩说明会",
            "五粮液：关于2026年半年度报告的问询函回复",
            "五粮液：2026年半年度报告（H股）",
        ],
    )
    def test_periodic_side_documents_are_excluded(self, title):
        assert is_excluded_title(title) is True

    def test_plain_periodic_title_is_not_excluded(self):
        assert is_excluded_title("五粮液：2026年半年度报告") is False


class TestExtractCninfoPeriodCandidates:
    def test_tags_periods_and_filters_side_documents(self):
        candidates = extract_cninfo_period_candidates(PERIODIC_ANNOUNCEMENTS)
        periods = [candidate["period"] for candidate in candidates]
        assert periods == ["2026H1", "2026H1", "2026Q1", "2025FY"]
        assert all(candidate["source"] == "cninfo" for candidate in candidates)
        assert all(candidate["url"].startswith("https://static.cninfo.com.cn/") for candidate in candidates)


class TestDiscoverPeriods:
    @patch("discover_report.requests.post")
    def test_returns_newest_period_first_and_dedupes(self, mock_post):
        mock_post.return_value = _cninfo_mock(PERIODIC_ANNOUNCEMENTS)

        periods = discover_periods("000858", "五粮液")

        assert [candidate["period"] for candidate in periods] == ["2026H1", "2026Q1", "2025FY"]
        # The corrected ("更新后") version wins for the same period.
        assert periods[0]["url"].endswith("h1-updated.PDF")
        assert mock_post.call_count == 1

    @patch("discover_report.requests.post")
    def test_query_covers_all_regular_categories(self, mock_post):
        mock_post.return_value = _cninfo_mock(PERIODIC_ANNOUNCEMENTS)

        discover_periods("000858", "五粮液")

        data = mock_post.call_args.kwargs["data"]
        assert data["category"] == CNINFO_ALL_REGULAR_CATEGORIES
        assert data["column"] == "szse"
        assert "~" in data["seDate"]
        assert data["searchkey"] == "000858"

    @patch("discover_report.requests.post")
    def test_report_type_filter(self, mock_post):
        mock_post.return_value = _cninfo_mock(PERIODIC_ANNOUNCEMENTS)

        periods = discover_periods("000858", "五粮液", report_type="中报")

        assert [candidate["period"] for candidate in periods] == ["2026H1"]

    @patch("discover_report.requests.post")
    def test_non_a_share_returns_empty_without_request(self, mock_post):
        assert discover_periods("00700", "腾讯") == []
        mock_post.assert_not_called()

    @patch("discover_report.requests.post")
    def test_unknown_report_type_returns_empty(self, mock_post):
        assert discover_periods("000858", "五粮液", report_type="季报") == []
        mock_post.assert_not_called()

    @patch("discover_report.requests.post")
    def test_no_announcements_returns_empty(self, mock_post):
        mock_post.return_value = _cninfo_mock({"announcements": []})
        assert discover_periods("000858", "五粮液") == []

    @patch("discover_report.requests.post")
    def test_latest_period_is_first_result(self, mock_post):
        mock_post.return_value = _cninfo_mock(PERIODIC_ANNOUNCEMENTS)
        latest = discover_latest_period("000858", "五粮液")
        assert latest is not None
        assert latest["period"] == "2026H1"
        assert latest["report_type"] == "中报"


class TestDiscoverInterimViaCninfo:
    @patch("discover_report.requests.post")
    @patch("discover_report.requests.get")
    def test_interim_report_uses_cninfo_category(self, mock_get, mock_post):
        page_response = MagicMock()
        page_response.text = """
        <html>
          <title>五粮液(000858)个股行情</title>
          <strong stockname="五粮液"></strong>
        </html>
        """
        page_response.raise_for_status = MagicMock()
        mock_get.return_value = page_response

        mock_post.return_value = _cninfo_mock(
            {
                "announcements": [
                    {
                        "announcementTitle": "五粮液：2026年半年度报告",
                        "announcementTime": 1787875200000,
                        "adjunctUrl": "finalpage/2026-08-28/h1.PDF",
                    }
                ]
            }
        )

        _, _, best = discover_report("000858", "2026", "中报")

        assert best is not None
        assert best["url"].endswith("h1.PDF")
        assert mock_post.call_args.kwargs["data"]["category"] == "category_bndbg_szsh"


class TestMainLatestPeriod:
    @patch("discover_report.requests.post")
    def test_auto_report_type_prints_latest_period(self, mock_post, capsys):
        mock_post.return_value = _cninfo_mock(PERIODIC_ANNOUNCEMENTS)

        with pytest.raises(SystemExit) as exc_info:
            main(["--stock-code", "000858", "--report-type", "auto"])

        assert exc_info.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "period: 2026H1" in out
        assert "report_type: 中报" in out

    @patch("discover_report.requests.post")
    def test_latest_flag_prints_latest_period(self, mock_post, capsys):
        mock_post.return_value = _cninfo_mock(PERIODIC_ANNOUNCEMENTS)

        with pytest.raises(SystemExit) as exc_info:
            main(["--stock-code", "000858", "--report-type", "年报", "--latest"])

        assert exc_info.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "period: 2025FY" in out

    @patch("discover_report.requests.post")
    def test_no_published_period_exits_no_match(self, mock_post):
        mock_post.return_value = _cninfo_mock({"announcements": []})

        with pytest.raises(SystemExit) as exc_info:
            main(["--stock-code", "000858", "--report-type", "auto"])

        assert exc_info.value.code == EXIT_NO_MATCH

    def test_missing_year_without_latest_exits_bad_arguments(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["--stock-code", "000858", "--report-type", "年报"])
        assert exc_info.value.code == 3
