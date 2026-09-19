"""Tests for scripts/download_report.py"""

import os
import sys
import tempfile
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from download_report import (
    EXIT_BAD_ARGUMENTS,
    EXIT_NETWORK_FAILURE,
    EXIT_PDF_VALIDATION_FAILURE,
    EXIT_SUCCESS,
    build_filename,
    determine_years_to_download,
    download_annual_report,
    get_headers,
    main,
    print_result,
    resolve_auto_targets,
    validate_url,
    write_sources_index,
)


# --- TestValidateUrl ---

class TestValidateUrl:
    def test_valid_xueqiu_url(self):
        ok, msg = validate_url("https://stockn.xueqiu.com/some/path/report.pdf")
        assert ok is True
        assert msg == ""

    def test_valid_10jqka_url(self):
        ok, msg = validate_url("https://notice.10jqka.com.cn/api/report.pdf")
        assert ok is True
        assert msg == ""

    def test_valid_10jqka_subdomain(self):
        ok, msg = validate_url("https://data.10jqka.com.cn/path/file.pdf")
        assert ok is True
        assert msg == ""

    def test_valid_cninfo_url(self):
        ok, msg = validate_url("https://static.cninfo.com.cn/finalpage/2026-03-27/1225036280.PDF")
        assert ok is True
        assert msg == ""

    def test_invalid_domain(self):
        ok, msg = validate_url("https://example.com/report.pdf")
        assert ok is False
        assert "Invalid URL" in msg

    def test_non_pdf_url(self):
        ok, msg = validate_url("https://stockn.xueqiu.com/report.html")
        assert ok is False
        assert "Invalid URL" in msg

    def test_http_also_valid(self):
        ok, msg = validate_url("http://stockn.xueqiu.com/report.pdf")
        assert ok is True

    def test_empty_url(self):
        ok, msg = validate_url("")
        assert ok is False

    def test_case_insensitive(self):
        ok, msg = validate_url("HTTPS://STOCKN.XUEQIU.COM/REPORT.PDF")
        assert ok is True


# --- TestBuildFilename ---

class TestBuildFilename:
    def test_chinese_annual(self):
        assert build_filename("SH600887", "年报", "2024") == "600887_2024_年报.pdf"

    def test_chinese_interim(self):
        assert build_filename("SZ300750", "中报", "2024") == "300750_2024_中报.pdf"

    def test_english_annual(self):
        assert build_filename("SH600887", "annual", "2024") == "600887_2024_年报.pdf"

    def test_english_interim(self):
        assert build_filename("SH600887", "interim", "2024") == "600887_2024_中报.pdf"

    def test_english_q1(self):
        assert build_filename("SH600887", "q1", "2024") == "600887_2024_一季报.pdf"

    def test_english_q3(self):
        assert build_filename("SH600887", "Q3", "2024") == "600887_2024_三季报.pdf"

    def test_hk_stock_no_prefix(self):
        assert build_filename("00700", "年报", "2024") == "00700_2024_年报.pdf"

    def test_strips_sh_prefix(self):
        result = build_filename("SH600887", "年报", "2024")
        assert result.startswith("600887_")

    def test_strips_sz_prefix(self):
        result = build_filename("SZ300750", "年报", "2024")
        assert result.startswith("300750_")

    def test_lowercase_prefix_stripped(self):
        result = build_filename("sh600887", "年报", "2024")
        assert result.startswith("600887_")


class TestDetermineYearsToDownload:
    def test_defaults_to_recent_three_annual_years(self):
        years = determine_years_to_download("年报")
        assert len(years) == 3

    def test_explicit_year_defaults_to_single_year(self):
        assert determine_years_to_download("年报", explicit_year="2025") == ["2025"]

    def test_recent_years_respects_anchor_year(self):
        assert determine_years_to_download("年报", explicit_year="2025", recent_years=3) == ["2025", "2024", "2023"]


# --- TestGetHeaders ---

class TestGetHeaders:
    def test_xueqiu_referer(self):
        headers = get_headers("https://stockn.xueqiu.com/report.pdf")
        assert headers["Referer"] == "https://xueqiu.com/"

    def test_10jqka_referer(self):
        headers = get_headers("https://notice.10jqka.com.cn/report.pdf")
        assert headers["Referer"] == "https://10jqka.com.cn/"

    def test_cninfo_referer(self):
        headers = get_headers("https://static.cninfo.com.cn/finalpage/2026-03-27/1225036280.PDF")
        assert headers["Referer"] == "https://www.cninfo.com.cn/"

    def test_base_headers_present(self):
        headers = get_headers("https://stockn.xueqiu.com/report.pdf")
        assert "User-Agent" in headers
        assert "Accept" in headers
        assert "Accept-Language" in headers


