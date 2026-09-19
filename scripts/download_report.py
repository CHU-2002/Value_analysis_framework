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
from datetime import date, datetime, timezone
import hashlib
import json
import os
import re
import sys
import time

import requests

from discover_report import discover_periods, discover_report

try:
    from periods import (
        REPORT_TYPES,
        months_since,
        normalize_report_type as _normalize_period_report_type,
        parse_period,
        period_sort_key,
        period_to_filename,
    )
except ImportError:  # Support importing with the repository root on sys.path.
    from scripts.periods import (
        REPORT_TYPES,
        months_since,
        normalize_report_type as _normalize_period_report_type,
        parse_period,
        period_sort_key,
        period_to_filename,
    )

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
        default=None,
        help=(
            "Report type (年报/中报/一季报/三季报/annual/interim/q1/q3) or 'auto' "
            "(default: 年报, or auto when --latest/--since is used)"
        ),
    )
    parser.add_argument(
        "--year", help="Report year (e.g. 2024). If omitted with 年报, defaults to latest fiscal year."
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Download the newest published regular report of any type",
    )
    parser.add_argument(
        "--since",
        help="Download every published period at or after this period, e.g. 2026Q1",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download periods already recorded in the source index",
    )
    parser.add_argument(
        "--lookback-months",
        type=int,
        default=18,
        help="Announcement lookback window used by periodic discovery (default: 18)",
    )
    parser.add_argument(
        "--sources-index",
        default="",
        help="Period source index to write/merge (default: <save-dir>/sources_index.json in periodic mode)",
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
    return _normalize_period_report_type(report_type)


def _is_auto_report_type(report_type):
    return (report_type or "").strip().lower() in {"auto", "latest"}


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
                 completed_years=None, failed_years=None, requested_years=None,
                 latest_period="", periods=None, completed_periods=None,
                 failed_periods=None, periods_skipped=None):
    """Print structured result block for Claude to parse."""
    if success and (failed_years or failed_periods):
        status = "PARTIAL"
    else:
        status = "SUCCESS" if success else "FAILED"

    filepaths = filepaths or []
    completed_years = completed_years or []
    failed_years = failed_years or []
    requested_years = requested_years or ([] if not year else [str(year)])
    completed_periods = completed_periods or []
    failed_periods = failed_periods or []
    periods_skipped = periods_skipped or []
    periods = periods or completed_periods + failed_periods

    print("\n---RESULT---")
    print(f"status: {status}")
    print(f"filepath: {filepath}")
    print(f"filepaths: {','.join(filepaths)}")
    print(f"filesize: {filesize}")
    print(f"url: {url}")
    print(f"stock_code: {stock_code}")
    print(f"report_type: {report_type}")
    print(f"year: {year}")
    print(f"latest_period: {latest_period}")
    print(f"periods_requested: {','.join(periods)}")
    print(f"periods_completed: {','.join(completed_periods)}")
    print(f"periods_failed: {','.join(failed_periods)}")
    print(f"periods_skipped: {','.join(periods_skipped)}")
    print(f"requested_years: {','.join(requested_years)}")
    print(f"completed_years: {','.join(completed_years)}")
    print(f"failed_years: {','.join(failed_years)}")
    print(f"message: {message}")
    print("---END---")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_key(value):
    match = re.search(r"(\d{5,6})", str(value or ""))
    return match.group(1) if match else str(value or "")


