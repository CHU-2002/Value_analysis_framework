#!/usr/bin/env python3
"""Discover A-share financial report PDF URLs with CNINFO-first fallbacks."""

import argparse
from datetime import date, datetime, timedelta, timezone
import html
import re
import sys

import requests

try:
    from scripts.periods import (
        REPORT_TYPES,
        normalize_report_type as _normalize_period_report_type,
        parse_period,
        parse_period_from_title,
        period_sort_key,
        report_type_keywords,
    )
except ImportError:  # Support importing with scripts/ on sys.path.
    from periods import (
        REPORT_TYPES,
        normalize_report_type as _normalize_period_report_type,
        parse_period,
        parse_period_from_title,
        period_sort_key,
        report_type_keywords,
    )


DEFAULT_TIMEOUT = 30
CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC_BASE_URL = "https://static.cninfo.com.cn/"
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# CNINFO announcement categories for regular (periodic) reports.
CNINFO_CATEGORIES = {
    "年报": "category_ndbg_szsh",
    "中报": "category_bndbg_szsh",
    "一季报": "category_yjdbg_szsh",
    "三季报": "category_sjdbg_szsh",
}

# The query API accepts several categories joined by semicolons.
CNINFO_ALL_REGULAR_CATEGORIES = ";".join(CNINFO_CATEGORIES.values()) + ";"

CNINFO_HEADERS = {
    "User-Agent": BASE_HEADERS["User-Agent"],
    "Accept-Language": BASE_HEADERS["Accept-Language"],
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
}

PDF_LINK_RE = re.compile(
    r'<a\s+href="(?P<url>https?://notice\.10jqka\.com\.cn/api/pdf/[^"]+\.pdf)"[^>]*>'
    r'(?P<title>.*?)</a>.*?<em>(?P<date>[^<]+)</em>',
    re.IGNORECASE | re.DOTALL,
)

EXIT_SUCCESS = 0
EXIT_NO_MATCH = 1
EXIT_NETWORK_FAILURE = 2
EXIT_BAD_ARGUMENTS = 3


def normalize_report_type(report_type):
    return _normalize_period_report_type(report_type)


def extract_numeric_code(stock_code):
    match = re.search(r"(\d{5,6})", stock_code)
    if not match:
        raise ValueError(f"Unsupported stock code: {stock_code}")
    return match.group(1)


def build_stockpage_url(stock_code):
    return f"https://stockpage.10jqka.com.cn/{extract_numeric_code(stock_code)}/"


def is_a_share_stock_code(stock_code):
    numeric_code = extract_numeric_code(stock_code)
    return len(numeric_code) == 6 and numeric_code[0] in {"0", "3", "6"}


def normalize_title(text):
    return re.sub(r"\s+", "", html.unescape(text))


def normalize_company_name(text):
    return re.sub(r"\s+", "", html.unescape(text or ""))


def extract_company_name(page_html):
    attr_match = re.search(r'stockname="([^"]+)"', page_html)
    if attr_match:
        return normalize_company_name(attr_match.group(1))

    title_match = re.search(r"<title>([^<(]+)\((\d{5,6})\)", page_html)
    if title_match:
        return normalize_company_name(title_match.group(1))

    return ""


def build_spaced_company_name(company_name):
    normalized = normalize_company_name(company_name)
    if not normalized or len(normalized) <= 1:
        return normalized
    return " ".join(normalized)


def fetch_company_name(stock_code, timeout=DEFAULT_TIMEOUT):
    """Best-effort company name lookup for the CNINFO keyword fallback."""

    try:
        response = requests.get(
            build_stockpage_url(stock_code), headers=BASE_HEADERS, timeout=timeout
        )
        response.raise_for_status()
    except requests.RequestException:
        return ""
    return extract_company_name(response.text)


def is_summary_title(title):
    lowered = title.lower()
    return "摘要" in title or "summary" in lowered


def extract_pdf_candidates(page_html):
    candidates = []
    for match in PDF_LINK_RE.finditer(page_html):
        candidates.append(
            {
                "url": match.group("url").replace("http://", "https://", 1),
                "title": normalize_title(match.group("title")),
                "date": match.group("date").strip(),
            }
        )
    return candidates


