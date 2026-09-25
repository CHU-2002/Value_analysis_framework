#!/usr/bin/env python3
"""Value Analysis Framework - PDF Preprocessor (Phase 2A).

Scans annual report PDFs for 9 target sections using keyword matching
and outputs structured JSON for Agent fine-extraction.

Target sections:
    P2: Restricted cash (受限资产)
    P3: AR aging (应收账款账龄)
    P4: Related party transactions (关联方交易)
    P6: Contingent liabilities (或有负债)
    P13: Non-recurring items (非经常性损益)
    MDA: Management Discussion & Analysis (管理层讨论与分析)
    GOV: Corporate governance (公司治理)
    MATTERS: Significant matters (重要事项)
    SUB: Subsidiary holdings (主要控股参股公司)

Usage:
    python3 scripts/pdf_preprocessor.py --pdf report.pdf
    python3 scripts/pdf_preprocessor.py --pdf report.pdf --output output/sections.json
    python3 scripts/pdf_preprocessor.py --pdf 600887_2025_年报.pdf
    python3 scripts/pdf_preprocessor.py --pdf report.pdf --period 2026H1
    python3 scripts/pdf_preprocessor.py --pdf report.pdf --verbose --dry-run

When the report period can be resolved (either from ``--period`` or from the
canonical ``{code}_{year}_{report_type}.pdf`` filename) and no ``--output`` is
given, the sections are written to ``pdf_sections_{period}.json`` next to the
PDF. Without a resolvable period the legacy ``output/pdf_sections.json``
default is kept unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pdfplumber

try:  # Imported as a package (``scripts.pdf_preprocessor``).
    from scripts.periods import filename_to_period, is_valid_period
except ImportError:  # ``scripts/`` is on sys.path (direct script execution).
    from periods import filename_to_period, is_valid_period


# Legacy default, kept for backward compatibility when no period is resolvable.
DEFAULT_OUTPUT = "output/pdf_sections.json"


# ---------------------------------------------------------------------------
# Feature #38: SECTION_KEYWORDS for 5 target sections
# Feature #43: Traditional Chinese keyword support
# ---------------------------------------------------------------------------

SECTION_KEYWORDS: Dict[str, List[str]] = {
    "P2": [
        # Simplified Chinese
        "所有权或使用权受限资产",
        "主要资产受限情况",
        "资产受限情况",
        "受限资产",
        "使用受限的资产",
        "所有权受限",
        "使用权受到限制",
        "受限的货币资金",
        "受限制的银行存款",
        "受限制的货币资金",
        "受到限制的资产",
        # Traditional Chinese (HK reports)
        "所有權或使用權受限資產",
        "受限資產",
        "使用受限的資產",
    ],
    "P3": [
        # Simplified Chinese
        "应收账款账龄",
        "应收账款的账龄",
        "账龄分析",
        "应收账款按账龄披露",
        "应收账款按账龄列示",
        "应收款项账龄",
        # Traditional Chinese
        "應收賬款賬齡",
        "應收賬款的賬齡",
        "賬齡分析",
    ],
    "P4": [
        # Simplified Chinese
        "关联方交易",
        "关联交易",
        "关联方及关联交易",
        "关联方关系及其交易",
        "重大关联交易",
        # Traditional Chinese
        "關聯方交易",
        "關聯交易",
        "關聯方及關聯交易",
    ],
    "P6": [
        # Simplified Chinese
        "或有负债",
        "或有事项",
        "未决诉讼",
        "重大诉讼",
        "对外担保",
        "承诺及或有事项",
        "承诺和或有负债",
        # Traditional Chinese
        "或有負債",
        "或有事項",
        "未決訴訟",
        "承諾及或有事項",
    ],
    "P13": [
        # Simplified Chinese - specific (prefer these for supplement zone)
        "非经常性损益项目及金额",
        "非经常性损益合计",
        # Simplified Chinese - general
        "非经常性损益",
        "非经常性损益明细",
        "非经常性损益项目",
        "扣除非经常性损益",
        "非经常性损益的项目和金额",
        # Traditional Chinese
        "非經常性損益",
        "非經常性損益明細",
        "非經常性損益項目及金額",
    ],
    "MDA": [
        # HK-style chairman's report carries the operating review for many
        # HK-listed issuers. List the most specific section titles first so the
        # per-page keyword loop prefers them over generic mentions.
        "董事长报告书",
        "董事长报告",
        "主席报告书",
        "主席报告",
        "致股东信",
        # Simplified Chinese
        "管理层讨论与分析",
        "经营情况讨论与分析",
        "经营情况的讨论与分析",
        "管理层分析与讨论",
        "董事会报告书",
        "董事会报告",
        # Traditional Chinese
        "管理層討論與分析",
        "經營情況討論與分析",
        "董事會報告",
    ],
    "GOV": [
        # Specific section titles first; generic "公司治理" can appear as an
        # incidental mention inside the business review and must not win.
        "企业管治报告",
        "公司治理报告",
        "企業管治報告",
        "公司治理",
        "董事、监事和高级管理人员",
        "董事、監事和高級管理人員",
    ],
    "MATTERS": [
        "重要事项",
        "重大事项",
        "重要事項",
        "重大事項",
        "重大诉讼、仲裁事项",
        "重大訴訟、仲裁事項",
    ],
    "SUB": [
        # 高特异性 — 主匹配
        "主要控股参股公司分析",
        "主要子公司及对公司净利润的影响",
        "主要控股参股公司情况",
        "控股子公司情况",
        # 中特异性
        "在子公司中的权益",
        "在其他主体中的权益",
        "纳入合并范围的主体",
        "合并范围的变化",
        # 删除: "长期股权投资" (歧义太大，匹配到 Note #17)
        # 新增: 更具体的变体
        "长期股权投资——对子公司",
        "长期股权投资——联营企业",
        # 繁体
        "主要控股參股公司分析",
        "在子公司中的權益",
        "在其他主體中的權益",
        "長期股權投資——對子公司",
    ],
}

# Per-section extraction parameters (overrides defaults)
SECTION_EXTRACT_CONFIG: Dict[str, Dict[str, int]] = {
    # max_chars 按真实中报的章节长度设定：旧值 8000 会把 MD&A 正文尾部截掉
    # （2026-09-25 实跑 F12）。前置上下文不再占用正文预算，见 extract_section_context。
    "MDA": {"buffer_pages": 3, "max_chars": 20000},
    "GOV": {"buffer_pages": 3, "max_chars": 20000},
    "MATTERS": {"buffer_pages": 3, "max_chars": 20000},
    "SUB": {"buffer_pages": 2, "max_chars": 6000},
}

#: 前置上下文至少要能放这么多字才附在正文后面（否则直接丢掉，不占预算）。
PREFIX_MIN_CHARS = 200
DEFAULT_BUFFER_PAGES = 1
DEFAULT_MAX_CHARS = 4000

# ---------------------------------------------------------------------------
# Zone detection markers for A-share annual reports (CSRC format)
# ---------------------------------------------------------------------------

ZONE_MARKERS: List[Tuple[str, str]] = [
    (r"第[一二三四五六七八九十百]+节\s*重要提示", "INTRO_ZONE"),
    (r"第[一二三四五六七八九十百]+节\s*公司简介", "INTRO_ZONE"),
    (r"第[一二三四五六七八九十百]+节\s*管理层讨论与分析", "MDA_ZONE"),
    (r"第[一二三四五六七八九十百]+节\s*经营情况讨论与分析", "MDA_ZONE"),
    (r"第[一二三四五六七八九十百]+节\s*公司治理", "GOVERNANCE_ZONE"),
    (r"第[一二三四五六七八九十百]+节\s*(?:重要|重大)事项", "MATTERS_ZONE"),
    (r"第[一二三四五六七八九十百]+节\s*财务报告", "FIN_ZONE"),
    (r"第[一二三四五六七八九十百]+节\s*会计数据", "FIN_ZONE"),
    # Sub-zones within financial report
    (r"[四五六]\s*[、.．]\s*重要会计政策", "POLICY_ZONE"),
    (r"七\s*[、.．]\s*合并财务报表项目注释", "NOTES_ZONE"),
    (r"[一二三四五六七八九十]+[、.．]\s*补充资料", "SUPPLEMENT_ZONE"),
]

SECTION_ZONE_PREFERENCES: Dict[str, Dict[str, List[str]]] = {
    "P2":  {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE"]},
    "P3":  {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE"]},
    "P4":  {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE"]},
    "P6":  {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE"]},
    "P13": {"prefer": ["SUPPLEMENT_ZONE", "NOTES_ZONE"], "avoid": ["POLICY_ZONE"]},
    "MDA": {"prefer": ["MDA_ZONE"], "avoid": ["NOTES_ZONE", "FIN_ZONE", "POLICY_ZONE", "SUPPLEMENT_ZONE"]},
    "GOV": {"prefer": ["GOVERNANCE_ZONE"], "avoid": ["NOTES_ZONE", "FIN_ZONE", "POLICY_ZONE", "SUPPLEMENT_ZONE"]},
    "MATTERS": {"prefer": ["MATTERS_ZONE"], "avoid": ["NOTES_ZONE", "FIN_ZONE", "POLICY_ZONE", "SUPPLEMENT_ZONE"]},
    "SUB": {"prefer": ["NOTES_ZONE"], "avoid": ["POLICY_ZONE"]},
}


# ---------------------------------------------------------------------------
# Feature #37: PDF text extraction with pdfplumber
# Feature #44: PyMuPDF fallback for garbled text
# Feature #45: Table-aware extraction
# ---------------------------------------------------------------------------

def is_garbled(text: str, threshold: float = 0.30) -> bool:
    """Detect garbled text: >threshold fraction of non-CJK/ASCII/common-punct chars."""
    if not text:
        return True
    # Characters we consider "normal" in a Chinese annual report
    normal = 0
    for ch in text:
        cp = ord(ch)
        if (
            0x20 <= cp <= 0x7E  # ASCII printable
            or 0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF  # CJK Extension A
            or 0x3000 <= cp <= 0x303F  # CJK Punctuation
            or 0xFF00 <= cp <= 0xFFEF  # Fullwidth Forms
            or ch in "\n\r\t"
        ):
            normal += 1
    ratio = normal / len(text)
    return ratio < (1 - threshold)


def _tables_to_markdown(tables: list) -> str:
    """Convert pdfplumber tables to markdown format."""
    parts = []
    for table in tables:
        if not table or len(table) < 2:
            continue
        # Clean cells
        cleaned = []
        for row in table:
            cleaned.append([
                (cell or "").replace("\n", " ").strip()
                for cell in row
            ])
        # Build markdown table
        header = cleaned[0]
        md = "| " + " | ".join(header) + " |\n"
        md += "| " + " | ".join(["---"] * len(header)) + " |\n"
        for row in cleaned[1:]:
            # Pad row if shorter than header
            while len(row) < len(header):
                row.append("")
            md += "| " + " | ".join(row[:len(header)]) + " |\n"
        parts.append(md)
    return "\n".join(parts)


def reflow_two_column_words(words: List[Dict], page_width: float) -> Optional[str]:
    """把 pdfplumber 的 words 按**栏**重排：左栏读完整栏再读右栏。

    2026-09-25 实跑（F23）：双栏页用 ``extract_text()`` 线性化会把左右栏交错拼接
    （「该类别票**据是由信用风险较**低银行出具」），这种串行错乱一旦进证据索引就无法引用。
    只在页面明显是两栏（中缝无跨栏词）时才重排，其余情况返回 None 交回 ``extract_text()``。
    """

    if not words or page_width <= 0:
        return None
    word_list = [item for item in words if isinstance(item, dict) and "x0" in item and "x1" in item]
    if len(word_list) < 40:
        return None
    mid = page_width / 2
    center_lo, center_hi = page_width * 0.45, page_width * 0.55
    near_center = [
        item for item in word_list
        if center_lo <= (float(item["x0"]) + float(item["x1"])) / 2 <= center_hi
    ]
    if len(near_center) > max(2, int(len(word_list) * 0.02)):
        return None
    left = [item for item in word_list if (float(item["x0"]) + float(item["x1"])) / 2 < mid]
    right = [item for item in word_list if (float(item["x0"]) + float(item["x1"])) / 2 >= mid]
    if not left or not right:
        return None

    def _lines(column: List[Dict]) -> List[str]:
        lines: List[List[Dict]] = []
        for item in sorted(column, key=lambda w: (float(w.get("top", 0)), float(w["x0"]))):
            if lines and abs(float(item.get("top", 0)) - float(lines[-1][0].get("top", 0))) <= 3:
                lines[-1].append(item)
            else:
                lines.append([item])
        return ["".join(str(w.get("text", "")) for w in sorted(line, key=lambda w: float(w["x0"])))
                for line in lines]

    return "\n".join([*_lines(left), *_lines(right)])


def _extract_page_text(page) -> str:
    """优先按栏重排双栏页；失败或不适用时退回 pdfplumber 的线性文本。"""

    plain = page.extract_text() or ""
    try:
        words = page.extract_words()
    except Exception:  # pragma: no cover - pdfplumber 内部异常时退回线性文本
        return plain
    if not isinstance(words, list):
        return plain
    reflowed = reflow_two_column_words(words, float(getattr(page, "width", 0) or 0))
    return reflowed or plain


def extract_all_pages(pdf_path: str, verbose: bool = False) -> List[Tuple[int, str]]:
    """Extract text from all pages of a PDF using pdfplumber.

    Falls back to PyMuPDF if pdfplumber produces garbled text.

    Args:
        pdf_path: Path to the PDF file.
        verbose: Print progress messages.

    Returns:
        List of (page_number_1indexed, text) tuples.

    Raises:
        FileNotFoundError: If the PDF file doesn't exist.
        RuntimeError: If the PDF cannot be opened or is encrypted.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pages_text: List[Tuple[int, str]] = []
    garbled_count = 0

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            if verbose:
                print(f"Extracting {total} pages with pdfplumber...")

            for i, page in enumerate(pdf.pages):
                page_num = i + 1
                text = _extract_page_text(page)

                # Feature #45: table-aware extraction
                tables = page.extract_tables()
                if tables:
                    table_md = _tables_to_markdown(tables)
                    if table_md:
                        text = text + "\n\n[TABLE]\n" + table_md
                # 同一页的原文与 markup 表格是两份表达：多行列头的大表（重大担保表）
                # 原文那份列值对不上，必须让结构化表格版本成为唯一进入索引的一份
                # （REQ-006.2 AC-2.4）。
                text = _drop_raw_duplicates_of_tables(text)

                if is_garbled(text) and len(text) > 50:
                    garbled_count += 1

                pages_text.append((page_num, text))

                if verbose and page_num % 50 == 0:
                    print(f"  ...page {page_num}/{total}")

    except Exception as e:
        err_msg = str(e).lower()
        if "encrypt" in err_msg or "password" in err_msg:
            raise RuntimeError(f"PDF is encrypted: {pdf_path}") from e
        raise RuntimeError(f"Cannot open PDF: {pdf_path}: {e}") from e

    # Feature #44: PyMuPDF fallback if >30% pages are garbled
    if total > 0 and garbled_count / total > 0.30:
        if verbose:
            print(f"Garbled text detected ({garbled_count}/{total} pages), trying PyMuPDF...")
        fallback = fallback_extract_pymupdf(pdf_path, verbose=verbose)
        if fallback:
            return fallback

    return pages_text


