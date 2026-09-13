#!/usr/bin/env python3
"""
财报PDF下载工具 (Financial Report PDF Downloader)

从巨潮资讯或同花顺下载 A 股财报 PDF 文件。
当前重点优化 A 股近 3 年年报下载，支持年报、中报、一季报、三季报。

Usage:
    python3 scripts/download_report.py \
        --url "https://stockn.xueqiu.com/.../report.pdf" \
        --stock-code SH600887 \
        --report-type 年报 \
        --year 2024 \
        --save-dir .
"""

import argparse
from datetime import date
import os
import re
import sys
import time

import requests

from discover_report import discover_report

# Exit codes
EXIT_SUCCESS = 0
EXIT_NETWORK_FAILURE = 1
EXIT_PDF_VALIDATION_FAILURE = 2
EXIT_BAD_ARGUMENTS = 3

# Constants
PDF_MAGIC_BYTES = b"%PDF-"
MIN_FILE_SIZE_WARNING = 100 * 1024  # 100KB
DOWNLOAD_TIMEOUT = 120
DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE = 3  # seconds

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

URL_PATTERN = re.compile(
    r"^https?://(stockn\.xueqiu\.com|[\w.-]*10jqka\.com\.cn|static\.cninfo\.com\.cn)/.+\.pdf$",
    re.IGNORECASE,
)


def get_headers(url):
    """Return headers with Referer matching the URL domain."""
    headers = dict(BASE_HEADERS)
    if "10jqka.com.cn" in url:
        headers["Referer"] = "https://10jqka.com.cn/"
    elif "static.cninfo.com.cn" in url:
        headers["Referer"] = "https://www.cninfo.com.cn/"
    else:
        headers["Referer"] = "https://xueqiu.com/"
    return headers


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Download A-share financial report PDF from CNINFO or 10jqka"
    )
    parser.add_argument(
        "--url", help="Direct PDF URL from static.cninfo.com.cn or 10jqka.com.cn"
    )
    parser.add_argument(
        "--stock-code", required=True, help="A-share stock code (e.g. SH600887, SZ300750, 600941)"
    )
    parser.add_argument(
        "--report-type",
        required=True,
        help="Report type (年报/中报/一季报/三季报/annual/interim)",
    )
    parser.add_argument(
        "--year", help="Report year (e.g. 2024). If omitted with 年报, defaults to latest fiscal year."
    )
    parser.add_argument(
        "--recent-years",
        type=int,
        default=None,
        help="Download recent N fiscal years. Defaults to 3 for 年报 when --year is omitted; otherwise 1.",
    )
    parser.add_argument(
        "--save-dir", default="output", help="Directory to save the PDF (default: output)"
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Max download retries (default: {DEFAULT_MAX_RETRIES})",
    )
    return parser.parse_args(argv)


def normalize_report_type(report_type):
    type_map = {
        "annual": "年报",
        "interim": "中报",
        "q1": "一季报",
        "q3": "三季报",
    }
    return type_map.get(report_type.lower(), report_type)


def determine_anchor_year(report_type, explicit_year=None):
    if explicit_year:
        return int(explicit_year)

    normalized = normalize_report_type(report_type)
    today = date.today()
    if normalized == "年报":
        return today.year - 1
    return today.year


def determine_years_to_download(report_type, explicit_year=None, recent_years=None):
    normalized = normalize_report_type(report_type)
    anchor_year = determine_anchor_year(report_type, explicit_year)

    if recent_years is not None:
        count = max(recent_years, 1)
    elif explicit_year:
        count = 1
    elif normalized == "年报":
        count = 3
    else:
        count = 1

    return [str(anchor_year - offset) for offset in range(count)]


def validate_url(url):
    """Validate that the URL points to a supported source and ends with .pdf."""
    if not URL_PATTERN.match(url):
        return False, (
            f"Invalid URL: {url}\n"
            "URL must be a .pdf link from stockn.xueqiu.com, 10jqka.com.cn, or static.cninfo.com.cn"
        )
    return True, ""


def build_filename(stock_code, report_type, year):
    """Build output filename: {code}_{year}_{report_type}.pdf

    Strips SH/SZ prefix from stock_code to match coordinator.md convention
    (e.g. 600887_2024_年报.pdf).
    """
    normalized = normalize_report_type(report_type)
    # Strip exchange prefix for filename
    code = re.sub(r"^(SH|SZ)", "", stock_code, flags=re.IGNORECASE)
    return f"{code}_{year}_{normalized}.pdf"