def build_cninfo_search_keywords(stock_code, company_name):
    keywords = []
    numeric_code = extract_numeric_code(stock_code)
    if is_a_share_stock_code(stock_code):
        keywords.append(numeric_code)

    normalized_name = normalize_company_name(company_name)
    if normalized_name:
        keywords.append(normalized_name)

        spaced_name = build_spaced_company_name(normalized_name)
        if spaced_name != normalized_name:
            keywords.append(spaced_name)

    deduped = []
    seen = set()
    for keyword in keywords:
        if keyword and keyword not in seen:
            seen.add(keyword)
            deduped.append(keyword)
    return deduped


def get_cninfo_column(stock_code):
    numeric_code = extract_numeric_code(stock_code)
    return "sse" if numeric_code.startswith("6") else "szse"


def get_cninfo_category(report_type):
    return CNINFO_CATEGORIES.get(normalize_report_type(report_type), "")


def build_cninfo_date_range(year, report_type):
    normalized = normalize_report_type(report_type)
    target_year = int(year)
    if normalized == "年报":
        return f"{target_year}-01-01~{target_year + 2}-12-31"
    return f"{target_year}-01-01~{target_year + 1}-12-31"


def build_recent_date_range(lookback_months=18, today=None):
    """Rolling announcement-date window ending today (inclusive)."""

    end = today or date.today()
    start = end - timedelta(days=max(1, int(lookback_months)) * 31)
    return f"{start.isoformat()}~{end.isoformat()}"


CNINFO_TZ = timezone(timedelta(hours=8))


def format_cninfo_timestamp(timestamp_ms):
    """Format a CNINFO epoch-ms announcement time as its Beijing calendar date.

    CNINFO timestamps are Beijing midnight; interpreting them as UTC shifts the
    date back one day and would also mis-rank same-day original/corrected forms.
    """

    if not timestamp_ms:
        return ""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=CNINFO_TZ).strftime("%Y-%m-%d")


def _announcements(payload):
    """Return a usable announcement list for any malformed CNINFO payload."""

    if not isinstance(payload, dict):
        return []
    announcements = payload.get("announcements")
    if not isinstance(announcements, list):
        return []
    return [item for item in announcements if isinstance(item, dict)]


def extract_cninfo_candidates(payload):
    candidates = []
    for announcement in _announcements(payload):
        adjunct_url = announcement.get("adjunctUrl")
        if not adjunct_url:
            continue

        candidates.append(
            {
                "url": CNINFO_STATIC_BASE_URL + adjunct_url.lstrip("/"),
                "title": normalize_title(announcement.get("announcementTitle", "")),
                "date": format_cninfo_timestamp(announcement.get("announcementTime")),
                "source": "cninfo",
            }
        )
    return candidates


def discover_cninfo_report(stock_code, company_name, year, report_type, timeout=DEFAULT_TIMEOUT):
    if not is_a_share_stock_code(stock_code):
        return []

    category = get_cninfo_category(report_type)
    if not category:
        return []

    all_candidates = []
    for keyword in build_cninfo_search_keywords(stock_code, company_name):
        response = requests.post(
            CNINFO_QUERY_URL,
            headers=CNINFO_HEADERS,
            data={
                "pageNum": 1,
                "pageSize": 50,
                "tabName": "fulltext",
                "stock": "",
                "searchkey": keyword,
                "column": get_cninfo_column(stock_code),
                "category": category,
                "seDate": build_cninfo_date_range(year, report_type),
            },
            timeout=timeout,
        )
        response.raise_for_status()
        candidates = extract_cninfo_candidates(response.json())
        if candidates:
            all_candidates.extend(candidates)
            break

    deduped = []
    seen_urls = set()
    for candidate in all_candidates:
        if candidate["url"] in seen_urls:
            continue
        seen_urls.add(candidate["url"])
        deduped.append(candidate)
    return deduped


def extract_cninfo_period_candidates(payload, stock_code=None):
    """Turn CNINFO announcements into period-tagged full-report candidates.

    ``tabName=fulltext`` is a full-text search, so when ``stock_code`` is given
    any announcement whose ``secCode`` belongs to another issuer is dropped —
    otherwise a peer's report could occupy one of this company's period slots.
    """

    wanted_code = None
    if stock_code is not None:
        wanted_code = extract_numeric_code(stock_code)

    candidates = []
    for announcement in _announcements(payload):
        adjunct_url = announcement.get("adjunctUrl")
        if not adjunct_url:
            continue
        if announcement.get("adjunctType") not in (None, "", "PDF"):
            continue

        sec_code = announcement.get("secCode")
        if wanted_code and sec_code and str(sec_code).strip() != wanted_code:
            continue

        title = normalize_title(announcement.get("announcementTitle", ""))
        if is_summary_title(title) or is_excluded_title(title):
            continue

        period = parse_period_from_title(title)
        if not period:
            continue

        _, report_type = parse_period(period)
        candidates.append(
            {
                "url": CNINFO_STATIC_BASE_URL + adjunct_url.lstrip("/"),
                "title": title,
                "date": format_cninfo_timestamp(announcement.get("announcementTime")),
                "period": period,
                "report_type": report_type,
                "source": "cninfo",
            }
        )
    return candidates