def fallback_extract_pymupdf(pdf_path: str, verbose: bool = False) -> Optional[List[Tuple[int, str]]]:
    """Fallback extraction using PyMuPDF (fitz).

    Returns None if PyMuPDF is not installed.
    """
    try:
        import fitz
    except ImportError:
        if verbose:
            print("PyMuPDF not installed, skipping fallback.")
        return None

    pages_text: List[Tuple[int, str]] = []
    try:
        doc = fitz.open(pdf_path)
        total = len(doc)
        if verbose:
            print(f"Extracting {total} pages with PyMuPDF...")
        for i in range(total):
            page = doc[i]
            text = page.get_text() if hasattr(page, 'get_text') else page.getText()
            pages_text.append((i + 1, text or ""))
        doc.close()
    except Exception as e:
        if verbose:
            print(f"PyMuPDF fallback failed: {e}")
        return None

    return pages_text


# ---------------------------------------------------------------------------
# Zone detection for A-share annual report structure
# ---------------------------------------------------------------------------

def detect_zones(pages_text: List[Tuple[int, str]]) -> Dict[int, str]:
    """Detect report structure zones by scanning for section markers.

    Returns:
        Dict mapping page_number -> zone_name. Pages without a detected
        zone inherit from the most recent zone marker before them.
        Returns empty dict if no zone markers are found.
    """
    zone_transitions: List[Tuple[int, str]] = []

    for page_num, text in pages_text:
        if not text:
            continue
        for pattern, zone_name in ZONE_MARKERS:
            if re.search(pattern, text):
                zone_transitions.append((page_num, zone_name))
                break  # first matching marker per page

    if not zone_transitions:
        return {}

    # Build page->zone mapping (each page inherits from last marker)
    zone_transitions.sort(key=lambda x: x[0])
    page_zones: Dict[int, str] = {}
    current_zone = None
    transition_idx = 0

    for page_num, _ in pages_text:
        while transition_idx < len(zone_transitions) and zone_transitions[transition_idx][0] <= page_num:
            current_zone = zone_transitions[transition_idx][1]
            transition_idx += 1
        if current_zone:
            page_zones[page_num] = current_zone

    return page_zones