# --- TestDownloadAnnualReport ---

class TestDownloadAnnualReport:
    def _make_pdf_response(self, content=None, content_type="application/pdf"):
        """Create a mock response that behaves like a streaming PDF download."""
        if content is None:
            content = b"%PDF-1.4 fake pdf content here" + b"\x00" * 1024
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": content_type}
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content = MagicMock(return_value=[content])
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("download_report.requests.get")
    def test_successful_download(self, mock_get):
        mock_get.return_value = self._make_pdf_response()
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "test.pdf")
            success, msg, size = download_annual_report(
                "https://stockn.xueqiu.com/test.pdf", save_path, max_retries=1
            )
            assert success is True
            assert "successful" in msg.lower()
            assert size > 0
            assert os.path.exists(save_path)

    @patch("download_report.requests.get")
    def test_pdf_magic_bytes_failure(self, mock_get):
        mock_get.return_value = self._make_pdf_response(content=b"<html>not a pdf</html>")
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "test.pdf")
            success, msg, size = download_annual_report(
                "https://stockn.xueqiu.com/test.pdf", save_path, max_retries=1
            )
            assert success is False
            assert "magic bytes" in msg.lower()
            assert size == 0

    @patch("download_report.requests.get")
    @patch("download_report.time.sleep")
    def test_retry_on_network_error(self, mock_sleep, mock_get):
        import requests as req
        mock_get.side_effect = [
            req.exceptions.ConnectionError("connection refused"),
            self._make_pdf_response(),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "test.pdf")
            success, msg, size = download_annual_report(
                "https://stockn.xueqiu.com/test.pdf", save_path, max_retries=2
            )
            assert success is True
            assert mock_get.call_count == 2
            mock_sleep.assert_called_once_with(3)  # BACKOFF_BASE * 1

    @patch("download_report.requests.get")
    @patch("download_report.time.sleep")
    def test_all_retries_exhausted(self, mock_sleep, mock_get):
        import requests as req
        mock_get.side_effect = req.exceptions.ConnectionError("connection refused")
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "test.pdf")
            success, msg, size = download_annual_report(
                "https://stockn.xueqiu.com/test.pdf", save_path, max_retries=2
            )
            assert success is False
            assert "failed after 2 attempts" in msg.lower()
            assert mock_get.call_count == 2

    @patch("download_report.requests.get")
    def test_tmp_file_cleaned_on_magic_failure(self, mock_get):
        mock_get.return_value = self._make_pdf_response(content=b"NOT_PDF_CONTENT")
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "test.pdf")
            download_annual_report(
                "https://stockn.xueqiu.com/test.pdf", save_path, max_retries=1
            )
            assert not os.path.exists(save_path + ".tmp")
            assert not os.path.exists(save_path)

    @patch("download_report.requests.get")
    def test_content_type_warning(self, mock_get, capsys):
        mock_get.return_value = self._make_pdf_response(content_type="text/html")
        with tempfile.TemporaryDirectory() as tmpdir:
            save_path = os.path.join(tmpdir, "test.pdf")
            download_annual_report(
                "https://stockn.xueqiu.com/test.pdf", save_path, max_retries=1
            )
            captured = capsys.readouterr()
            assert "Content-Type" in captured.err


# --- TestPrintResult ---

class TestPrintResult:
    def test_success_format(self, capsys):
        print_result(
            success=True, filepath="/tmp/test.pdf", filesize=12345,
            url="https://example.com/test.pdf", stock_code="SH600887",
            report_type="年报", year="2024", message="OK"
        )
        out = capsys.readouterr().out
        assert "---RESULT---" in out
        assert "status: SUCCESS" in out
        assert "filepath: /tmp/test.pdf" in out
        assert "filesize: 12345" in out
        assert "---END---" in out

    def test_failure_format(self, capsys):
        print_result(success=False, message="Download failed")
        out = capsys.readouterr().out
        assert "status: FAILED" in out
        assert "message: Download failed" in out

    def test_all_fields_present(self, capsys):
        print_result(
            success=True, filepath="p", filesize=1, url="u",
            stock_code="s", report_type="r", year="y", message="m"
        )
        out = capsys.readouterr().out
        for field in ["status", "filepath", "filesize", "url", "stock_code",
                       "report_type", "year", "message", "filepaths",
                       "requested_years", "completed_years", "failed_years"]:
            assert f"{field}:" in out


# --- TestMain ---