def _period_candidate_rank(candidate):
    """Prefer the newest announcement, and a corrected version on a tie."""

    return (candidate.get("date") or "", "更新后" in candidate.get("title", ""))


def _query_cninfo_periods(stock_code, keywords, *, lookback_months, timeout, today, max_pages=5):
    """Query CNINFO for period-tagged reports, following pagination."""

    page_size = 100
    se_date = build_recent_date_range(lookback_months, today=today)
    candidates = []
    for keyword in keywords:
        scanned = 0
        for page in range(1, max_pages + 1):
            response = requests.post(
                CNINFO_QUERY_URL,
                headers=CNINFO_HEADERS,
                data={
                    "pageNum": page,
                    "pageSize": page_size,
                    "tabName": "fulltext",
                    "stock": "",
                    "searchkey": keyword,
                    "column": get_cninfo_column(stock_code),
                    "category": CNINFO_ALL_REGULAR_CATEGORIES,
                    "seDate": se_date,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            candidates.extend(extract_cninfo_period_candidates(payload, stock_code=stock_code))

            announcements = _announcements(payload)
            scanned += len(announcements)
            if not announcements:
                break

            total_pages = payload.get("totalpages") if isinstance(payload, dict) else None
            total_records = payload.get("totalRecordNum") if isinstance(payload, dict) else None
            if isinstance(total_records, int) and total_records > 0:
                more_pages = scanned < total_records
            elif isinstance(total_pages, int) and total_pages > 0:
                more_pages = page < total_pages
            else:
                more_pages = len(announcements) >= page_size

            if not more_pages:
                break
            if page >= max_pages:
                print(
                    f"Warning: stopped at the page cap ({max_pages}); some older "
                    f"announcements may not have been scanned",
                    file=sys.stderr,
                )
                break
        if candidates:
            break
    return candidates


def discover_periods(
    stock_code,
    company_name="",
    *,
    report_type=None,
    lookback_months=18,
    timeout=DEFAULT_TIMEOUT,
    today=None,
):
    """Discover the newest published report for every period in the window.

    Returns a list of period-tagged candidates sorted newest-period-first (by
    fiscal period, not by announcement date). Non-A-share codes and unknown
    report types return an empty list rather than raising, so callers can
    decide how to degrade. When no candidates are found with the code keyword,
    the 10jqka company name is fetched lazily and used as a second keyword.
    """

    if not is_a_share_stock_code(stock_code):
        return []

    wanted = normalize_report_type(report_type) if report_type else None
    if wanted is not None and wanted not in REPORT_TYPES:
        return []

    keywords = build_cninfo_search_keywords(stock_code, company_name)
    candidates = _query_cninfo_periods(
        stock_code,
        keywords,
        lookback_months=lookback_months,
        timeout=timeout,
        today=today,
    )

    if not candidates and not normalize_company_name(company_name):
        fallback_name = fetch_company_name(stock_code, timeout=timeout)
        extra_keywords = [
            keyword
            for keyword in build_cninfo_search_keywords(stock_code, fallback_name)
            if keyword not in keywords
        ]
        if extra_keywords:
            candidates = _query_cninfo_periods(
                stock_code,
                extra_keywords,
                lookback_months=lookback_months,
                timeout=timeout,
                today=today,
            )

    by_period = {}
    for candidate in candidates:
        if wanted is not None and candidate["report_type"] != wanted:
            continue
        current = by_period.get(candidate["period"])
        if current is None or _period_candidate_rank(candidate) > _period_candidate_rank(current):
            by_period[candidate["period"]] = candidate

    return sorted(
        by_period.values(),
        key=lambda candidate: period_sort_key(candidate["period"]),
        reverse=True,
    )



def discover_latest_period(
    stock_code,
    company_name="",
    *,
    report_type=None,
    lookback_months=18,
    timeout=DEFAULT_TIMEOUT,
    today=None,
):
    """Return the newest published regular report, or ``None`` when absent."""

    periods = discover_periods(
        stock_code,
        company_name,
        report_type=report_type,
        lookback_months=lookback_months,
        timeout=timeout,
        today=today,
    )
    return periods[0] if periods else None


def should_prefer_cninfo(stock_code, report_type):
    """CNINFO is the authoritative source for every A-share regular report."""

    return is_a_share_stock_code(stock_code) and normalize_report_type(report_type) in REPORT_TYPES


def get_required_keywords(report_type):
    """Title labels accepted for a report type, shared with ``periods``."""

    keywords = report_type_keywords(report_type)
    if keywords:
        return list(keywords)
    return [normalize_report_type(report_type)]


def is_excluded_title(title):
    excluded_keywords = [
        "审计报告",
        "公告",
        "利润分配",
        "可持续发展",
        "股东大会",
        "ESG",
        "auditor",
        "dividend",
        "更正",
        "补充",
        "意见",
        "内部控制",
        "英文",
        "取消",
        "提示性",
        "业绩说明会",
        "问询函",
    ]
    lowered = title.lower()
    if any(keyword.lower() in lowered for keyword in excluded_keywords):
        return True
    # H-share-only versions of a regular report are not the A-share filing.
    # "A股" is allowed alongside "H股" so an A+H combined title is kept.
    return "h股" in lowered and "a股" not in lowered


def score_candidate(candidate, year, report_type):
    title = candidate["title"]
    if is_summary_title(title):
        return None
    if is_excluded_title(title):
        return None

    keywords = get_required_keywords(report_type)
    if not any(keyword in title for keyword in keywords):
        return None
    # Substring matching alone lets "半年度报告"/"半年报" satisfy the annual
    # keywords ("年度报告"/"年报"). Require the shared period parser to agree
    # that this title is the requested report type.
    if parse_period_from_title(title, report_type) is None:
        return None
    if str(year) not in title:
        return None

    score = 0
    exact_patterns = [f"{year}年{keyword}" for keyword in keywords]
    if any(pattern in title for pattern in exact_patterns):
        score += 10
    if str(year) in title:
        score += 5
    if "更新后" in title:
        score -= 1
    if "(" in title or "（" in title:
        score -= 1
    score -= len(title) / 1000
    return score


def score_summary_fallback_candidate(candidate, year, report_type):
    title = candidate["title"]
    if not is_summary_title(title):
        return None

    fallback_candidate = dict(candidate)
    fallback_candidate["title"] = title.replace("摘要", "").replace("summary", "")
    score = score_candidate(fallback_candidate, year, report_type)
    if score is None:
        return None

    return score - 5


def select_best_candidate(candidates, year, report_type, allow_summary_fallback=True):
    scored = []
    for candidate in candidates:
        score = score_candidate(candidate, year, report_type)
        if score is not None:
            scored.append((score, candidate))

    if not scored:
        normalized = normalize_report_type(report_type)
        if normalized != "年报" or not allow_summary_fallback:
            return None

        summary_scored = []
        for candidate in candidates:
            score = score_summary_fallback_candidate(candidate, year, report_type)
            if score is not None:
                summary_candidate = dict(candidate)
                summary_candidate["match_quality"] = "summary_fallback"
                summary_scored.append((score, summary_candidate))

        if not summary_scored:
            return None

        summary_scored.sort(key=lambda item: item[0], reverse=True)
        return summary_scored[0][1]

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def discover_report(stock_code, year, report_type, timeout=DEFAULT_TIMEOUT):
    url = build_stockpage_url(stock_code)
    company_name = ""
    candidates = []
    stockpage_error = None

    try:
        response = requests.get(url, headers=BASE_HEADERS, timeout=timeout)
        response.raise_for_status()
        company_name = extract_company_name(response.text)
        candidates.extend(extract_pdf_candidates(response.text))
    except requests.RequestException as exc:
        stockpage_error = exc

    best = None
    cninfo_candidates = []
    if should_prefer_cninfo(stock_code, report_type):
        cninfo_candidates = discover_cninfo_report(
            stock_code=stock_code,
            company_name=company_name,
            year=year,
            report_type=report_type,
            timeout=timeout,
        )
        if cninfo_candidates:
            candidates = cninfo_candidates + candidates
            best = select_best_candidate(cninfo_candidates, year, report_type, allow_summary_fallback=False)

    if not best:
        best = select_best_candidate(candidates, year, report_type, allow_summary_fallback=False)

    if not best and not cninfo_candidates and not should_prefer_cninfo(stock_code, report_type):
        cninfo_candidates = discover_cninfo_report(
            stock_code=stock_code,
            company_name=company_name,
            year=year,
            report_type=report_type,
            timeout=timeout,
        )
        if cninfo_candidates:
            candidates.extend(cninfo_candidates)
            best = select_best_candidate(cninfo_candidates, year, report_type, allow_summary_fallback=False)

    if not best:
        best = select_best_candidate(candidates, year, report_type, allow_summary_fallback=True)

    if stockpage_error and not candidates:
        raise stockpage_error

    return url, candidates, best


def print_result(success, stock_page_url="", report_url="", title="", date="", count=0,
                 message="", period="", report_type=""):
    status = "SUCCESS" if success else "FAILED"
    print("\n---RESULT---")
    print(f"status: {status}")
    print(f"stock_page_url: {stock_page_url}")
    print(f"report_url: {report_url}")
    print(f"title: {title}")
    print(f"date: {date}")
    print(f"period: {period}")
    print(f"report_type: {report_type}")
    print(f"candidate_count: {count}")
    print(f"message: {message}")
    print("---END---")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Discover A-share report PDF URL with CNINFO and 10jqka fallbacks"
    )
    parser.add_argument("--stock-code", required=True, help="Stock code, e.g. 000858")
    parser.add_argument("--year", help="Fiscal year, e.g. 2025 (not needed with --latest)")
    parser.add_argument(
        "--report-type",
        default=None,
        help=(
            "年报/中报/一季报/三季报, or 'auto' for the newest published regular "
            "report of any type (default: 年报, or auto when --latest is given)"
        ),
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Discover the newest published regular report instead of a fixed year",
    )
    return parser.parse_args(argv)


def _is_auto_report_type(report_type):
    return (report_type or "").strip().lower() in {"auto", "latest"}


def run_latest_discovery(args):
    """Handle --latest / --report-type auto."""

    report_type = None
    if normalize_report_type(args.report_type) in REPORT_TYPES:
        report_type = args.report_type

    best = discover_latest_period(args.stock_code, report_type=report_type)
    if not best:
        message = (
            f"No published regular report found for {args.stock_code} "
            f"in the recent discovery window"
        )
        print_result(False, message=message)
        sys.exit(EXIT_NO_MATCH)

    print_result(
        True,
        report_url=best["url"],
        title=best["title"],
        date=best["date"],
        count=1,
        period=best["period"],
        report_type=best["report_type"],
        message="Latest regular report found",
    )
    sys.exit(EXIT_SUCCESS)


def main(argv=None):
    args = parse_args(argv)

    # No explicit type: --latest means "newest of any type", otherwise keep the
    # historical 年报 default.
    if args.report_type is None:
        args.report_type = "auto" if args.latest else "年报"

    if args.latest or _is_auto_report_type(args.report_type):
        try:
            run_latest_discovery(args)
        except requests.RequestException as exc:
            print(f"Error: {exc}", file=sys.stderr)
            print_result(False, message=str(exc))
            sys.exit(EXIT_NETWORK_FAILURE)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            print_result(False, message=str(exc))
            sys.exit(EXIT_BAD_ARGUMENTS)

    if not args.year:
        print("Error: --year is required unless --latest or --report-type auto is used", file=sys.stderr)
        print_result(False, message="Missing --year")
        sys.exit(EXIT_BAD_ARGUMENTS)

    try:
        stock_page_url, candidates, best = discover_report(
            stock_code=args.stock_code,
            year=args.year,
            report_type=args.report_type,
        )
    except requests.RequestException as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print_result(False, message=str(exc))
        sys.exit(EXIT_NETWORK_FAILURE)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print_result(False, message=str(exc))
        sys.exit(EXIT_BAD_ARGUMENTS)

    if not best:
        message = (
            f"No matching {normalize_report_type(args.report_type)} found for "
            f"{args.stock_code} {args.year}"
        )
        print_result(
            False,
            stock_page_url=stock_page_url,
            count=len(candidates),
            message=message,
        )
        sys.exit(EXIT_NO_MATCH)

    period = parse_period_from_title(best.get("title", ""), args.report_type)
    print_result(
        True,
        stock_page_url=stock_page_url,
        report_url=best["url"],
        title=best["title"],
        date=best["date"],
        count=len(candidates),
        period=period or "",
        report_type=normalize_report_type(args.report_type),
        message=(
            "Summary fallback match found"
            if best.get("match_quality") == "summary_fallback"
            else "Match found"
        ),
    )
    sys.exit(EXIT_SUCCESS)


if __name__ == "__main__":
    main()