# ---------------------------------------------------------------------------
# Feature #39: Keyword matching to locate sections
# Feature #46: Section priority scoring
# ---------------------------------------------------------------------------

def _score_match(
    page_num: int, total_pages: int, text: str, keyword: str,
    zone: Optional[str] = None, section_id: Optional[str] = None,
) -> float:
    """Score a keyword match: prefer correct report zone over TOC.

    Scoring:
        +1.0 base for a match
        +2.0 if page is in a preferred zone for this section
        -2.0 if page is in an avoided zone for this section
        +0.5 fallback position bonus if no zone info available
        -0.5 if page looks like TOC (contains "目录" or "目 录")
        +0.3 if keyword appears in a heading-like context (numbered section)
        -0.3 if keyword only appears as a cross-reference ("详见")
    """
    score = 1.0

    # Zone-aware scoring (replaces position bonus when zone info available)
    if zone and section_id and section_id in SECTION_ZONE_PREFERENCES:
        prefs = SECTION_ZONE_PREFERENCES[section_id]
        if zone in prefs.get("prefer", []):
            score += 2.0
        elif zone in prefs.get("avoid", []):
            score -= 2.0
    elif total_pages > 0:
        # Fallback: position-based scoring when no zone info
        if page_num / total_pages > 0.30:
            score += 0.5
        # Keyword specificity: longer, section-title-like keywords are stronger
        # signals than generic terms (e.g. "企业管治报告" over "公司治理",
        # "董事长报告书" over "董事会报告"). Important for HK-style reports
        # where zone detection finds no CSRC "第X节" markers.
        score += 0.12 * len(keyword)

    # Penalize TOC pages
    if "目录" in text or "目 录" in text:
        score -= 0.5

    # Penalize cross-references ("详见注释七'31、所有权或使用权受限资产'")
    kw_pos = text.find(keyword)
    if kw_pos > 0:
        before = text[max(0, kw_pos - 30):kw_pos]
        if "详见" in before or "参见" in before or "参照" in before:
            score -= 0.3

    # SUB context scoring: penalize accounting detail, reward subsidiary operating data
    if section_id == "SUB" and kw_pos >= 0:
        context_window = text[max(0, kw_pos - 200):min(len(text), kw_pos + 200)]
        # Penalize: accounting detail context
        acct = ["权益法", "账面余额", "减值准备", "成本法", "账面价值"]
        if sum(1 for a in acct if a in context_window) >= 2:
            score -= 1.5
        # Reward: subsidiary operating data context
        subs = ["主营业务", "营业收入", "净利润", "注册资本", "持股比例"]
        if sum(1 for s in subs if s in context_window) >= 2:
            score += 1.0

    # P3 context scoring: penalize non-AR aging (prepayments, other payables)
    if section_id == "P3" and kw_pos >= 0:
        context_window = text[max(0, kw_pos - 200):min(len(text), kw_pos + 200)]
        non_ar = ["预付款项", "预付账款", "预付", "应付账款", "应付票据", "其他应付"]
        if any(term in context_window for term in non_ar):
            score -= 2.0

    # Bonus: keyword appears near a numbered heading pattern
    # e.g., "31、所有权或使用权受限资产" or "十四、关联方及关联交易"
    heading_patterns = [
        r"\d+[、.．]\s*" + re.escape(keyword),
        r"[一二三四五六七八九十]+[、.．]\s*" + re.escape(keyword),
    ]
    for pat in heading_patterns:
        if re.search(pat, text):
            score += 0.3
            break

    # Standalone heading bonus: a keyword forming (almost) the whole line is a
    # real section heading, not an inline cross-reference such as
    # "“董事长报告书”章节之…" nor a TOC entry. PDF extraction frequently puts
    # the heading on its own line in HK-style reports.
    if kw_pos >= 0:
        line_start = text.rfind("\n", 0, kw_pos) + 1
        line_end = text.find("\n", kw_pos)
        line = text[line_start: line_end if line_end != -1 else len(text)].strip()
        if line.startswith(keyword) and len(line) <= len(keyword) + 4:
            score += 1.0

    return score


