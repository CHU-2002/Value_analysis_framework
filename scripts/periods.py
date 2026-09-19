#!/usr/bin/env python3
"""Period (定期报告期次) helpers shared by report discovery and download.

A period is a compact identifier for one regular financial report:

    2026Q1 | 2026H1 | 2026Q3 | 2026FY

For A-shares, ``Q1`` / ``H1`` / ``Q3`` are year-to-date cumulative disclosures
and ``FY`` is the full year. This module is pure and offline-testable so that
both ``discover_report`` and ``download_report`` agree on the same vocabulary.
"""

from __future__ import annotations

import html
import re
from typing import Iterable

REPORT_TYPES = ("年报", "中报", "一季报", "三季报")

# report_type -> period suffix
REPORT_TYPE_SUFFIX = {
    "年报": "FY",
    "中报": "H1",
    "一季报": "Q1",
    "三季报": "Q3",
}

# period suffix -> report_type
SUFFIX_REPORT_TYPE = {suffix: report_type for report_type, suffix in REPORT_TYPE_SUFFIX.items()}

# Period suffix order inside a fiscal year (also the sort weight).
SUFFIX_ORDER = {"Q1": 1, "H1": 2, "Q3": 3, "FY": 4}

# English / alias normalization for report types.
_REPORT_TYPE_ALIASES = {
    "annual": "年报",
    "annual report": "年报",
    "fy": "年报",
    "year": "年报",
    "interim": "中报",
    "semiannual": "中报",
    "semi-annual": "中报",
    "half": "中报",
    "h1": "中报",
    "半年报": "中报",
    "半年報": "中报",
    "中期报告": "中报",
    "中期報告": "中报",
    "q1": "一季报",
    "first quarter": "一季报",
    "q3": "三季报",
    "third quarter": "三季报",
}

_YEAR_RE = re.compile(r"(20\d{2})")
_PERIOD_RE = re.compile(r"^(20\d{2})(Q1|H1|Q3|FY)$")

# Ordered so that a more specific label wins: "半年度报告" also contains
# "年度报告", and "半年报" also contains "年报".
_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Q1", ("第一季度报告", "第一季度報告", "一季度报告", "一季度報告", "一季报", "一季報")),
    ("Q3", ("第三季度报告", "第三季度報告", "三季度报告", "三季度報告", "三季报", "三季報")),
    ("H1", ("半年度报告", "半年度報告", "中期报告", "中期報告", "半年报", "半年報", "中报", "中報")),
    ("FY", ("年度报告", "年度報告", "年报", "年報")),
)


def normalize_report_type(report_type: str) -> str:
    """Normalize a report type to one of ``年报/中报/一季报/三季报``."""

    raw = (report_type or "").strip()
    if not raw:
        return raw
    if raw in REPORT_TYPES:
        return raw
    alias = _REPORT_TYPE_ALIASES.get(raw.lower())
    if alias:
        return alias
    return raw


def make_period(year: int | str, report_type: str) -> str:
    """Build a period identifier from a fiscal year and report type."""

    normalized = normalize_report_type(report_type)
    if normalized not in REPORT_TYPE_SUFFIX:
        raise ValueError(f"Unsupported report type: {report_type!r}")
    year_int = int(year)
    if not 1900 <= year_int <= 2999:
        raise ValueError(f"Unsupported fiscal year: {year!r}")
    return f"{year_int}{REPORT_TYPE_SUFFIX[normalized]}"


def is_valid_period(period: str) -> bool:
    return isinstance(period, str) and bool(_PERIOD_RE.match(period.strip()))


def parse_period(period: str) -> tuple[int, str]:
    """Split a period identifier into ``(fiscal_year, report_type)``."""

    match = _PERIOD_RE.match((period or "").strip())
    if not match:
        raise ValueError(f"Invalid period: {period!r}")
    return int(match.group(1)), SUFFIX_REPORT_TYPE[match.group(2)]


def period_sort_key(period: str) -> tuple[int, int]:
    """Sort key ordering periods chronologically by fiscal coverage."""

    year, report_type = parse_period(period)
    return year, SUFFIX_ORDER[REPORT_TYPE_SUFFIX[report_type]]


