"""Tests for scripts/discover_report.py"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from discover_report import (
    EXIT_NETWORK_FAILURE,
    EXIT_NO_MATCH,
    EXIT_SUCCESS,
    build_stockpage_url,
    discover_report,
    extract_pdf_candidates,
    main,
    score_candidate,
    select_best_candidate,
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