class TestMain:
    @patch("download_report.download_annual_report")
    @patch("download_report.validate_url", return_value=(True, ""))
    def test_success_flow(self, mock_validate, mock_download):
        mock_download.return_value = (True, "OK", 50000)
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--url", "https://stockn.xueqiu.com/test.pdf",
                    "--stock-code", "SH600887",
                    "--report-type", "年报",
                    "--year", "2024",
                    "--save-dir", tmpdir,
                ])
            assert exc_info.value.code == EXIT_SUCCESS

    @patch("download_report.discover_report")
    @patch("download_report.download_annual_report")
    @patch("download_report.validate_url", return_value=(True, ""))
    def test_success_flow_without_url(self, mock_validate, mock_download, mock_discover):
        mock_discover.return_value = (
            "https://stockpage.10jqka.com.cn/000858/",
            [],
            {"url": "https://notice.10jqka.com.cn/api/pdf/report.pdf"},
        )
        mock_download.return_value = (True, "OK", 50000)

        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--stock-code", "SZ000858",
                    "--report-type", "年报",
                    "--year", "2025",
                    "--save-dir", tmpdir,
                ])

            assert exc_info.value.code == EXIT_SUCCESS

    @patch("download_report.discover_report")
    @patch("download_report.download_annual_report")
    @patch("download_report.validate_url", return_value=(True, ""))
    def test_reports_summary_fallback_when_only_annual_summary_exists(self, mock_validate, mock_download, mock_discover, capsys):
        mock_discover.return_value = (
            "https://stockpage.10jqka.com.cn/600941/",
            [],
            {
                "url": "https://notice.10jqka.com.cn/api/pdf/report.pdf",
                "title": "中国移动：2025年年度报告摘要",
                "match_quality": "summary_fallback",
            },
        )
        mock_download.return_value = (True, "OK", 50000)

        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--stock-code", "SH600941",
                    "--report-type", "年报",
                    "--year", "2025",
                    "--save-dir", tmpdir,
                ])

            assert exc_info.value.code == EXIT_SUCCESS
            out = capsys.readouterr().out
            assert "summary fallback used for years: 2025" in out

    @patch("download_report.discover_report")
    @patch("download_report.download_annual_report")
    @patch("download_report.validate_url", return_value=(True, ""))
    def test_defaults_to_three_year_download_for_annual(self, mock_validate, mock_download, mock_discover):
        mock_discover.side_effect = [
            ("https://stockpage.10jqka.com.cn/000858/", [], {"url": "https://notice.10jqka.com.cn/api/pdf/2025.pdf"}),
            ("https://stockpage.10jqka.com.cn/000858/", [], {"url": "https://notice.10jqka.com.cn/api/pdf/2024.pdf"}),
            ("https://stockpage.10jqka.com.cn/000858/", [], {"url": "https://notice.10jqka.com.cn/api/pdf/2023.pdf"}),
        ]
        mock_download.return_value = (True, "OK", 50000)

        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--stock-code", "SZ000858",
                    "--report-type", "年报",
                    "--save-dir", tmpdir,
                ])

            assert exc_info.value.code == EXIT_SUCCESS
            assert mock_discover.call_count == 3

    @patch("download_report.discover_report")
    @patch("download_report.download_annual_report")
    @patch("download_report.validate_url", return_value=(True, ""))
    def test_partial_failure_reports_upstream(self, mock_validate, mock_download, mock_discover, capsys):
        mock_discover.side_effect = [
            ("https://stockpage.10jqka.com.cn/000858/", [], {"url": "https://notice.10jqka.com.cn/api/pdf/2025.pdf"}),
            ("https://stockpage.10jqka.com.cn/000858/", [], None),
            ("https://stockpage.10jqka.com.cn/000858/", [], {"url": "https://notice.10jqka.com.cn/api/pdf/2023.pdf"}),
        ]
        mock_download.return_value = (True, "OK", 50000)

        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--stock-code", "SZ000858",
                    "--report-type", "年报",
                    "--year", "2025",
                    "--recent-years", "3",
                    "--save-dir", tmpdir,
                ])

            assert exc_info.value.code == EXIT_NETWORK_FAILURE
            out = capsys.readouterr().out
            assert "status: PARTIAL" in out
            assert "failed_years: 2024" in out

    @patch("download_report.discover_report")
    def test_discovery_failure_exit_code(self, mock_discover):
        mock_discover.return_value = (
            "https://stockpage.10jqka.com.cn/000858/",
            [],
            None,
        )

        with pytest.raises(SystemExit) as exc_info:
            main([
                "--stock-code", "SZ000858",
                "--report-type", "年报",
                "--year", "2025",
            ])

        assert exc_info.value.code == EXIT_NETWORK_FAILURE

    def test_bad_url_exit_code(self):
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--url", "https://example.com/bad.pdf",
                "--stock-code", "SH600887",
                "--report-type", "年报",
                "--year", "2024",
            ])
        assert exc_info.value.code == EXIT_BAD_ARGUMENTS

    @patch("download_report.download_annual_report")
    @patch("download_report.validate_url", return_value=(True, ""))
    def test_network_failure_exit_code(self, mock_validate, mock_download):
        mock_download.return_value = (False, "Network error after 3 attempts", 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--url", "https://stockn.xueqiu.com/test.pdf",
                    "--stock-code", "SH600887",
                    "--report-type", "年报",
                    "--year", "2024",
                    "--save-dir", tmpdir,
                ])
            assert exc_info.value.code == EXIT_NETWORK_FAILURE

    @patch("download_report.download_annual_report")
    @patch("download_report.validate_url", return_value=(True, ""))
    def test_validation_failure_exit_code(self, mock_validate, mock_download):
        mock_download.return_value = (False, "PDF validation failed: bad magic", 0)
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "--url", "https://stockn.xueqiu.com/test.pdf",
                    "--stock-code", "SH600887",
                    "--report-type", "年报",
                    "--year", "2024",
                    "--save-dir", tmpdir,
                ])
            assert exc_info.value.code == EXIT_PDF_VALIDATION_FAILURE