def find_section_pages(
    pages_text: List[Tuple[int, str]],
    section_keywords: Dict[str, List[str]] = None,
) -> Dict[str, List[int]]:
    """Locate sections by scanning all pages for keywords.

    Args:
        pages_text: List of (page_number, text) tuples.
        section_keywords: Keyword dict (default: SECTION_KEYWORDS).

    Returns:
        Dict mapping section_id -> [page_numbers] sorted by priority score (best first).
    """
    if section_keywords is None:
        section_keywords = SECTION_KEYWORDS

    total_pages = len(pages_text)
    results: Dict[str, List[int]] = {}

    # Detect zones for scoring
    page_zones = detect_zones(pages_text)

    for section_id, keywords in section_keywords.items():
        # Collect (score, page_num) for all matches
        scored_matches: List[Tuple[float, int]] = []

        for page_num, text in pages_text:
            if not text:
                continue
            for kw in keywords:
                if kw in text:
                    zone = page_zones.get(page_num)
                    score = _score_match(page_num, total_pages, text, kw,
                                         zone=zone, section_id=section_id)
                    scored_matches.append((score, page_num))
                    break  # one keyword per page is enough

        # Sort by score descending, then by page number ascending as tiebreak
        scored_matches.sort(key=lambda x: (-x[0], x[1]))

        # Deduplicate page numbers while preserving order
        seen = set()
        ordered_pages = []
        for _, pn in scored_matches:
            if pn not in seen:
                seen.add(pn)
                ordered_pages.append(pn)

        results[section_id] = ordered_pages

    return results