def comparable_period(period: str) -> str:
    """Return the prior-year same-period identifier (for 同比 comparison)."""

    year, report_type = parse_period(period)
    return make_period(year - 1, report_type)


def previous_period(period: str) -> str | None:
    """Return the immediately preceding disclosure period."""

    year, report_type = parse_period(period)
    suffix = REPORT_TYPE_SUFFIX[report_type]
    if suffix == "Q1":
        return make_period(year - 1, "年报")
    if suffix == "H1":
        return make_period(year, "一季报")
    if suffix == "Q3":
        return make_period(year, "中报")
    return make_period(year, "三季报")


def single_quarter_base(period: str) -> str | None:
    """Period to subtract to obtain the single-quarter figure.

    ``Q1`` is already a single quarter, so ``None`` is returned.
    """

    year, report_type = parse_period(period)
    suffix = REPORT_TYPE_SUFFIX[report_type]
    if suffix == "Q1":
        return None
    if suffix == "H1":
        return make_period(year, "一季报")
    if suffix == "Q3":
        return make_period(year, "中报")
    return make_period(year, "三季报")


def parse_period_from_title(title: str, report_type: str | None = None) -> str | None:
    """Extract a period from an announcement title.

    Adjacency between the fiscal year and the report label is required, so a
    title such as ``关于2024年年度报告的更正公告`` still parses to ``2024FY``
    (callers filter disallowed titles separately), while unrelated years in the
    same title do not produce a false match.
    """

    if not title:
        return None
    text = re.sub(r"\s+", "", html.unescape(title))
    wanted_suffix = None
    if report_type:
        normalized = normalize_report_type(report_type)
        wanted_suffix = REPORT_TYPE_SUFFIX.get(normalized)

    matches = list(_YEAR_RE.finditer(text))
    for index, match in enumerate(matches):
        year = match.group(1)
        # The label must sit between this year and the next year mentioned, so an
        # unrelated earlier year cannot borrow a later year's report label.
        boundary = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        window = text[match.end(): min(boundary, match.end() + 16)]
        for suffix, keywords in _TYPE_KEYWORDS:
            if not any(keyword in window for keyword in keywords):
                continue
            if wanted_suffix is not None and suffix != wanted_suffix:
                break  # this year is a different report type; try the next year
            return f"{year}{suffix}"
    return None


def period_to_filename(stock_code: str, period: str) -> str:
    """Build the canonical ``{code}_{year}_{report_type}.pdf`` filename."""

    year, report_type = parse_period(period)
    code = re.sub(r"^(SH|SZ)", "", stock_code or "", flags=re.IGNORECASE)
    return f"{code}_{year}_{report_type}.pdf"


def filename_to_period(filename: str) -> str | None:
    """Recover a period from a canonical report filename (or any title-like text)."""

    if not filename:
        return None
    stem = re.sub(r"\.pdf$", "", str(filename), flags=re.IGNORECASE)
    match = re.match(r"^[A-Za-z]*\d{5,6}_(20\d{2})_(.+)$", stem)
    if match:
        year, label = match.groups()
        normalized = normalize_report_type(label)
        if normalized in REPORT_TYPE_SUFFIX:
            return make_period(year, normalized)
    return parse_period_from_title(stem)


def sort_periods(periods: Iterable[str], *, reverse: bool = True) -> list[str]:
    """Sort period identifiers chronologically (newest first by default)."""

    valid = [period for period in periods if is_valid_period(period)]
    return sorted(set(valid), key=period_sort_key, reverse=reverse)


def list_periods_between(start: str, end: str) -> list[str]:
    """Return every period from ``start`` to ``end`` inclusive, newest first."""

    start_key = period_sort_key(start)
    end_key = period_sort_key(end)
    if start_key > end_key:
        start, end = end, start
        start_key, end_key = end_key, start_key
    start_year, _ = parse_period(start)
    end_year, _ = parse_period(end)
    periods = [
        make_period(year, report_type)
        for year in range(start_year, end_year + 1)
        for report_type in REPORT_TYPES
    ]
    return sort_periods(
        (period for period in periods if start_key <= period_sort_key(period) <= end_key),
        reverse=False,
    )