def download_annual_report(url, save_path, max_retries=DEFAULT_MAX_RETRIES):
    """
    Download PDF with retry and validation.

    Returns:
        tuple: (success: bool, message: str, filesize: int)
    """
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            print(
                f"Downloading (attempt {attempt}/{max_retries}): {url}",
                file=sys.stderr,
            )

            response = requests.get(
                url,
                headers=get_headers(url),
                timeout=DOWNLOAD_TIMEOUT,
                stream=True,
            )
            response.raise_for_status()

            # Check Content-Type
            content_type = response.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower() and "octet-stream" not in content_type.lower():
                print(
                    f"Warning: Content-Type is '{content_type}', expected PDF",
                    file=sys.stderr,
                )

            # Download to temporary path first, then rename
            tmp_path = save_path + ".tmp"
            total_size = 0
            first_chunk = True

            with open(tmp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        # Validate PDF magic bytes on first chunk
                        if first_chunk:
                            if not chunk[:5].startswith(PDF_MAGIC_BYTES):
                                os.remove(tmp_path)
                                return (
                                    False,
                                    "PDF validation failed: file does not start with %PDF- magic bytes",
                                    0,
                                )
                            first_chunk = False
                        f.write(chunk)
                        total_size += len(chunk)

            # Rename tmp to final
            if os.path.exists(save_path):
                os.remove(save_path)
            os.rename(tmp_path, save_path)

            # Size warning
            if total_size < MIN_FILE_SIZE_WARNING:
                print(
                    f"Warning: file size ({total_size} bytes) is smaller than expected (<100KB)",
                    file=sys.stderr,
                )

            return True, "Download successful", total_size

        except requests.exceptions.RequestException as e:
            last_error = str(e)
            print(
                f"Attempt {attempt} failed: {last_error}", file=sys.stderr
            )
            # Clean up partial download
            tmp_path = save_path + ".tmp"
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

            if attempt < max_retries:
                wait_time = BACKOFF_BASE * attempt  # 3s, 6s, 9s
                print(f"Retrying in {wait_time}s...", file=sys.stderr)
                time.sleep(wait_time)

    return False, f"Download failed after {max_retries} attempts: {last_error}", 0


def print_result(success, filepath="", filesize=0, url="", stock_code="",
                 report_type="", year="", message="", filepaths=None,
                 completed_years=None, failed_years=None, requested_years=None):
    """Print structured result block for Claude to parse."""
    if success and failed_years:
        status = "PARTIAL"
    else:
        status = "SUCCESS" if success else "FAILED"

    filepaths = filepaths or []
    completed_years = completed_years or []
    failed_years = failed_years or []
    requested_years = requested_years or ([] if not year else [str(year)])

    print("\n---RESULT---")
    print(f"status: {status}")
    print(f"filepath: {filepath}")
    print(f"filepaths: {','.join(filepaths)}")
    print(f"filesize: {filesize}")
    print(f"url: {url}")
    print(f"stock_code: {stock_code}")
    print(f"report_type: {report_type}")
    print(f"year: {year}")
    print(f"requested_years: {','.join(requested_years)}")
    print(f"completed_years: {','.join(completed_years)}")
    print(f"failed_years: {','.join(failed_years)}")
    print(f"message: {message}")
    print("---END---")


def main(argv=None):
    args = parse_args(argv)

    years = determine_years_to_download(
        report_type=args.report_type,
        explicit_year=args.year,
        recent_years=args.recent_years,
    )

    os.makedirs(args.save_dir, exist_ok=True)

    completed_years = []
    failed_years = []
    downloaded_paths = []
    last_url = ""
    last_message = ""
    last_filesize = 0
    summary_fallback_years = []

    for target_year in years:
        download_url = args.url

        if not download_url:
            try:
                _, _, best_candidate = discover_report(
                    stock_code=args.stock_code,
                    year=target_year,
                    report_type=args.report_type,
                )
            except requests.RequestException as exc:
                failed_years.append(target_year)
                last_message = f"Report discovery failed for {target_year}: {exc}"
                print(f"Error: {last_message}", file=sys.stderr)
                continue

            if not best_candidate:
                failed_years.append(target_year)
                last_message = (
                    f"Report discovery failed: no matching report found for "
                    f"{args.stock_code} {target_year} {args.report_type}"
                )
                print(f"Error: {last_message}", file=sys.stderr)
                continue

            download_url = best_candidate["url"]
            if best_candidate.get("match_quality") == "summary_fallback":
                summary_fallback_years.append(target_year)

        valid, err_msg = validate_url(download_url)
        if not valid:
            print(f"Error: {err_msg}", file=sys.stderr)
            print_result(
                success=False,
                url=download_url,
                stock_code=args.stock_code,
                report_type=args.report_type,
                year=target_year,
                message=err_msg,
                requested_years=years,
            )
            sys.exit(EXIT_BAD_ARGUMENTS)

        filename = build_filename(args.stock_code, args.report_type, target_year)
        save_path = os.path.join(args.save_dir, filename)
        success, message, filesize = download_annual_report(
            url=download_url,
            save_path=save_path,
            max_retries=args.max_retries,
        )

        last_url = download_url
        last_message = message
        last_filesize = filesize

        if success:
            completed_years.append(target_year)
            downloaded_paths.append(os.path.abspath(save_path))
        else:
            failed_years.append(target_year)

    overall_success = bool(completed_years)
    summary_message = last_message
    if completed_years and failed_years:
        summary_message = (
            f"Downloaded years: {','.join(completed_years)}; "
            f"failed years: {','.join(failed_years)}"
        )
    elif completed_years:
        summary_message = f"Downloaded years: {','.join(completed_years)}"
    elif failed_years and last_message:
        summary_message = last_message

    if summary_fallback_years:
        fallback_note = f"summary fallback used for years: {','.join(summary_fallback_years)}"
        if summary_message:
            summary_message = f"{summary_message}; {fallback_note}"
        else:
            summary_message = fallback_note

    print_result(
        success=overall_success,
        filepath=downloaded_paths[0] if len(downloaded_paths) == 1 else "",
        filepaths=downloaded_paths,
        filesize=last_filesize,
        url=last_url,
        stock_code=args.stock_code,
        report_type=args.report_type,
        year=args.year or years[0],
        completed_years=completed_years,
        failed_years=failed_years,
        requested_years=years,
        message=summary_message,
    )

    if not overall_success:
        if "validation" in summary_message.lower():
            sys.exit(EXIT_PDF_VALIDATION_FAILURE)
        sys.exit(EXIT_NETWORK_FAILURE)

    if failed_years:
        sys.exit(EXIT_NETWORK_FAILURE)

    sys.exit(EXIT_SUCCESS)


if __name__ == "__main__":
    main()