# ---------------------------------------------------------------------------
# Feature #40: Context extraction with page buffer
# ---------------------------------------------------------------------------

def extract_section_context(
    pages_text: List[Tuple[int, str]],
    section_pages: Dict[str, List[int]],
    section_keywords: Dict[str, List[str]] = None,
    buffer_pages: int = 1,
    max_chars: int = 4000,
) -> Dict[str, Optional[str]]:
    """Extract context text for each section using best-match page +/- buffer.

    Centers the extraction around the first keyword match position on the
    target page to maximize relevance.

    Args:
        pages_text: List of (page_number, text) tuples.
        section_pages: Output from find_section_pages.
        section_keywords: Keywords dict for locating match position.
        buffer_pages: Number of pages before/after to include.
        max_chars: Maximum characters per section.

    Returns:
        Dict mapping section_id -> extracted text or None if not found.
    """
    if section_keywords is None:
        section_keywords = SECTION_KEYWORDS

    # Build a lookup: page_num -> text
    page_lookup: Dict[int, str] = {pn: text for pn, text in pages_text}

    contexts: Dict[str, Optional[str]] = {}

    for section_id, matched_pages in section_pages.items():
        if not matched_pages:
            contexts[section_id] = None
            continue

        # Per-section config overrides function defaults
        cfg = SECTION_EXTRACT_CONFIG.get(section_id, {})
        sect_buffer = cfg.get("buffer_pages", buffer_pages)
        sect_max = cfg.get("max_chars", max_chars)

        # Use the best-scored page (first in list)
        best_page = matched_pages[0]

        # 章节正文 = best_page 起；best_page 之前的 buffer 页是**前置上下文**，
        # 不属于本节正文。旧实现按页码升序拼接，前置页排在正文前面，于是
        # MDA:001 整块是上一节的非经常性损益表（2026-09-25 实跑 F12 / REQ-006.2 AC-2.4）。
        body_parts: List[str] = []
        prefix_parts: List[str] = []
        for offset in range(-sect_buffer, sect_buffer + 1):
            target = best_page + offset
            if target not in page_lookup:
                continue
            text = page_lookup[target]
            if not text:
                continue
            block = f"--- p.{target} ---\n{text}"
            (prefix_parts if offset < 0 else body_parts).append(block)

        body = "\n\n".join(body_parts)
        # 截断只在**正文**里做：代表块必须落在正文上。
        if body and len(body) > sect_max:
            body = _center_truncate(body, section_keywords.get(section_id, []), sect_max)

        combined = body
        prefix = "\n\n".join(prefix_parts)
        if prefix:
            label = "--- 前置上下文（上一节的页，非本节正文）---"
            remaining = max(0, sect_max - len(combined) - len(label) - 2)
            if remaining >= PREFIX_MIN_CHARS:
                combined = f"{combined}\n\n{label}\n{_truncate_at_boundary(prefix, remaining)}"
            elif not combined:
                # 只有前置页可用（best_page 没有文本）时不能把整节丢空。
                combined = prefix

        contexts[section_id] = combined

    return contexts


def _normalize_for_table_match(line: str) -> str:
    """把一行压成「去空白、去表格竖线」的形式，用于判断原文行是否与表格行重复。"""

    return "".join(ch for ch in line if not ch.isspace() and ch != "|")