# --- Periodic (quarterly / interim / annual) download ---

PERIODIC_TARGETS = [
    {
        "period": "2026H1",
        "report_type": "中报",
        "title": "五粮液：2026年半年度报告（更新后）",
        "date": "2026-08-30",
        "url": "https://static.cninfo.com.cn/finalpage/2026-08-30/h1.PDF",
        "source": "cninfo",
    },
    {
        "period": "2026Q1",
        "report_type": "一季报",
        "title": "五粮液：2026年第一季度报告",
        "date": "2026-04-25",
        "url": "https://static.cninfo.com.cn/finalpage/2026-04-25/q1.PDF",
        "source": "cninfo",
    },
]


def _write_fake_pdf(url, save_path, max_retries=3):
    with open(save_path, "wb") as handle:
        handle.write(b"%PDF-1.4 test content")
    return True, "Download successful", 22


class TestResolveAutoTargets:
    @patch("download_report.discover_periods")
    def test_without_since_returns_only_latest(self, mock_discover):
        mock_discover.return_value = PERIODIC_TARGETS
        targets, latest = resolve_auto_targets("000858")
        assert [target["period"] for target in targets] == ["2026H1"]
        assert latest["period"] == "2026H1"

    @patch("download_report.discover_periods")
    def test_since_includes_every_newer_period(self, mock_discover):
        mock_discover.return_value = PERIODIC_TARGETS
        targets, latest = resolve_auto_targets("000858", since="2026Q1")
        assert [target["period"] for target in targets] == ["2026H1", "2026Q1"]

    @patch("download_report.discover_periods")
    def test_no_published_period(self, mock_discover):
        mock_discover.return_value = []
        assert resolve_auto_targets("000858") == ([], None)


class TestWriteSourcesIndex:
    def test_merges_existing_entries(self, tmp_path):
        index_path = tmp_path / "sources_index.json"
        write_sources_index(
            index_path,
            stock_code="000858",
            latest_period="2025FY",
            entries=[{"period": "2025FY", "sha256": "a"}],
        )
        write_sources_index(
            index_path,
            stock_code="000858",
            latest_period="2026H1",
            entries=[{"period": "2026H1", "sha256": "b"}],
        )

        import json

        payload = json.loads(index_path.read_text(encoding="utf-8"))
        assert payload["schema"] == "investment.report_sources"
        assert payload["latest_period"] == "2026H1"
        assert set(payload["periods"]) == {"2025FY", "2026H1"}
        assert payload["periods"]["2025FY"]["sha256"] == "a"


class TestPrintResultPeriodFields:
    def test_period_fields_present(self, capsys):
        print_result(
            success=True,
            latest_period="2026H1",
            periods=["2026H1", "2026Q1"],
            completed_periods=["2026H1"],
            failed_periods=["2026Q1"],
        )
        out = capsys.readouterr().out
        assert "status: PARTIAL" in out
        assert "latest_period: 2026H1" in out
        assert "periods_requested: 2026H1,2026Q1" in out
        assert "periods_completed: 2026H1" in out
        assert "periods_failed: 2026Q1" in out


