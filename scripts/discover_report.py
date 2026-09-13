#!/usr/bin/env python3
"""Discover A-share financial report PDF URLs with CNINFO-first fallbacks."""

import argparse
from datetime import datetime
import html
import re
import sys

import requests


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
    mapping = {
        "annual": "年报",
        "interim": "中报",
        "q1": "一季报",
        "q3": "三季报",
    }
    return mapping.get(report_type.lower(), report_type)


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
    normalized = normalize_report_type(report_type)
    if normalized == "年报":
        return "category_ndbg_szsh"
    return ""


def build_cninfo_date_range(year, report_type):
    normalized = normalize_report_type(report_type)
    target_year = int(year)
    if normalized == "年报":
        return f"{target_year}-01-01~{target_year + 2}-12-31"
    return f"{target_year}-01-01~{target_year + 1}-12-31"


def format_cninfo_timestamp(timestamp_ms):
    if not timestamp_ms:
        return ""
    return datetime.utcfromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d")


def extract_cninfo_candidates(payload):
    candidates = []
    for announcement in payload.get("announcements") or []:
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


def should_prefer_cninfo(stock_code, report_type):
    return is_a_share_stock_code(stock_code) and normalize_report_type(report_type) == "年报"


def get_required_keywords(report_type):
    normalized = normalize_report_type(report_type)
    if normalized == "年报":
        return ["年度报告", "年报"]
    if normalized == "中报":
        return ["半年度报告", "中报"]
    if normalized == "一季报":
        return ["第一季度报告", "一季报"]
    if normalized == "三季报":
        return ["第三季度报告", "三季报"]
    return [normalized]


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
    ]
    lowered = title.lower()
    return any(keyword.lower() in lowered for keyword in excluded_keywords)


def score_candidate(candidate, year, report_type):
    title = candidate["title"]
    if is_summary_title(title):
        return None
    if is_excluded_title(title):
        return None

    keywords = get_required_keywords(report_type)
    if not any(keyword in title for keyword in keywords):
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
                 message=""):
    status = "SUCCESS" if success else "FAILED"
    print("\n---RESULT---")
    print(f"status: {status}")
    print(f"stock_page_url: {stock_page_url}")
    print(f"report_url: {report_url}")
    print(f"title: {title}")
    print(f"date: {date}")
    print(f"candidate_count: {count}")
    print(f"message: {message}")
    print("---END---")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Discover A-share report PDF URL with CNINFO and 10jqka fallbacks"
    )
    parser.add_argument("--stock-code", required=True, help="Stock code, e.g. 000858")
    parser.add_argument("--year", required=True, help="Fiscal year, e.g. 2025")
    parser.add_argument("--report-type", default="年报", help="年报/中报/一季报/三季报")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    try:
        stock_page_url, candidates, best = discover_report(
            stock_code=args.stock_code,
            year=args.year,
            report_type=args.report_type,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print_result(False, message=str(exc))
        sys.exit(EXIT_BAD_ARGUMENTS)
    except requests.RequestException as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print_result(False, message=str(exc))
        sys.exit(EXIT_NETWORK_FAILURE)

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

    print_result(
        True,
        stock_page_url=stock_page_url,
        report_url=best["url"],
        title=best["title"],
        date=best["date"],
        count=len(candidates),
        message=(
            "Summary fallback match found"
            if best.get("match_quality") == "summary_fallback"
            else "Match found"
        ),
    )
    sys.exit(EXIT_SUCCESS)


if __name__ == "__main__":
    main()