def _table_content_tokens(table_lines: list[str]) -> tuple[set[str], dict[str, int], str]:
    """表格里出现过的内容标记、每个标记**出现在多少行**，以及整张表的规范化全文。

    返回 ``(全部标记, 标记 -> 出现行数, 规范化全文)``。前两项识别「整行 / 整格」与
    「被折行拆碎的单元格」；第三项用于判断一行原文是否**只由表格里已有的片段**组成
    （多行列头的原文复述就是这样：每个片段都能在表格里找到，但拼起来不属于任何整行）。
    """

    seen: set[str] = set()
    row_counts: dict[str, int] = {}
    normalized_parts: list[str] = []
    for table_line in table_lines:
        normalized_line = _normalize_for_table_match(table_line)
        if normalized_line:
            seen.add(normalized_line)
            normalized_parts.append(normalized_line)
        row_tokens: set[str] = set()
        for cell in table_line.split("|"):
            normalized_cell = _normalize_for_table_match(cell)
            if normalized_cell:
                seen.add(normalized_cell)
                row_tokens.add(normalized_cell)
        for token in row_tokens:
            row_counts[token] = row_counts.get(token, 0) + 1
    return seen, row_counts, "".join(normalized_parts)


def _raw_line_duplicates_table(
    line: str, table_seen: set[str], table_text: str = "",
) -> bool:
    """这一行原文是否只是表格内容的复述（是则不进索引，交给表格版本表达）。

    判据从强到弱：
    1. 整行（规范化后）就是表格的某一行 / 某个单元格——单格汇总行属这一类；
    2. 整行是表格文本的一段**连续子串**——表格把这句放进了一个 cell，原文单独起行；
    3. 按空白切分的**每个片段**都能在表格文本里找到——多行列头 / 折行单元格的原文
       复述就是这样：``担保发生日 担保是否 担保 是否为`` 四个片段分别来自表头的四个
       单元格，拼起来却不属于任何整行（pdfplumber 的列顺序与 markup 表格不同）。
       这一层只在**这一行没有引入表格之外的内容**时命中，所以原文里的正文（本节结论、
       表格未覆盖的句子）不会命中。
    """

    stripped = line.strip()
    if not stripped:
        return False
    # 页码 / 页标记 / 页眉：不是表格内容。
    if stripped.startswith("---") or stripped.startswith("内蒙古伊利实业集团股份有限公司"):
        return False
    normalized = _normalize_for_table_match(stripped)
    if not normalized:
        return False
    if normalized in table_seen:
        return True
    if table_text and normalized in table_text:
        return True
    if not table_text:
        return False
    tokens = [
        token
        for token in (_normalize_for_table_match(part) for part in stripped.split())
        if token
    ]
    # 至少两个片段、至少一个多字片段：避免把「√适用」「否」这类短标记误判成表格复述。
    if len(tokens) < 2 or not any(len(token) >= 2 for token in tokens):
        return False
    return all(token in table_text for token in tokens)


def _duplicate_raw_line_numbers(
    raw_lines: list[str], table_seen: set[str], token_row_counts: dict[str, int],
    table_text: str = "",
) -> set[int]:
    """找出原文段里「只是表格复述」的行号。

    四种形态都算重复：
    1. 整行文本就是表格里的某个 cell / 整行；
    2. 整行是表格文本的连续子串（表格把整句放进了一个 cell）；
    3. 整行按空白切成的每个片段都能在表格里找到（多行列头的未对齐复述）；
    4. 原文把表格的一行拆成多行，**连续**片段拼起来正好等于表格里的一行内容，
       或某个片段在表格的**多行**里都出现（被折行拆碎的金额 / 日期）。
    表格未覆盖的句子（如担保存续说明）四条都不命中，照旧保留。
    """

    duplicates = {
        index
        for index, line in enumerate(raw_lines)
        if _raw_line_duplicates_table(line, table_seen, table_text)
    }
    index = 0
    while index < len(raw_lines):
        if index in duplicates or not raw_lines[index].strip():
            index += 1
            continue
        merged = _normalize_for_table_match(raw_lines[index])
        cursor = index + 1
        while cursor < len(raw_lines) and raw_lines[cursor].strip() and cursor not in duplicates:
            candidate = merged + _normalize_for_table_match(raw_lines[cursor])
            if candidate not in table_seen:
                break
            merged = candidate
            duplicates.add(cursor)
            cursor += 1
        index += 1
    for index, line in enumerate(raw_lines):
        if index in duplicates:
            continue
        normalized = _normalize_for_table_match(line.strip())
        if normalized and token_row_counts.get(normalized, 0) >= 2:
            duplicates.add(index)
    return duplicates