class TestAutoDownloadMain:
    @patch("download_report.discover_periods")
    @patch("download_report.download_annual_report", side_effect=_write_fake_pdf)
    def test_latest_downloads_only_newest_period(self, mock_download, mock_discover, tmp_path, capsys):
        mock_discover.return_value = PERIODIC_TARGETS

        with pytest.raises(SystemExit) as exc_info:
            main([
                "--stock-code", "000858",
                "--report-type", "auto",
                "--save-dir", str(tmp_path),
            ])

        assert exc_info.value.code == EXIT_SUCCESS
        assert mock_download.call_count == 1
        assert (tmp_path / "000858_2026_中报.pdf").exists()
        out = capsys.readouterr().out
        assert "latest_period: 2026H1" in out
        assert "periods_completed: 2026H1" in out

    @patch("download_report.discover_periods")
    @patch("download_report.download_annual_report", side_effect=_write_fake_pdf)
    def test_since_downloads_every_missing_period_and_writes_index(
        self, mock_download, mock_discover, tmp_path, capsys
    ):
        mock_discover.return_value = PERIODIC_TARGETS

        with pytest.raises(SystemExit) as exc_info:
            main([
                "--stock-code", "000858",
                "--report-type", "auto",
                "--since", "2026Q1",
                "--save-dir", str(tmp_path),
            ])

        assert exc_info.value.code == EXIT_SUCCESS
        assert (tmp_path / "000858_2026_中报.pdf").exists()
        assert (tmp_path / "000858_2026_一季报.pdf").exists()

        import json

        payload = json.loads((tmp_path / "sources_index.json").read_text(encoding="utf-8"))
        assert payload["latest_period"] == "2026H1"
        assert set(payload["periods"]) == {"2026H1", "2026Q1"}
        assert payload["periods"]["2026H1"]["filename"] == "000858_2026_中报.pdf"
        assert len(payload["periods"]["2026H1"]["sha256"]) == 64

        out = capsys.readouterr().out
        assert "periods_requested: 2026H1,2026Q1" in out

    @patch("download_report.discover_periods")
    def test_no_published_period_exits_network_failure(self, mock_discover, tmp_path):
        mock_discover.return_value = []

        with pytest.raises(SystemExit) as exc_info:
            main([
                "--stock-code", "000858",
                "--report-type", "auto",
                "--save-dir", str(tmp_path),
            ])

        assert exc_info.value.code == EXIT_NETWORK_FAILURE

    def test_invalid_since_period_exits_bad_arguments(self, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--stock-code", "000858",
                "--report-type", "auto",
                "--since", "2026Q2",
                "--save-dir", str(tmp_path),
            ])

        assert exc_info.value.code == EXIT_BAD_ARGUMENTS

    @patch("download_report.discover_periods")
    @patch("download_report.download_annual_report", side_effect=_write_fake_pdf)
    def test_interim_concrete_type_with_latest(self, mock_download, mock_discover, tmp_path):
        mock_discover.return_value = PERIODIC_TARGETS

        with pytest.raises(SystemExit) as exc_info:
            main([
                "--stock-code", "000858",
                "--report-type", "中报",
                "--latest",
                "--save-dir", str(tmp_path),
            ])

        assert exc_info.value.code == EXIT_SUCCESS
        assert mock_discover.call_args.kwargs["report_type"] == "中报"

    @patch("download_report.discover_periods")
    @patch("download_report.download_annual_report")
    def test_partial_download_reports_partial(self, mock_download, mock_discover, tmp_path, capsys):
        mock_discover.return_value = PERIODIC_TARGETS

        def _download(url, save_path, max_retries=3):
            if url.endswith("q1.PDF"):
                return False, "network error", 0
            return _write_fake_pdf(url, save_path, max_retries)

        mock_download.side_effect = _download

        with pytest.raises(SystemExit) as exc_info:
            main([
                "--stock-code", "000858",
                "--report-type", "auto",
                "--since", "2026Q1",
                "--save-dir", str(tmp_path),
            ])

        assert exc_info.value.code == EXIT_NETWORK_FAILURE
        out = capsys.readouterr().out
        assert "status: PARTIAL" in out
        assert "periods_failed: 2026Q1" in out

    @patch("download_report.discover_periods")
    def test_discovery_network_failure_exits_network_failure(self, mock_discover, tmp_path):
        import requests as req

        mock_discover.side_effect = req.exceptions.ConnectionError("cninfo down")

        with pytest.raises(SystemExit) as exc_info:
            main([
                "--stock-code", "000858",
                "--report-type", "auto",
                "--save-dir", str(tmp_path),
            ])

        assert exc_info.value.code == EXIT_NETWORK_FAILURE
