
# 覆盖需求：REQ-001（定期报告发现与下载）—— AC-1 四类发现与 secCode 过滤、AC-2 auto/latest 不静默退化、AC-4 --since 拓宽窗口、AC-6 单条失败不中断整批
"""Tests for scripts/discover_report.py"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from discover_report import (
    CNINFO_ALL_REGULAR_CATEGORIES,
    CNINFO_HEADERS,
    CNINFO_PROTOCOL_ENV_VAR,
    CNINFO_QUERY_URL,
    EXIT_BAD_ARGUMENTS,
    EXIT_NETWORK_FAILURE,
    EXIT_NO_MATCH,
    EXIT_SUCCESS,
    build_cninfo_query_url,
    build_stockpage_url,
    discover_latest_period,
    discover_periods,
    discover_report,
    extract_cninfo_period_candidates,
    extract_pdf_candidates,
    format_cninfo_timestamp,
    get_cninfo_category,
    get_required_keywords,
    is_excluded_title,
    main,
    resolve_cninfo_protocol,
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

    @patch("discover_report.requests.get")
    @patch("discover_report.requests.post")
    def test_no_announcements_returns_empty(self, mock_post, mock_get):
        mock_post.return_value = _cninfo_mock({"announcements": []})
        # The lazy company-name fallback is not reached because a company name
        # was supplied; if it were, this would raise instead of hitting network.
        mock_get.side_effect = AssertionError("unexpected stockpage request")
        assert discover_periods("000858", "五粮液") == []
        mock_get.assert_not_called()

    @patch("discover_report.requests.post")
    def test_latest_period_is_first_result(self, mock_post):
        mock_post.return_value = _cninfo_mock(PERIODIC_ANNOUNCEMENTS)
        latest = discover_latest_period("000858", "五粮液")
        assert latest is not None
        assert latest["period"] == "2026H1"
        assert latest["report_type"] == "中报"


class TestDiscoverPeriodsSafety:
    @patch("discover_report.requests.post")
    def test_other_issuer_is_filtered_out(self, mock_post):
        mock_post.return_value = _cninfo_mock(
            {
                "announcements": [
                    {
                        "announcementTitle": "平安银行：2025年年度报告",
                        "announcementTime": 1774800000000,
                        "adjunctUrl": "finalpage/2026-03-29/other.PDF",
                        "secCode": "000001",
                        "secName": "平安银行",
                        "adjunctType": "PDF",
                    },
                    {
                        "announcementTitle": "五粮液：2026年半年度报告",
                        "announcementTime": 1787875200000,
                        "adjunctUrl": "finalpage/2026-08-28/ours.PDF",
                        "secCode": "000858",
                        "secName": "五粮液",
                        "adjunctType": "PDF",
                    },
                ]
            }
        )

        periods = discover_periods("000858", "五粮液")

        assert [candidate["period"] for candidate in periods] == ["2026H1"]
        assert periods[0]["url"].endswith("ours.PDF")

    @patch("discover_report.requests.post")
    def test_candidates_without_seccode_are_kept(self, mock_post):
        mock_post.return_value = _cninfo_mock(PERIODIC_ANNOUNCEMENTS)
        periods = discover_periods("000858", "五粮液")
        assert [candidate["period"] for candidate in periods] == ["2026H1", "2026Q1", "2025FY"]

    @patch("discover_report.requests.post")
    def test_non_pdf_attachment_is_skipped(self, mock_post):
        mock_post.return_value = _cninfo_mock(
            {
                "announcements": [
                    {
                        "announcementTitle": "五粮液：2026年半年度报告",
                        "announcementTime": 1787875200000,
                        "adjunctUrl": "finalpage/2026-08-28/h1.doc",
                        "secCode": "000858",
                        "adjunctType": "DOC",
                    }
                ]
            }
        )
        assert discover_periods("000858", "五粮液") == []

    @patch("discover_report.requests.post")
    def test_pagination_follows_totalpages(self, mock_post):
        page_one = {
            "announcements": [
                {
                    "announcementTitle": f"五粮液：历年公告 {index}",
                    "announcementTime": 1700000000000,
                    "adjunctUrl": f"finalpage/x/{index}.PDF",
                    "secCode": "000858",
                }
                for index in range(100)
            ],
            "totalpages": 2,
        }
        page_two = {
            "announcements": [
                {
                    "announcementTitle": "五粮液：2026年半年度报告",
                    "announcementTime": 1787875200000,
                    "adjunctUrl": "finalpage/2026-08-28/h1.PDF",
                    "secCode": "000858",
                }
            ],
            "totalpages": 2,
        }
        responses = [_cninfo_mock(page_one), _cninfo_mock(page_two)]
        mock_post.side_effect = responses

        periods = discover_periods("000858", "五粮液")

        assert [candidate["period"] for candidate in periods] == ["2026H1"]
        assert mock_post.call_count == 2
        assert [call.kwargs["data"]["pageNum"] for call in mock_post.call_args_list] == [1, 2]

    @patch("discover_report.requests.get")
    @patch("discover_report.requests.post")
    def test_company_name_fallback_when_code_yields_nothing(self, mock_post, mock_get):
        page_response = MagicMock()
        page_response.text = """
        <html>
          <title>五粮液(000858)个股行情</title>
          <strong stockname="五粮液"></strong>
        </html>
        """
        page_response.raise_for_status = MagicMock()
        mock_get.return_value = page_response

        empty = _cninfo_mock({"announcements": []})
        hit = _cninfo_mock(
            {
                "announcements": [
                    {
                        "announcementTitle": "五粮液：2026年半年度报告",
                        "announcementTime": 1787875200000,
                        "adjunctUrl": "finalpage/2026-08-28/h1.PDF",
                        "secCode": "000858",
                    }
                ]
            }
        )
        mock_post.side_effect = [empty, hit]

        periods = discover_periods("000858")

        assert [candidate["period"] for candidate in periods] == ["2026H1"]
        posted_keywords = [call.kwargs["data"]["searchkey"] for call in mock_post.call_args_list]
        assert posted_keywords == ["000858", "五粮液"]
        mock_get.assert_called_once()

    @pytest.mark.parametrize(
        "payload",
        [None, "oops", 5, {"announcements": None}, {"announcements": {"a": 1}}, {"announcements": ["x"]}],
    )
    def test_malformed_payload_does_not_crash(self, payload):
        assert extract_cninfo_period_candidates(payload) == []

    def test_announcement_date_uses_beijing_time(self):
        # 1774540800000 is 2026-03-27 00:00 Beijing (= 2026-03-26 UTC).
        assert format_cninfo_timestamp(1774540800000) == "2026-03-27"

    def test_get_required_keywords_covers_short_title_variants(self):
        assert "三季度报告" in get_required_keywords("三季报")
        assert "半年报" in get_required_keywords("中报")
        assert "中期报告" in get_required_keywords("中报")

    def test_h_share_only_title_excluded_but_combined_title_kept(self):
        assert is_excluded_title("五粮液：2025年年度报告（H股）") is True
        assert is_excluded_title("五粮液：2025年年度报告（A股、H股）") is False


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

    @patch("discover_report.requests.post")
    def test_latest_alone_returns_newest_of_any_type(self, mock_post, capsys):
        mock_post.return_value = _cninfo_mock(PERIODIC_ANNOUNCEMENTS)

        with pytest.raises(SystemExit) as exc_info:
            main(["--stock-code", "000858", "--latest"])

        assert exc_info.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "period: 2026H1" in out
        assert "report_type: 中报" in out

    @patch("discover_report.requests.post")
    def test_latest_with_explicit_type_filters(self, mock_post, capsys):
        mock_post.return_value = _cninfo_mock(PERIODIC_ANNOUNCEMENTS)

        with pytest.raises(SystemExit) as exc_info:
            main(["--stock-code", "000858", "--latest", "--report-type", "三季报"])

        # No 三季报 in the fixture window, so this must not fall back to another type.
        assert exc_info.value.code == EXIT_NO_MATCH

    @patch("discover_report.requests.get")
    @patch("discover_report.requests.post")
    def test_non_json_response_is_a_network_failure(self, mock_post, mock_get):
        # A WAF/HTML body must not be reported as a bad-argument error.
        import requests as req

        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.side_effect = req.exceptions.JSONDecodeError(
            "Expecting value", "<html>", 0
        )
        mock_post.return_value = response
        mock_get.side_effect = req.exceptions.ConnectionError("no stockpage")

        with pytest.raises(SystemExit) as exc_info:
            main(["--stock-code", "000858", "--report-type", "auto"])

        assert exc_info.value.code == EXIT_NETWORK_FAILURE

    def test_missing_year_without_latest_is_bad_arguments_constant(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["--stock-code", "000858", "--report-type", "年报"])
        assert exc_info.value.code == EXIT_BAD_ARGUMENTS


class TestPaginationCapWarning:
    @patch("discover_report.requests.get")
    @patch("discover_report.requests.post")
    def test_page_cap_warns_even_without_totalpages(self, mock_post, mock_get, capsys):
        import requests as req

        full_page = {
            "announcements": [
                {
                    "announcementTitle": f"五粮液：历史公告 {index}",
                    "announcementTime": 1700000000000,
                    "adjunctUrl": f"finalpage/x/{index}.PDF",
                    "secCode": "000858",
                }
                for index in range(100)
            ]
        }
        mock_post.return_value = _cninfo_mock(full_page)
        # No company name: one keyword only, and the name fallback finds nothing.
        mock_get.side_effect = req.exceptions.ConnectionError("no stockpage")

        assert discover_periods("000858") == []

        assert mock_post.call_count == 10
        assert "page cap" in capsys.readouterr().err

    @patch("discover_report.requests.post")
    def test_no_warning_when_last_page_is_partial(self, mock_post, capsys):
        partial_page = {
            "announcements": [
                {
                    "announcementTitle": "五粮液：2026年半年度报告",
                    "announcementTime": 1787875200000,
                    "adjunctUrl": "finalpage/2026-08-28/h1.PDF",
                    "secCode": "000858",
                }
            ]
        }
        mock_post.return_value = _cninfo_mock(partial_page)

        assert [candidate["period"] for candidate in discover_periods("000858", "五粮液")] == ["2026H1"]

        assert mock_post.call_count == 1
        assert "page cap" not in capsys.readouterr().err


class TestPaginationSignals:
    @patch("discover_report.requests.post")
    def test_total_recordnum_drives_pagination(self, mock_post):
        # totalpages is floored by the server (38 records -> totalpages=1), so
        # totalRecordNum must be what decides to fetch page 2.
        page_one = {
            "announcements": [
                {
                    "announcementTitle": f"五粮液：历史公告 {index}",
                    "announcementTime": 1700000000000,
                    "adjunctUrl": f"finalpage/x/{index}.PDF",
                    "secCode": "000858",
                }
                for index in range(30)
            ],
            "totalRecordNum": 45,
            "totalpages": 1,
            "hasMore": True,
        }
        page_two = {
            "announcements": [
                {
                    "announcementTitle": "五粮液：2026年半年度报告",
                    "announcementTime": 1787875200000,
                    "adjunctUrl": "finalpage/2026-08-28/h1.PDF",
                    "secCode": "000858",
                },
                *[
                    {
                        "announcementTitle": f"五粮液：历史公告 b{index}",
                        "announcementTime": 1700000000000,
                        "adjunctUrl": f"finalpage/y/{index}.PDF",
                        "secCode": "000858",
                    }
                    for index in range(14)
                ],
            ],
            "totalRecordNum": 45,
            "totalpages": 1,
            "hasMore": True,
        }
        mock_post.side_effect = [_cninfo_mock(page_one), _cninfo_mock(page_two)]

        periods = discover_periods("000858", "五粮液")

        assert [candidate["period"] for candidate in periods] == ["2026H1"]
        assert [call.kwargs["data"]["pageNum"] for call in mock_post.call_args_list] == [1, 2]

    @patch("discover_report.requests.get")
    @patch("discover_report.requests.post")
    def test_empty_page_with_pending_records_warns(self, mock_post, mock_get, capsys):
        import requests as req

        mock_get.side_effect = req.exceptions.ConnectionError("no stockpage")
        page_one = {
            "announcements": [
                {
                    "announcementTitle": f"五粮液：历史公告 {index}",
                    "announcementTime": 1700000000000,
                    "adjunctUrl": f"finalpage/x/{index}.PDF",
                    "secCode": "000858",
                }
                for index in range(30)
            ],
            "totalRecordNum": 500,
        }
        empty = {"announcements": [], "totalRecordNum": 500}
        mock_post.side_effect = [_cninfo_mock(page_one), _cninfo_mock(empty)]

        # No company name: one keyword, so the mock is not consumed twice.
        discover_periods("000858")

        assert "some announcements may not have been scanned" in capsys.readouterr().err

    @patch("discover_report.requests.post")
    def test_requested_page_size_matches_server_cap(self, mock_post):
        mock_post.return_value = _cninfo_mock(PERIODIC_ANNOUNCEMENTS)
        discover_periods("000858", "五粮液")
        assert mock_post.call_args.kwargs["data"]["pageSize"] == 30


class TestKeywordFallbackUntilScorable:
    @patch("discover_report.requests.post")
    @patch("discover_report.requests.get")
    def test_next_keyword_is_tried_when_first_only_has_filtered_titles(self, mock_get, mock_post):
        page_response = MagicMock()
        page_response.text = """
        <html>
          <title>五粮液(000858)个股行情</title>
          <strong stockname="五粮液"></strong>
        </html>
        """
        page_response.raise_for_status = MagicMock()
        mock_get.return_value = page_response

        english_only = _cninfo_mock(
            {
                "announcements": [
                    {
                        "announcementTitle": "五粮液：2024年半年度报告（英文版）",
                        "announcementTime": 1724760000000,
                        "adjunctUrl": "finalpage/2024-08-28/en.PDF",
                    }
                ]
            }
        )
        proper = _cninfo_mock(
            {
                "announcements": [
                    {
                        "announcementTitle": "五粮液：2024年半年度报告",
                        "announcementTime": 1724760000000,
                        "adjunctUrl": "finalpage/2024-08-28/h1.PDF",
                    }
                ]
            }
        )
        mock_post.side_effect = [english_only, proper]

        _, _, best = discover_report("000858", "2024", "中报")

        assert best is not None
        assert best["url"].endswith("h1.PDF")
        posted_keywords = [call.kwargs["data"]["searchkey"] for call in mock_post.call_args_list]
        assert posted_keywords == ["000858", "五粮液"]


class TestScoreCandidateYearAlignment:
    def test_other_year_in_title_does_not_satisfy_requested_year(self):
        assert score_candidate(
            {"title": "五粮液：2023年年度报告（2024年4月30日更新）"}, "2024", "年报"
        ) is None
        assert score_candidate(
            {"title": "五粮液：2024年第一季度报告及2023年年度报告"}, "2024", "年报"
        ) is None

    def test_matching_year_still_scores(self):
        assert score_candidate({"title": "五粮液：2024年年度报告"}, "2024", "年报") is not None


# --- CNINFO query protocol configuration / https 403 -> http fallback ---
# 子需求 AC-1.1：查询协议可配置、https 403 回退 http 且请求头保持、回退留痕。
# （本条测试归属仍随本文件登记为 REQ-001；如需在 docs/TEST_SCOPE.md 里
#  追加子需求编号，请运行 `make scope-write`，本任务范围内不改 docs。）


def _forbidden_response():
    response = MagicMock()
    response.status_code = 403
    response.raise_for_status = MagicMock(side_effect=requests.HTTPError("403 Client Error"))
    return response


def _ok_response(payload):
    response = _cninfo_mock(payload)
    response.status_code = 200
    return response


CNINFO_QUERY_URLS_BY_PROTOCOL = {
    "https": "https://www.cninfo.com.cn/new/hisAnnouncement/query",
    "http": "http://www.cninfo.com.cn/new/hisAnnouncement/query",
}

ANNUAL_ANNOUNCEMENTS = {
    "announcements": [
        {
            "announcementTitle": "五粮液：2025年年度报告",
            "announcementTime": 1774540800000,  # 2026-03-27
            "adjunctUrl": "finalpage/2026-03-27/fy2025.PDF",
        }
    ]
}


class TestCninfoProtocolFallback:
    """AC-1.1: configurable protocol, https 403 -> http fallback."""

    @pytest.fixture(autouse=True)
    def _clear_protocol_env(self, monkeypatch):
        monkeypatch.delenv(CNINFO_PROTOCOL_ENV_VAR, raising=False)

    @patch("discover_report.requests.post")
    def test_https_403_falls_back_to_http_and_keeps_headers(self, mock_post, capsys):
        mock_post.side_effect = [_forbidden_response(), _ok_response(PERIODIC_ANNOUNCEMENTS)]

        periods = discover_periods("000858", "五粮液")

        assert [candidate["period"] for candidate in periods] == ["2026H1", "2026Q1", "2025FY"]
        # Regression guard: no fallback -> the 403 raises; fallback without the
        # headers -> the headers assertion below fails.
        assert [call.args[0] for call in mock_post.call_args_list] == [
            CNINFO_QUERY_URLS_BY_PROTOCOL["https"],
            CNINFO_QUERY_URLS_BY_PROTOCOL["http"],
        ]
        assert [call.kwargs["headers"] for call in mock_post.call_args_list] == [
            CNINFO_HEADERS,
            CNINFO_HEADERS,
        ]
        assert mock_post.call_args_list[-1].kwargs["headers"]["Referer"].startswith(
            "https://www.cninfo.com.cn/"
        )
        # The switch is reported, not silent.
        assert "403" in capsys.readouterr().err

    @patch("discover_report.requests.get")
    @patch("discover_report.requests.post")
    def test_annual_query_path_also_falls_back(self, mock_post, mock_get):
        page_response = MagicMock()
        page_response.text = """
        <html>
          <title>五粮液(000858)个股行情</title>
          <strong stockname="五粮液"></strong>
        </html>
        """
        page_response.raise_for_status = MagicMock()
        mock_get.return_value = page_response
        mock_post.side_effect = [_forbidden_response(), _ok_response(ANNUAL_ANNOUNCEMENTS)]

        _, _, best = discover_report("000858", "2025", "年报")

        assert best is not None
        assert best["url"] == "https://static.cninfo.com.cn/finalpage/2026-03-27/fy2025.PDF"
        assert [call.args[0] for call in mock_post.call_args_list] == [
            CNINFO_QUERY_URLS_BY_PROTOCOL["https"],
            CNINFO_QUERY_URLS_BY_PROTOCOL["http"],
        ]

    @patch("discover_report.requests.post")
    def test_explicit_https_pins_and_does_not_fall_back(self, mock_post):
        mock_post.return_value = _forbidden_response()

        with pytest.raises(requests.HTTPError):
            discover_periods("000858", "五粮液", protocol="https")

        assert mock_post.call_count == 1
        assert mock_post.call_args.args[0] == CNINFO_QUERY_URLS_BY_PROTOCOL["https"]

    @patch("discover_report.requests.post")
    def test_explicit_http_never_tries_https(self, mock_post):
        mock_post.return_value = _ok_response(PERIODIC_ANNOUNCEMENTS)

        periods = discover_periods("000858", "五粮液", protocol="http")

        assert [candidate["period"] for candidate in periods] == ["2026H1", "2026Q1", "2025FY"]
        assert mock_post.call_count == 1
        assert mock_post.call_args.args[0] == CNINFO_QUERY_URLS_BY_PROTOCOL["http"]

    @patch("discover_report.requests.post")
    def test_env_var_selects_the_protocol(self, mock_post, monkeypatch):
        monkeypatch.setenv(CNINFO_PROTOCOL_ENV_VAR, "http")
        mock_post.return_value = _ok_response(PERIODIC_ANNOUNCEMENTS)

        assert resolve_cninfo_protocol() == "http"
        discover_periods("000858", "五粮液")

        assert mock_post.call_count == 1
        assert mock_post.call_args.args[0] == CNINFO_QUERY_URLS_BY_PROTOCOL["http"]

    def test_default_protocol_is_still_https(self):
        assert CNINFO_QUERY_URL == CNINFO_QUERY_URLS_BY_PROTOCOL["https"]
        assert resolve_cninfo_protocol() == "https"
        assert build_cninfo_query_url("http") == CNINFO_QUERY_URLS_BY_PROTOCOL["http"]

    def test_explicit_protocol_beats_the_env_var(self, monkeypatch):
        monkeypatch.setenv(CNINFO_PROTOCOL_ENV_VAR, "http")
        assert resolve_cninfo_protocol("https") == "https"

    def test_invalid_explicit_protocol_is_rejected(self):
        with pytest.raises(ValueError):
            resolve_cninfo_protocol("ftp")

    def test_invalid_env_var_warns_and_keeps_https(self, monkeypatch, capsys):
        monkeypatch.setenv(CNINFO_PROTOCOL_ENV_VAR, "gopher")

        assert resolve_cninfo_protocol() == "https"
        assert "ignoring invalid" in capsys.readouterr().err

    @patch("discover_report.requests.get")
    @patch("discover_report.requests.post")
    def test_cli_flag_pins_the_protocol(self, mock_post, mock_get, monkeypatch, capsys):
        monkeypatch.setenv(CNINFO_PROTOCOL_ENV_VAR, "https")
        mock_post.return_value = _ok_response(PERIODIC_ANNOUNCEMENTS)
        mock_get.side_effect = requests.exceptions.ConnectionError("no stockpage")

        with pytest.raises(SystemExit) as exc_info:
            main(["--stock-code", "000858", "--latest", "--cninfo-protocol", "http"])

        assert exc_info.value.code == EXIT_SUCCESS
        assert mock_post.call_count == 1
        assert mock_post.call_args.args[0] == CNINFO_QUERY_URLS_BY_PROTOCOL["http"]
        assert "period: 2026H1" in capsys.readouterr().out