def _drop_raw_duplicates_of_tables(text: str) -> str:
    """去掉与 `[TABLE]` 块内容重复的**原文行**，只留下结构化表格版本。

    同一页会先进一次原文（``extract_text()`` 的线性化结果），再进一次 markup 表格。对
    多行列头的大表（重大担保表：16 列）原文那一份的列与值对不上——「担保逾期金额」被
    压在别的表头下面、值 ``4,811.72`` 落在孤立的行里，而结构化表格是**列值一一对应**的。
    两份都进索引时，关键词打分很容易挑中原文那份（REQ-006.2 AC-2.4 实测：
    `pdf_sections:MATTERS:003` 被 `governance`/`period_delta` 当成担保证据，而列对齐的
    表格版 `MATTERS:004` 反而没被引用）。

    实现：把文本切成「原文段 / [TABLE] 表格段」，每个表格用它前一段原文来判定——表格
    开始**之前**的原文行凡是「只由表格里已有的内容组成」就删掉（见
    :func:`_raw_line_duplicates_table` 的四条判据：整行 / 连续子串 / 片段全覆盖 /
    折行拼接）；表格之后的原文（如本节结论、表格没覆盖的句子）与**第一个表格之前**的
    正文一律保留。表格行、表头、分隔行本身从不删。
    """

    if "[TABLE]" not in text:
        return text
    lines = text.splitlines()
    # 切成 [(kind, lines)]，kind ∈ {"raw", "table"}。
    segments: list[tuple[str, list[str]]] = []
    buffer: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == "[TABLE]":
            if buffer:
                segments.append(("raw", buffer))
                buffer = []
            table_lines = []
            index += 1
            while index < len(lines) and (not lines[index].strip() or lines[index].lstrip().startswith("|")):
                if lines[index].lstrip().startswith("|"):
                    table_lines.append(lines[index])
                index += 1
            segments.append(("table", table_lines))
            continue
        buffer.append(lines[index])
        index += 1
    if buffer:
        segments.append(("raw", buffer))
    if not any(kind == "table" for kind, _ in segments):
        return text

    # 每个表格只看它**前面**那一段原文：那里的重复就是同一页线性化的复述。
    # 表格之后的原文可能是本节结论或表格未覆盖的文字，保守保留。
    to_drop: set[int] = set()
    line_cursor = 0  # 当前段在 lines 里的起始行号
    for position, (kind, segment_lines) in enumerate(segments):
        if kind == "raw":
            segment_start = line_cursor
            line_cursor += len(segment_lines)
            continue
        # table 段：先跳过 [TABLE] 标记本身，再看它前一段原文。
        line_cursor += 1
        if position > 0 and segments[position - 1][0] == "raw":
            table_seen, token_row_counts, table_text = _table_content_tokens(segment_lines)
            raw_lines, raw_segment_start = segments[position - 1][1], segment_start
            for line_no in _duplicate_raw_line_numbers(
                raw_lines, table_seen, token_row_counts, table_text,
            ):
                to_drop.add(raw_segment_start + line_no)
        line_cursor += len(segment_lines)
    if not to_drop:
        return text
    return "\n".join(line for line_no, line in enumerate(lines) if line_no not in to_drop)


def _center_truncate(text: str, keywords: list, max_chars: int) -> str:
    """Truncate text centered around the first keyword match."""
    # Find the first keyword position
    match_pos = len(text)
    for kw in keywords:
        pos = text.find(kw)
        if pos >= 0 and pos < match_pos:
            match_pos = pos

    if match_pos == len(text):
        # No keyword found, fall back to simple truncation
        return _truncate_at_boundary(text, max_chars)

    # Center the window around the match
    half = max_chars // 2
    start = max(0, match_pos - half // 2)  # More text after match than before
    end = min(len(text), start + max_chars)
    start = max(0, end - max_chars)

    result = text[start:end]

    # Clean up: try to start at a page boundary or line boundary
    if start > 0:
        newline_pos = result.find("\n")
        if newline_pos >= 0 and newline_pos < 200:
            result = result[newline_pos + 1:]

    return _truncate_at_boundary(result, max_chars)


def _truncate_at_boundary(text: str, max_chars: int) -> str:
    """Truncate text at the last sentence boundary before max_chars."""
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]

    # Try to find last Chinese period, question mark, or newline
    for sep in ["。", "\n", "；", ".", "!", "！"]:
        last_pos = truncated.rfind(sep)
        if last_pos > max_chars * 0.5:  # Don't cut too aggressively
            return truncated[:last_pos + 1]

    return truncated


# ---------------------------------------------------------------------------
# Feature #41: JSON output writer
# ---------------------------------------------------------------------------

def write_output(
    contexts: Dict[str, Optional[str]],
    pdf_path: str,
    total_pages: int,
    output_path: str,
    period: str = "",
) -> dict:
    """Write pdf_sections.json with all configured sections plus metadata.

    ``period`` is the resolved report period (``2026H1`` / ``2025FY`` ...) and
    is recorded as an empty string in the metadata when it cannot be resolved.

    Returns the output dict for inspection.
    """
    found_count = sum(1 for v in contexts.values() if v is not None)

    output = {
        "metadata": {
            "pdf_file": os.path.basename(pdf_path),
            "total_pages": total_pages,
            "extract_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sections_found": found_count,
            "sections_total": len(contexts),
            "period": period or "",
        },
    }

    for section_id in SECTION_KEYWORDS:
        output[section_id] = contexts.get(section_id)

    # Ensure output directory exists
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return output


# ---------------------------------------------------------------------------
# Feature #42: Main pipeline
# ---------------------------------------------------------------------------

def _period_arg(value: str) -> str:
    """Argparse type for ``--period``: normalize and reject unknown periods."""

    normalized = str(value).strip().upper()
    if not is_valid_period(normalized):
        raise argparse.ArgumentTypeError(
            f"invalid period {value!r}: expected one of YYYYQ1, YYYYH1, YYYYQ3, YYYYFY"
        )
    return normalized


def resolve_output_path(pdf_path: str, period: str, output: Optional[str]) -> str:
    """Resolve the effective output path.

    An explicit ``--output`` always wins. Otherwise a resolvable period writes
    ``pdf_sections_{period}.json`` next to the PDF, while an unresolvable
    period keeps the legacy ``output/pdf_sections.json`` default.
    """

    if output is not None and str(output) == "":
        raise ValueError("--output must not be empty (omit it to use the default)")
    if output:
        return output
    if period:
        return os.path.join(os.path.dirname(str(pdf_path)), f"pdf_sections_{period}.json")
    return DEFAULT_OUTPUT