def covered_periods(index_path, stock_code=None):
    """Periods already recorded in a source index and still usable on disk.

    An entry only counts when its PDF exists, is non-empty, matches the recorded
    size, and is not flagged as a failed download (a failure must be retried on
    the next run). A relative ``filepath`` is resolved against the index
    location, and an index belonging to a different stock is ignored.
    """

    if not index_path or not os.path.exists(index_path):
        return set()
    try:
        with open(index_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return set()
    if not isinstance(payload, dict):
        return set()

    index_code = payload.get("stock_code")
    if stock_code and isinstance(index_code, str) and index_code.strip():
        if _code_key(index_code) != _code_key(stock_code):
            return set()

    periods = payload.get("periods")
    if not isinstance(periods, dict):
        return set()

    base_dir = os.path.dirname(os.path.abspath(index_path))
    covered = set()
    for period, entry in periods.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("last_download_status") == "failed":
            continue
        filepath = entry.get("filepath")
        if not isinstance(filepath, str) or not filepath:
            continue
        resolved = filepath if os.path.isabs(filepath) else os.path.join(base_dir, filepath)
        if not os.path.isfile(resolved):
            continue
        size = os.path.getsize(resolved)
        if size <= 0:
            continue
        recorded_size = entry.get("size_bytes")
        if isinstance(recorded_size, int) and recorded_size > 0 and recorded_size != size:
            continue
        covered.add(period)
    return covered


def resolve_auto_targets(
    stock_code,
    *,
    report_type=None,
    since=None,
    lookback_months=18,
    covered=(),
):
    """Pick the periods to download in periodic (--latest/--since/auto) mode.

    When ``since`` reaches further back than the default window, the lookback is
    widened so periods are not silently skipped. Periods already present in
    ``covered`` are returned separately instead of being re-downloaded.
    """

    effective_lookback = lookback_months
    if since:
        effective_lookback = max(lookback_months, months_since(since))

    periods = discover_periods(
        stock_code,
        report_type=report_type,
        lookback_months=effective_lookback,
    )
    if not periods:
        return [], None, []

    latest = periods[0]
    if since:
        wanted = [
            candidate
            for candidate in periods
            if period_sort_key(candidate["period"]) >= period_sort_key(since)
        ]
    else:
        wanted = [latest]

    covered_set = set(covered)
    targets = [candidate for candidate in wanted if candidate["period"] not in covered_set]
    skipped = [candidate for candidate in wanted if candidate["period"] in covered_set]
    return targets, latest, skipped


def write_sources_index(path, *, stock_code, latest_period, entries, failed_periods=()):
    """Write or merge the period -> source file index."""

    existing = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                existing = json.load(handle)
        except (OSError, ValueError):
            existing = {}

    raw_periods = existing.get("periods") if isinstance(existing, dict) else None
    periods = dict(raw_periods) if isinstance(raw_periods, dict) else {}
    for entry in entries:
        periods[entry["period"]] = entry

    # A failed re-download must not leave a stale entry claiming a file that is
    # gone from disk; annotate the ones whose recorded file still exists.
    for period in failed_periods:
        recorded = periods.get(period)
        if not isinstance(recorded, dict):
            continue
        filepath = recorded.get("filepath")
        if isinstance(filepath, str) and os.path.exists(filepath):
            recorded["last_download_status"] = "failed"
        else:
            periods.pop(period, None)

    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)

    payload = {
        "schema": "investment.report_sources",
        "schema_version": "1.0",
        "stock_code": stock_code,
        "latest_period": latest_period,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "periods": periods,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def run_auto_download(args):
    """Download the newest published regular report, or every period since --since."""

    os.makedirs(args.save_dir, exist_ok=True)
    normalized_type = normalize_report_type(args.report_type)
    report_type = args.report_type if normalized_type in REPORT_TYPES else None

    if args.since:
        try:
            since_year, _ = parse_period(args.since)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            print_result(False, stock_code=args.stock_code, report_type=args.report_type, message=str(exc))
            sys.exit(EXIT_BAD_ARGUMENTS)
        if since_year > date.today().year:
            message = f"--since {args.since} is in the future"
            print(f"Error: {message}", file=sys.stderr)
            print_result(False, stock_code=args.stock_code, report_type=args.report_type, message=message)
            sys.exit(EXIT_BAD_ARGUMENTS)

    index_path = args.sources_index or os.path.join(args.save_dir, "sources_index.json")
    covered = set() if args.force else covered_periods(index_path, stock_code=args.stock_code)

    try:
        targets, latest, skipped = resolve_auto_targets(
            args.stock_code,
            report_type=report_type,
            since=args.since,
            lookback_months=args.lookback_months,
            covered=covered,
        )
    except requests.RequestException as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print_result(
            False,
            stock_code=args.stock_code,
            report_type=args.report_type,
            message=f"Periodic discovery failed: {exc}",
        )
        sys.exit(EXIT_NETWORK_FAILURE)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print_result(False, stock_code=args.stock_code, report_type=args.report_type, message=str(exc))
        sys.exit(EXIT_BAD_ARGUMENTS)

    latest_period = latest["period"] if latest else ""
    skipped_periods = [candidate["period"] for candidate in skipped]
    if not targets:
        if skipped_periods:
            # Nothing missing: every discovered period in range is already held.
            print_result(
                True,
                stock_code=args.stock_code,
                report_type=args.report_type,
                latest_period=latest_period,
                periods=skipped_periods,
                completed_periods=[],
                periods_skipped=skipped_periods,
                message=f"All periods already present: {','.join(skipped_periods)}",
            )
            sys.exit(EXIT_SUCCESS)
        if args.since:
            message = (
                f"No published regular report found for {args.stock_code} "
                f"at or after {args.since}"
            )
        else:
            message = (
                f"No published regular report found for {args.stock_code} "
                f"in the last {args.lookback_months} months"
            )
        print_result(
            False,
            stock_code=args.stock_code,
            report_type=args.report_type,
            latest_period=latest_period,
            message=message,
        )
        sys.exit(EXIT_NETWORK_FAILURE)

    requested_periods = [target["period"] for target in targets]
    completed_periods = []
    failed_periods = []
    downloaded_paths = []
    index_entries = []
    last_url = ""
    last_message = ""
    last_filesize = 0

    for target in targets:
        period = target["period"]
        download_url = target["url"]

        valid, err_msg = validate_url(download_url)
        if not valid:
            # One bad upstream link must not abort the whole catch-up batch.
            failed_periods.append(period)
            print(f"Error: {period}: {err_msg}", file=sys.stderr)
            continue

        filename = period_to_filename(args.stock_code, period)
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
            completed_periods.append(period)
            downloaded_paths.append(os.path.abspath(save_path))
            index_entries.append(
                {
                    "period": period,
                    "report_type": target.get("report_type", ""),
                    "title": target.get("title", ""),
                    "date": target.get("date", ""),
                    "url": download_url,
                    "filename": filename,
                    "filepath": os.path.abspath(save_path),
                    "size_bytes": filesize,
                    "sha256": sha256_file(save_path),
                    "source": target.get("source", "cninfo"),
                }
            )
        else:
            failed_periods.append(period)
            print(f"Error: {message}", file=sys.stderr)

    write_sources_index(
        index_path,
        stock_code=args.stock_code,
        latest_period=latest_period,
        entries=index_entries,
        failed_periods=failed_periods,
    )

    overall_success = bool(completed_periods)
    if completed_periods and failed_periods:
        summary_message = (
            f"Downloaded periods: {','.join(completed_periods)}; "
            f"failed periods: {','.join(failed_periods)}"
        )
    elif completed_periods:
        summary_message = f"Downloaded periods: {','.join(completed_periods)}"
    else:
        summary_message = last_message or "No period downloaded"

    print_result(
        success=overall_success,
        filepath=downloaded_paths[0] if len(downloaded_paths) == 1 else "",
        filepaths=downloaded_paths,
        filesize=last_filesize,
        url=last_url,
        stock_code=args.stock_code,
        report_type=args.report_type,
        year="",
        latest_period=latest_period,
        periods=requested_periods,
        completed_periods=completed_periods,
        failed_periods=failed_periods,
        periods_skipped=skipped_periods,
        message=summary_message,
    )

    if not overall_success:
        if "validation" in summary_message.lower():
            sys.exit(EXIT_PDF_VALIDATION_FAILURE)
        sys.exit(EXIT_NETWORK_FAILURE)

    if failed_periods:
        sys.exit(EXIT_NETWORK_FAILURE)

    sys.exit(EXIT_SUCCESS)


def main(argv=None):
    args = parse_args(argv)

    auto_mode = bool(args.latest or args.since or _is_auto_report_type(args.report_type))
    if args.url and auto_mode:
        print(
            "Error: --url cannot be combined with --latest/--since/--report-type auto",
            file=sys.stderr,
        )
        print_result(False, stock_code=args.stock_code, message="--url conflicts with periodic mode")
        sys.exit(EXIT_BAD_ARGUMENTS)
    if auto_mode and args.recent_years is not None:
        print("Error: --recent-years does not apply to periodic mode", file=sys.stderr)
        print_result(False, stock_code=args.stock_code, message="--recent-years conflicts with periodic mode")
        sys.exit(EXIT_BAD_ARGUMENTS)
    if auto_mode and args.year:
        print("Error: --year does not apply to periodic mode", file=sys.stderr)
        print_result(False, stock_code=args.stock_code, message="--year conflicts with periodic mode")
        sys.exit(EXIT_BAD_ARGUMENTS)
    if auto_mode and args.latest and args.since:
        print("Error: --latest and --since are mutually exclusive", file=sys.stderr)
        print_result(False, stock_code=args.stock_code, message="--latest conflicts with --since")
        sys.exit(EXIT_BAD_ARGUMENTS)
    if not auto_mode and args.force:
        print("Error: --force only applies to periodic mode", file=sys.stderr)
        print_result(False, stock_code=args.stock_code, message="--force conflicts with the year path")
        sys.exit(EXIT_BAD_ARGUMENTS)
    if args.report_type is None:
        # --latest/--since already imply "any type"; otherwise keep 年报.
        args.report_type = "auto" if auto_mode else "年报"

    if auto_mode:
        run_auto_download(args)

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