def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description="Extract target sections from annual report PDFs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --pdf 伊利股份_2024_年报.pdf
  %(prog)s --pdf report.pdf --output output/pdf_sections.json --verbose
  %(prog)s --pdf 600887_2025_年报.pdf            # -> ./pdf_sections_2025FY.json
  %(prog)s --pdf report.pdf --period 2026H1      # -> ./pdf_sections_2026H1.json
        """,
    )
    parser.add_argument(
        "--pdf",
        required=True,
        help="Path to the annual report PDF file",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output JSON file path. Defaults to pdf_sections_{period}.json next "
            f"to the PDF when the period is known, otherwise {DEFAULT_OUTPUT}"
        ),
    )
    parser.add_argument(
        "--period",
        type=_period_arg,
        default=None,
        help=(
            "Report period such as 2026H1 or 2025FY. When omitted the period is "
            "inferred from the PDF filename (e.g. 600887_2025_年报.pdf -> 2025FY)"
        ),
    )
    parser.add_argument(
        "--hints",
        default=None,
        help="Path to toc_hints.json (optional, from Phase 2A.5 TOC analysis)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress messages during extraction",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print parsed arguments and exit without processing",
    )
    parsed = parser.parse_args(args)

    # Resolve the period (explicit first, then filename inference) and default
    # the output path from it. Both are applied here so that callers -- and the
    # existing CLI tests -- see the effective values on ``args``.
    if parsed.period is None:
        # Only the basename is meaningful: a year in the parent directory
        # would otherwise win over the report label in the filename.
        parsed.period = filename_to_period(os.path.basename(str(parsed.pdf))) or ""
    try:
        parsed.output = resolve_output_path(parsed.pdf, parsed.period, parsed.output)
    except ValueError as exc:
        parser.error(str(exc))
    return parsed


def _load_hints(hints_path: Optional[str]) -> Dict[str, dict]:
    """Load TOC hints from JSON file.

    Args:
        hints_path: Path to toc_hints.json or None.

    Returns:
        Dict mapping section_id -> {"page": int, "title": str} or empty dict.
    """
    if not hints_path or not os.path.exists(hints_path):
        return {}
    try:
        with open(hints_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to load hints file '{hints_path}': {e}", file=sys.stderr)
        return {}


def run_pipeline(pdf_path: str, output_path: str, verbose: bool = False,
                 hints_path: Optional[str] = None, period: str = "") -> dict:
    """Run the full extraction pipeline.

    Args:
        pdf_path: Path to the PDF.
        output_path: Path for JSON output.
        verbose: Print progress.
        hints_path: Optional path to toc_hints.json for TOC-based page overrides.
        period: Resolved report period, recorded in the output metadata.

    Returns:
        The output dict written to JSON.

    Raises:
        FileNotFoundError: If PDF not found.
        RuntimeError: If PDF cannot be opened or is too small.
    """
    try:
        from scripts.config import validate_pdf
    except ModuleNotFoundError:
        from config import validate_pdf

    # Validate PDF
    is_valid, reason = validate_pdf(pdf_path)
    if not is_valid:
        raise RuntimeError(f"Invalid PDF: {reason}")

    # Step 1: Extract all pages
    print(f"[1/4] Extracting pages from {pdf_path}...")
    pages_text = extract_all_pages(pdf_path, verbose=verbose)
    total_pages = len(pages_text)

    if total_pages == 0:
        raise RuntimeError("PDF has no extractable pages")

    print(f"  Extracted {total_pages} pages")

    # Load TOC hints (Phase 2A.5)
    hints = _load_hints(hints_path)
    if hints and verbose:
        print(f"  Loaded TOC hints for: {list(hints.keys())}")

    # Step 2: Find section pages via keyword matching
    print("[2/4] Scanning for target sections...")
    section_pages = find_section_pages(pages_text)

    # Apply hints: override keyword-matched pages with TOC hint pages
    for sid, hint in hints.items():
        if sid in section_pages and "page" in hint:
            hint_page = hint["page"]
            if 1 <= hint_page <= total_pages:
                section_pages[sid] = [hint_page]
                if verbose:
                    print(f"  {sid}: overridden by hint → page {hint_page}")

    if verbose:
        for sid, pages in section_pages.items():
            if pages:
                print(f"  {sid}: found on pages {pages[:5]}")
            else:
                print(f"  {sid}: not found")

    # Step 3: Extract context around best matches
    print("[3/4] Extracting section context...")
    contexts = extract_section_context(pages_text, section_pages)

    # Step 4: Write output
    print(f"[4/4] Writing output to {output_path}...")
    result = write_output(contexts, pdf_path, total_pages, output_path, period=period)

    found = result["metadata"]["sections_found"]
    total = result["metadata"]["sections_total"]
    print(f"Done: {found}/{total} sections found")

    return result


def main():
    args = parse_args()

    if args.dry_run:
        print("=== Dry Run ===")
        print(f"  PDF: {args.pdf}")
        print(f"  Output: {args.output}")
        print(f"  Period: {args.period or '(unresolved)'}")
        print(f"  Hints: {args.hints}")
        print(f"  Verbose: {args.verbose}")
        return

    try:
        result = run_pipeline(args.pdf, args.output, verbose=args.verbose,
                              hints_path=args.hints, period=args.period)
        found = result["metadata"]["sections_found"]
        total = result["metadata"]["sections_total"]
        print(f"Extracted {found}/{total} sections -> {args.output}")
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
