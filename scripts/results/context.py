#!/usr/bin/env python3
"""Build bounded, module-specific context bundles for analysis Agents."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

from .evidence import UNFILLED_REASON, select_evidence, unfilled_placeholder_markers


MODULE_CONFIG: dict[str, dict[str, Any]] = {
    "business_moat": {
        "scope": ["D1", "D2"],
        "data_sections": ["1.", "2.", "3.", "3P.", "4.", "4P.", "5.", "8.", "9.", "12.", "17."],
        "pdf_sections": ["MDA", "SUB", "P3", "P4"],
        "keywords": ["护城河", "品牌", "竞争", "主营", "毛利率", "ROE", "现金流"],
        "market_evidence": ["3", "5", "9", "12"],
        "footnote_evidence": ["P3", "P4"],
    },
    "environment": {
        "scope": ["D3"],
        "data_sections": ["1.", "3.", "8.", "10.", "12.", "14."],
        "pdf_sections": ["MDA", "P13"],
        "keywords": ["行业", "周期", "监管", "政策", "需求", "竞争"],
        "market_evidence": ["3", "8", "10", "12", "14"],
        "footnote_evidence": ["P13"],
    },
    "governance": {
        "scope": ["D4"],
        "data_sections": ["1.", "7.", "10.", "13.", "15.", "16."],
        "pdf_sections": ["GOV", "MATTERS", "P2", "P13", "P4", "P6"],
        "keywords": ["审计", "治理", "关联交易", "质押", "诉讼", "承诺", "重大事项", "担保", "逾期"],
        "market_evidence": ["7", "15", "16"],
        "footnote_evidence": ["P6", "P4", "P2"],
        # 不抬高字符预算：MATTERS 的担保明细块与「担保总额（A+B）」汇总块各占一个证据
        # 槽位（见 ``_module_evidence`` 的 ``guarantee`` 槽位），缺省 24,000 下两块都能
        # 交付（实测 actual_chars=23,999）。抬高预算会破坏 AC-2.5 的「只有 period_delta
        # 显式抬高」不变量，所以这里保持缺省。
    },
    "mda_quality": {
        "scope": ["D5"],
        "data_sections": ["1.", "3.", "5.", "6.", "10.", "12.", "15.", "17."],
        "pdf_sections": ["MDA", "P13", "P3", "P6"],
        "keywords": ["收入", "利润", "现金流", "分红", "回购", "指引", "风险"],
        "market_evidence": ["3", "5", "6", "15"],
        "footnote_evidence": ["P6", "P13"],
    },
    "holding_structure": {
        "scope": ["D6"],
        "data_sections": ["1.", "4.", "4P.", "9."],
        "pdf_sections": ["SUB", "P6", "P4"],
        "keywords": ["子公司", "控股", "参股", "长期股权投资", "合并范围"],
        "market_evidence": ["4", "4P", "9"],
        "footnote_evidence": ["SUB", "P6", "P4"],
    },
    "period_delta": {
        "scope": ["D7"],
        "data_sections": ["1.", "3.", "3P.", "4.", "4P.", "5.", "6.", "12.", "15.", "17."],
        "pdf_sections": ["MDA", "MATTERS", "P13", "P3", "P6"],
        "keywords": ["收入", "利润", "毛利率", "现金流", "同比", "指引", "承诺", "变化"],
        "market_evidence": ["3", "3P", "4", "4P", "5", "6", "12", "17"],
        "footnote_evidence": ["P13", "P3", "P6"],
        "prior_analysis": ["summary", "parameters", "claims", "risks", "watchlist", "quality"],
        #: 这个模块的证据槽位比别的模块多：它既要**本期**财务（必选节 §3/§4/§5/§12 +
        #: 其余数据段），又要**上一版结论**（6 段，D7 的全部对比基准）。实测默认 12
        #: 会让必选节或对比基准二选一被饿死（REQ-006.2 AC-2.5），所以显式抬到 16。
        "max_evidence": 16,
        #: 字符预算同理要抬：16 条证据的 JSON 元数据（evidence_id/locator/hash）就占掉
        #: 十几 k，默认 24,000 下必选节只能分到 97~170 字（实测 §5 现金流量表只剩表头）。
        #: 32,000 下 §3 保得住 5 条必选行、§5/§12 各约 450~520 字、16 条证据全在
        #: （实测 `actual_chars=31,287`）。
        "max_chars": 32000,
    },
}


def _parse_markdown_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_key = "_preamble"
    current_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            sections[current_key] = "".join(current_lines)
            current_key = line[3:].strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    sections[current_key] = "".join(current_lines)
    return sections


def _find_section(sections: dict[str, str], prefix: str) -> tuple[str, str] | None:
    for title, content in sections.items():
        if title.startswith(prefix):
            return title, content
    return None


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    for separator in ("\n", "。", "；", "."):
        position = cut.rfind(separator)
        if position > max_chars // 2:
            return cut[: position + 1], True
    return cut, True


#: 利润表里「不能因为预算被截掉」的必选行（REQ-006.2 AC-2.5 / 发现 F14：旧实现从尾部
#: 截断，把「归母净利润」砍掉，D5 的 net_profit_mm 只能为 null）。
REQUIRED_INCOME_ROWS: tuple[str, ...] = (
    "营业收入", "营业成本", "财务费用", "净利润", "归母净利润",
)

#: **必选节**（REQ-006.2 AC-2.5）：利润表（含必选行）、资产负债表、现金流量表、
#: 关键财务指标。它们在 max_evidence 分配里**先于 prior_analysis** 拿槽位，在字符预算里
#: 也有下限——否则「本期财务事实」会被上一版结论与 PDF 正文挤空。
#: 实测（run `20260925T091012981574Z`）：period_delta 的 `market_data` 8 个槽位全 `omitted`、
#: §3 只剩 168 字（表头两行），D7 的 revenue_yoy / net_profit_yoy / gross_margin_change /
#: ocf_to_profit 只能为 null。
REQUIRED_MARKET_SECTIONS: tuple[str, ...] = ("3", "4", "5", "12")

#: 含必选行的段落至少要分到这个字符数，`_truncate_keeping_rows` 才装得下整块必选行
#: （实测 §3 的 5 条必选行 + 表头共 725 字，故下限取 900）。
REQUIRED_INCOME_SECTION_MIN_CHARS = 900

#: 识别「这个段落里有必选行」：只认**行首**的必选行（`| 营业收入 |` / `- 营业收入`）。
#: 朴素子串匹配会把资产负债表段落也误判成利润表（它也有「净利润」这类行）。
REQUIRED_INCOME_ROW_RE = re.compile(
    r"^[\s|>*\-]*(" + "|".join(re.escape(row) for row in REQUIRED_INCOME_ROWS) + r")\s*[\s|:：]"
)

#: 模块没有显式声明证据槽位预算时的缺省值。
DEFAULT_MAX_EVIDENCE = 12
#: 模块没有显式声明字符预算时的缺省值。
DEFAULT_MAX_CHARS = 24000
#: 证据引文的最小渲染额度：低于这个值的额度直接放弃（``_render`` 的 160 字门槛），
#: 所以分配时要先按它给每个「第一块」留位。
MIN_QUOTE_BUDGET = 160

#: 同一段落最多给模块几条索引摘录（F22：只给 1 条会让索引里其余可用摘录引不到；
#: 超过这个数就会挤占别的段落，预算兜底由 evidence 池的均分截断负责）。
MAX_EVIDENCE_PER_SECTION = 2

#: 段落的预算份额低于这个值时不再强保必选行（否则极端紧的 max_chars 下 bundle 永远装不进）。
REQUIRED_ROWS_MIN_BUDGET = 300

#: bundle 里每个输入状态对**本模块结论**的含义（F20：missing/omitted/truncated 三态
#: 被模块混用，出现过「不存在」式错误表述）。
COVERAGE_STATE_MEANINGS: dict[str, str] = {
    "full": "完整进入 bundle。",
    "truncated": "被截断：尾部不在 bundle（可用 evidence_id 取原文）。",
    "omitted": "索引里有、预算没装下（不是数据不存在）。",
    "missing": "索引里没有：没采到或没接进 inputs。",
    "unavailable": "Agent 专属占位段，本 run 不填（不是缺陷）。",
}


def _truncate_keeping_rows(
    text: str, max_chars: int, required: Iterable[str],
) -> tuple[str, bool]:
    """截断时**先保必选行**，再用剩余预算按原顺序填其余内容。

    2026-09-25 实跑（F14）：按预算从尾部截断会把「归母净利润」行砍掉，D5 只能写 null。
    这里改成：markdown 表头 + 命中 ``required`` 的行无条件保留，其余行填到预算用完；
    输出仍按原文顺序，读起来还是一张表。
    """

    if max_chars <= 0 or len(text) <= max_chars or not required:
        return _truncate(text, max_chars)
    lines = text.splitlines(keepends=True)
    selected: set[int] = set()
    used = 0
    header_end = 1 if lines else 0
    for position, line in enumerate(lines[:4]):
        stripped = line.strip()
        if stripped and set(stripped) <= set("|-: "):
            header_end = position + 1
            break
    for position in range(header_end):
        selected.add(position)
        used += len(lines[position])
    for position, line in enumerate(lines):
        if any(term in line for term in required):
            if position not in selected:
                selected.add(position)
                used += len(lines[position])
    for position, line in enumerate(lines):
        if position in selected:
            continue
        if used + len(line) > max_chars:
            break
        selected.add(position)
        used += len(line)
    return "".join(lines[position] for position in sorted(selected)), True


def _estimate_tokens(text: str) -> int:
    # Conservative estimate for mixed Chinese/Latin text. Exact token counts
    # depend on the selected model and are recorded separately by the runner.
    return max(1, math.ceil(len(text) / 2)) if text else 0



def _load_pdf_sections(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        key: value
        for key, value in payload.items()
        if key != "metadata" and isinstance(value, str) and value.strip()
    }


def _fair_limits(lengths: list[int], budget: int) -> list[int]:
    """Share a pool evenly, redistributing unused shares from short sections."""
    limits = [0] * len(lengths)
    pending = list(range(len(lengths)))
    while pending and budget > 0:
        share = max(1, budget // len(pending))
        for position in pending:
            amount = min(share, lengths[position] - limits[position], budget)
            limits[position] += amount
            budget -= amount
        pending = [position for position in pending if limits[position] < lengths[position]]
    return limits


def _minimum_then_fair(lengths: list[int], budget: int, minimum: int) -> list[int]:
    """先给每一项留 ``minimum`` 字（或它自身的长度），再把剩余预算均分。

    与 :func:`_fair_limits` 的区别：这是「保证每一项都能被渲染出来」的分配——`_render`
    对低于 ``MIN_QUOTE_BUDGET`` 的额度直接放弃，均分到边角时最后一项会归零。池子连
    最小额度都付不起时按比例缩，仍可能放弃部分项（此时「装得进」优先）。
    """

    if not lengths:
        return []
    limits = [0] * len(lengths)
    pending = list(range(len(lengths)))
    for position, length in enumerate(lengths):
        take = min(minimum, length, budget)
        limits[position] = take
        budget -= take
    pending = [position for position in pending if limits[position] < lengths[position]]
    while pending and budget > 0:
        share = max(1, budget // len(pending))
        for position in pending:
            amount = min(share, lengths[position] - limits[position], budget)
            limits[position] += amount
            budget -= amount
        pending = [position for position in pending if limits[position] < lengths[position]]
    return limits


def _raise_income_section_limits(
    sections: list[tuple[str, str]], limits: list[int],
) -> list[int]:
    """把「含必选行的段落」的字符下限抬到 ``REQUIRED_INCOME_SECTION_MIN_CHARS``。

    多出来的部分从**有余量**的段落里扣（每次扣掉超出其一半份额的部分，从最大者开始），
    保证池子总量不变、bundle 不会超 ``max_chars``；池子里确实没有余量时保持原样，
    仍走普通截断（AC-2.5 的必选行保底在 `max_chars` 太小时只能让位给「装得进」）。
    """

    if not limits:
        return limits
    positions = [
        index
        for index, (_, text) in enumerate(sections)
        if any(REQUIRED_INCOME_ROW_RE.match(line) for line in text.splitlines())
    ]
    if not positions:
        return limits
    ceilings = {
        index: min(len(sections[index][1]), REQUIRED_INCOME_SECTION_MIN_CHARS)
        for index in positions
    }
    shortfall = sum(max(0, ceilings[index] - limits[index]) for index in positions)
    if shortfall <= 0:
        return limits
    donors = sorted(
        (index for index in range(len(limits)) if index not in positions),
        key=lambda index: -limits[index],
    )
    # 每个段落最多让出「自己份额的一半」，避免把它们压到连表头都不剩。
    available = sum(limits[index] - limits[index] // 2 for index in donors)
    if available < shortfall:
        # 池子没有余量：保持原样，交给普通截断（装得进优先于装得全）。
        return limits
    for index in donors:
        if shortfall <= 0:
            break
        give = min(limits[index] - limits[index] // 2, shortfall)
        limits[index] -= give
        shortfall -= give
    for index in positions:
        limits[index] = max(limits[index], ceilings[index])
    return limits


#: PDF 章节的证据优先级：排在前面的先占 `max_evidence` 槽位。MATTERS 承载重大担保 /
#: 诉讼（AC-2.4 的硬判据）、MDA 是经营讨论，二者优先；其余按配置顺序。
PDF_SECTION_PRIORITY: tuple[str, ...] = ("MATTERS", "MDA")


def _prioritized_pdf_sections(sections: Iterable[str]) -> list[str]:
    """按 :data:`PDF_SECTION_PRIORITY` 排序 PDF 章节，未列出的保持在配置里的相对顺序。"""

    order = {name: position for position, name in enumerate(PDF_SECTION_PRIORITY)}
    return sorted(sections, key=lambda name: order.get(name, len(order)))


def _module_evidence(
    index: dict[str, Any], config: dict[str, Any], limit: int,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """按「先保结构、再给 prior_analysis」的顺序挑选证据槽位。

    REQ-006.2 AC-2.5：`max_evidence` 是**两遍分配**——
    第一遍先给必选节（利润表 / 资产负债表 / 现金流量表 / 关键财务指标）与
    `prior_analysis` 各一条，这两类都不能被别的组饿死（前者是本期财务事实，
    后者是增量对比基准）；剩下的槽位再按组轮流补给其余来源与多余的块。
    实测（run `20260925T091012981574Z`）：旧顺序「prior_analysis 先拿满」让
    `period_delta` 的 8 个 `market_data` 槽位全 `omitted`，D7 的四个必填参数只能为 null。

    段落里有多块可用摘录时不能只给 1 条就切断其余（发现 F22：MDA 有 8 块、
    §17 有 4 块，D2 的市场份额/Capex 证据在索引里却引不到）。
    """

    # Agent 专属占位段落（§7/§8/§10/§13.2）不进索引，这里的槽位必须显示为
    # ``unavailable``（按策略本 run 不填）而不是 ``missing``（看起来像数据丢了）
    # —— REQ-006.2 AC-2.3。
    unfilled = {
        (item.get("source_id"), item.get("section"))
        for item in index.get("unfilled_sections", []) or []
        if isinstance(item, dict)
    }
    groups: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add_group(source: str, section: str) -> None:
        if (source, section) not in seen:
            seen.add((source, section))
            groups.append((source, section))

    for source, sections in (
        ("market_data", config["market_evidence"]),
        # PDF 章节按**证据优先级**排：MATTERS（重大担保/诉讼，AC-2.4 的硬判据）与 MDA
        # 先占槽位，再是其余章节。槽位不够时被挤掉的应该是 P2/P4 这类次要章节。
        ("pdf_sections", _prioritized_pdf_sections(config["pdf_sections"])),
        ("pdf_footnotes", config["footnote_evidence"]),
        ("prior_analysis", config.get("prior_analysis", [])),
    ):
        for section in sections:
            add_group(source, section)

    # 必选节按编号前缀匹配：market_evidence 里的 "3." / "3" 都算利润表。
    required_sections = set(REQUIRED_MARKET_SECTIONS)

    def is_required(source: str, section: str) -> bool:
        prefix = str(section).split(".")[0].strip()
        return source == "market_data" and prefix in required_sections

    required_leave_one_out = {
        (source, section) for source, section in groups if is_required(source, section)
    }

    #: 每个「组」排好序的候选块（最多 MAX_EVIDENCE_PER_SECTION 条）。
    ranked_by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    #: 担保类「汇总块」的专属槽位键：重大担保表（列值一一对应）与紧随其后的
    #: 「担保总额（A+B）/ 占净资产比例 / 违规担保」汇总段在**相邻两个块**里，
    #: 只能靠各自占一个槽位才都进得了 bundle（AC-2.4 的「担保逾期可判定」要两份都在）。
    guarantee_keys: dict[tuple[str, str], tuple[str, str]] = {}
    for source, section in groups:
        candidates = [
            item for item in index.get("entries", [])
            if item.get("source_id") == source and item.get("section") == section
        ]
        # Prior-run conclusions must be quoted verbatim; keyword ranking could
        # silently drop the parameter block the delta agent is comparing against.
        if source == "prior_analysis":
            ranked = candidates[:1]
        else:
            ranked = select_evidence(
                {"entries": candidates},
                keywords=config["keywords"],
                limit=MAX_EVIDENCE_PER_SECTION,
            )
        # Concrete guarantees take priority over accounting-policy references.
        if section in {"MATTERS", "P6"}:
            targeted = select_evidence(
                {"entries": candidates},
                keywords=["担保总额", "担保逾期", "逾期金额", "对外担保"],
                limit=MAX_EVIDENCE_PER_SECTION,
            )
            if len(targeted) > 1:
                # 第 2 条（通常是汇总段）单独占一个槽位，避免被均分预算挤掉。
                summary_key = f"{source}:{section}:guarantee"
                ranked_by_group[summary_key] = [targeted[1]]
                guarantee_keys[summary_key] = (source, section)
                targeted = targeted[:1]
            ranked = targeted or ranked
        ranked_by_group[(source, section)] = (ranked or candidates)[:MAX_EVIDENCE_PER_SECTION]
    # 担保明细块与汇总块都放进「必选档」：两块各自占一个第一遍槽位，从而都拿到
    # 最小引文额度（实测：只占一个槽位时另一块会被降级成「额外块」，额度 58 字被丢弃）。
    for summary_key in guarantee_keys:
        groups.append(summary_key)
        required_leave_one_out.add(summary_key)

    coverage: dict[str, str] = {}
    for key, choice in ranked_by_group.items():
        source, section = guarantee_keys.get(key, key)
        coverage_key = f"{source}:{section}"
        # 汇总块与主块共用一个 (source, section) 覆盖键：只要有一条入选就算有证据。
        if coverage_key in coverage and choice:
            continue
        coverage[coverage_key] = "missing" if not choice else "omitted"
        if not choice and (source, section) in unfilled:
            coverage[coverage_key] = "unavailable"

    # 第一遍：必选节 + prior_analysis 各一条。必选节先拿，保证「本期财务事实」优先于
    # 上一版结论；prior_analysis 紧随其后，保证对比基准不被本轮数据包挤掉。
    selected: list[dict[str, Any]] = []
    chosen_from: dict[tuple[str, str], int] = {}
    # 第一遍按**轮转**分三档，每轮每档最多拿 1 条：① 必选节（本期财务事实）
    # ② prior_analysis（增量对比基准）③ 其余来源（PDF 正文 / 附注 / 非必选数据段）。
    # 轮转而不是「一档拿满再下一档」，是为了在两件事之间取平衡：默认 max_evidence=12 时
    # 必选节必须先拿到（旧行为是 prior_analysis 先拿 6 条，market_data 8 个槽位全 `omitted`，
    # D7 四个必填参数只能为 null）；极紧的 max_evidence 下对比基准也不能整档消失。
    priority_tiers = [
        [key for key in groups if key in required_leave_one_out],
        [key for key in groups if key[0] == "prior_analysis"],
        [key for key in groups if key not in required_leave_one_out and key[0] != "prior_analysis"],
    ]
    while len(selected) < limit:
        progressed = False
        for tier in priority_tiers:
            if len(selected) >= limit:
                break
            for key in tier:
                choice = ranked_by_group[key]
                if chosen_from.get(key, 0) >= min(1, len(choice)):
                    continue
                # 记下槽位：担保汇总块与 MATTERS 明细块同属一个段落，但必须各自按
                # 「第一块」拿预算（AC-2.4）。
                selected.append(dict(choice[0], _slot=key))
                chosen_from[key] = 1
                source, section = guarantee_keys.get(key, key)
                coverage[f"{source}:{section}"] = choice[0]["evidence_id"]
                progressed = True
                break
        if not progressed:
            break

    # 第二遍：剩余槽位按组轮流分（每轮每组补 1 条），直到槽位用尽。
    while len(selected) < limit:
        progressed = False
        for key in groups:
            if len(selected) >= limit:
                break
            choice = ranked_by_group[key]
            position = chosen_from.get(key, 0)
            if position >= len(choice):
                continue
            selected.append(dict(choice[position], _slot=key))
            chosen_from[key] = position + 1
            if position == 0:  # 该组刚拿到第一条：覆盖状态要指向它
                source, section = guarantee_keys.get(key, key)
                coverage[f"{source}:{section}"] = choice[0]["evidence_id"]
            progressed = True
        if not progressed:
            break
    return selected, coverage


def build_module_context(
    module: str,
    *,
    data_pack_path: str | Path | None = None,
    pdf_sections_path: str | Path | None = None,
    evidence_index_path: str | Path | None = None,
    max_chars: int | None = None,
    max_evidence: int | None = None,
    run_id: str | None = None,
    subject: dict[str, Any] | None = None,
    input_digest: str | None = None,
    routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a bounded context bundle for one analysis module.

    ``max_chars`` / ``max_evidence`` 传 ``None``（默认）时取模块自己的预算
    （``MODULE_CONFIG`` 的 ``max_chars`` / ``max_evidence``，缺省 24,000 / 12）：
    period_delta 需要更多槽位与字符才能同时装下必选节、完整的 ``prior_analysis``
    与 PDF 正文（REQ-006.2 AC-2.5）。
    """

    if module not in MODULE_CONFIG:
        raise ValueError(f"Unknown module: {module}. Expected one of {sorted(MODULE_CONFIG)}")

    if max_chars is None:
        max_chars = int(MODULE_CONFIG[module].get("max_chars", DEFAULT_MAX_CHARS))
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    if max_evidence is None:
        max_evidence = int(MODULE_CONFIG[module].get("max_evidence", DEFAULT_MAX_EVIDENCE))
    if max_evidence < 0:
        raise ValueError("max_evidence must be non-negative")
    config = MODULE_CONFIG[module]
    inputs: list[str] = []
    market: list[tuple[str, str]] = []
    #: 数据包里「只含占位符」的段落（§8/§10 等）：按 AC-2.3 的策略本 run 不填，
    #: 既不进证据槽位，也不作为原始上下文塞给模块。
    agent_only_sections: list[dict[str, Any]] = []
    if data_pack_path:
        data_path = Path(data_pack_path)
        inputs.append(str(data_path))
        if data_path.exists():
            parsed = _parse_markdown_sections(data_path.read_text(encoding="utf-8"))
            for prefix in config["data_sections"]:
                match = _find_section(parsed, prefix)
                if not match:
                    continue
                title, section_text = match
                if unfilled_placeholder_markers(section_text):
                    agent_only_sections.append({"section": title, "reason": UNFILLED_REASON})
                    continue
                market.append(match)

    pdf_path = Path(pdf_sections_path) if pdf_sections_path else None
    if pdf_path:
        inputs.append(str(pdf_path))
    parsed_pdf = _load_pdf_sections(pdf_path)
    pdf = [(key, parsed_pdf[key]) for key in config["pdf_sections"] if key in parsed_pdf]
    index: dict[str, Any] = {"entries": []}
    if evidence_index_path:
        evidence_path = Path(evidence_index_path)
        inputs.append(str(evidence_path))
        if evidence_path.exists():
            index = json.loads(evidence_path.read_text(encoding="utf-8"))
    selected_evidence, coverage = _module_evidence(index, config, max_evidence) if evidence_index_path else ([], {})
    unfilled_by_key = {
        (item.get("source_id"), item.get("section")): item
        for item in index.get("unfilled_sections", []) or []
        if isinstance(item, dict)
    }
    # 证据源的期次（REQ-006.2 发现 F29）：附注源与 run 的 primary_period 不同期时，
    # 模块必须能看见「这段证据属于哪一期」，否则会把上年报附注当同期数据引用。
    # 只登记本模块真正会用到的源；索引里没有期次信息时键不出现，bundle 形状对旧 run 保持不变。
    relevant_sources = {"market_data", "pdf_sections", "pdf_footnotes"}
    if config.get("prior_analysis"):
        relevant_sources.add("prior_analysis")
    # 年报 PDF 的 source_id 是 ``annual_report:{stem}``，其集合随 run 变化，
    # 所以按前缀补进「本模块会看到的源」。
    relevant_sources |= {
        item["source_id"]
        for item in index.get("sources", []) or []
        if isinstance(item, dict)
        and str(item.get("source_id", "")).startswith("annual_report:")
    }
    source_periods: dict[str, Any] = {
        item["source_id"]: (
            {"period": item["period"], "basis": item["period_basis"]}
            if item.get("period_basis")
            else item["period"]
        )
        for item in index.get("sources", []) or []
        if isinstance(item, dict)
        and item.get("source_id") in relevant_sources
        and item.get("period")
    }
    period_mismatches = [
        item
        for item in index.get("period_mismatches", []) or []
        if isinstance(item, dict) and item.get("source_id") in relevant_sources
    ]
    unavailable_inputs = [
        {
            "source_id": source,
            "section": section,
            "reason": unfilled_by_key[(source, section)].get("reason", ""),
        }
        for source, sections in (
            ("market_data", config["market_evidence"]),
            ("pdf_sections", config["pdf_sections"]),
            ("pdf_footnotes", config["footnote_evidence"]),
        )
        for section in sections
        if (source, section) in unfilled_by_key
    ]
    unavailable_inputs.extend(
        {
            "source_id": "data_pack",
            "section": item["section"],
            "reason": item["reason"],
        }
        for item in agent_only_sections
    )
    content_budget = int(max_chars * 0.75)
    include_extras = True
    while True:
        # Independent pools prevent a long financial table from starving PDF
        # sections. Rebuild all pools after measuring JSON, never slice the tail.
        weights = [25 if market else 0, 35 if pdf else 0, 40 if selected_evidence else 0]
        pools = [content_budget * weight // max(1, sum(weights)) for weight in weights]
        blocks: list[str] = []
        section_metadata: list[list[dict[str, Any]]] = [[], []]
        truncated = False
        #: 每个输入段落在 context_text 里**实际展示**的字符数；同一 evidence id 的
        #: quote 必须落在这个范围内（REQ-006.2 AC-2.5 / 发现 F13：context_text 与
        #: evidence[].quote 覆盖窗口不一致，模块拿到的引文无法在上下文里核对）。
        shown_lengths: dict[tuple[str, str], int] = {}
        for sections, pool, metadata, label, key in (
            (market, pools[0], section_metadata[0], "Market data:", "title"),
            (pdf, pools[1], section_metadata[1], "PDF", "section"),
        ):
            limits = _fair_limits([len(text) for _, text in sections], pool)
            # 含必选行的段落（利润表）不能只分到一个装不下必选行的份额：先按
            # REQUIRED_INCOME_SECTION_MIN_CHARS 抬起下限，多出来的部分从同一池里
            # 余量足够的段落扣（AC-2.5）。
            if label.startswith("Market"):
                limits = _raise_income_section_limits(sections, limits)
            for (title, text), limit in zip(sections, limits):
                # 必选行（利润表）先保，再按预算填其余（F14）。极端紧的预算下
                # （段落份额 < 300 字）连必选行本身都放不下，此时退回普通截断，
                # 否则 bundle 永远装不进 max_chars。
                required = REQUIRED_INCOME_ROWS if limit >= REQUIRED_ROWS_MIN_BUDGET else ()
                excerpt, cut = _truncate_keeping_rows(text, limit, required)
                state = "omitted" if not excerpt else ("truncated" if cut else "full")
                metadata.append(
                    {key: title, "selected_chars": len(excerpt), "truncated": cut, "state": state}
                )
                if excerpt:
                    blocks.append(f"[{label} {title}]\n{excerpt}")
                    source_id = "market_data" if label.startswith("Market") else "pdf_sections"
                    section_key = title.split(".")[0].strip() if source_id == "market_data" else title
                    shown_lengths[(source_id, section_key)] = len(excerpt)
                truncated |= cut

        evidence = []
        retained_coverage = dict(coverage)
        truncated_extra: list[str] = []

        def _render(item: dict[str, Any], limit: int) -> dict[str, Any] | None:
            """把一条索引条目渲染成 bundle 里的 evidence（预算不够返回 None）。"""

            if limit < min(MIN_QUOTE_BUDGET, len(item["quote"])):
                return None
            quote = item["quote"]
            cap = shown_lengths.get((item["source_id"], item["section"]))
            if cap is not None and int(item.get("chunk_number", 1) or 1) == 1:
                # 这一段已经作为 context_text 展示了前 cap 个字符：引文必须是它的子段，
                # 否则模块拿到引文却无法在上下文里核对（F13）。
                excerpt, _ = _truncate(quote[:cap], limit)
            else:
                terms = ["担保总额", "担保逾期", "逾期金额", "对外担保"] + config["keywords"]
                positions = [quote.find(term) for term in terms if term in quote]
                start = max(0, min(positions[0] - limit // 4, len(quote) - limit)) if positions else 0
                excerpt, _ = _truncate(quote[start:], limit)
            return {
                **{key: item[key] for key in ("evidence_id", "source_id", "locator", "content_hash") if key in item},
                "quote": excerpt,
            }

        # 两遍分配：每个段落的**第一块**先按均分拿预算（保证大表不饿死别的段落），
        # 剩余预算再给同一段的后续块（REQ-006.2 AC-2.5 / 发现 F22）。
        # 分组键是**槽位**而不是真实段落：``_module_evidence`` 给重大担保的汇总块单独
        # 发了一个槽位（``pdf_sections:MATTERS:guarantee``），它和明细块同属
        # ``(pdf_sections, MATTERS)``；按真实段落分组会把汇总块降级成「第 2+ 块」，
        # 预算不够时被 ``_render`` 整条丢掉（AC-2.4 要明细与汇总两块都在）。
        grouped: dict[Any, list[dict[str, Any]]] = {}
        for item in selected_evidence:
            slot = item.get("_slot") or (item["source_id"], item["section"])
            grouped.setdefault(slot, []).append(item)
        first_items = [items[0] for items in grouped.values()]
        # 每个「第一块」先按最小额度留位，再均分剩余预算：否则池子被前面的块吃掉后，
        # 排在最后的第一块（实测：重大担保明细表）会连 `_render` 的 160 字门槛都够不到而
        # 被整条丢掉（AC-2.4 要担保明细与汇总两块都在）。
        first_limits = _minimum_then_fair(
            [len(item["quote"]) for item in first_items], pools[2], MIN_QUOTE_BUDGET,
        )
        spent = 0
        #: 真正渲染出来的块 -> 段落覆盖键。``evidence_coverage`` 必须指向**交付了**的
        #: 证据：担保明细与汇总同属 ``pdf_sections:MATTERS``，``_module_evidence`` 选中的
        #: 那条可能因为预算没渲染出来（实测 coverage 指向 003、交付的却是 004）。
        rendered_coverage: dict[str, str] = {}
        failed_coverage: list[str] = []
        for item, limit in zip(first_items, first_limits):
            rendered = _render(item, limit)
            coverage_key = f"{item['source_id']}:{item['section']}"
            if rendered is None:
                failed_coverage.append(coverage_key)
                truncated = True
                continue
            evidence.append(rendered)
            rendered_coverage.setdefault(coverage_key, rendered["evidence_id"])
            spent += len(rendered["quote"])
            truncated |= rendered["quote"] != item["quote"]
        extra_items = (
            [item for items in grouped.values() for item in items[1:]] if include_extras else []
        )
        extra_limits = _fair_limits([len(item["quote"]) for item in extra_items], max(0, pools[2] - spent))
        for item, limit in zip(extra_items, extra_limits):
            rendered = _render(item, limit)
            key = f"{item['source_id']}:{item['section']}"
            if rendered is None:
                truncated_extra.append(key)
                truncated = True
                continue
            evidence.append(rendered)
            rendered_coverage.setdefault(key, rendered["evidence_id"])
            truncated |= rendered["quote"] != item["quote"]
        # 段落覆盖状态收口：有块渲染出来就指向它；一条都没渲染出来才算 ``omitted``。
        for coverage_key in failed_coverage:
            if coverage_key not in rendered_coverage:
                retained_coverage[coverage_key] = "omitted"
        retained_coverage.update(rendered_coverage)
        context_text = "\n\n".join(blocks)
        # 每条交付给模块的引文都必须能在同一 bundle 的 context_text 中逐字核对。
        # 附注源、上一轮结论和同一段落的后续窗口不属于上面的原始段落，因此把它们
        # 作为带来源标记的证据摘录追加进去，而不是交付一个只能回索引查证的悬空引用。
        missing_context_blocks: list[str] = []
        for entry in evidence:
            quote = entry.get("quote", "")
            if quote and quote not in context_text:
                missing_context_blocks.append(
                    f"[Evidence {entry['evidence_id']}]\n{quote}"
                )
        if missing_context_blocks:
            context_text = "\n\n".join([context_text, *missing_context_blocks])
        # F13 的可判定形式：每条引文是否能在 context_text 里逐字核对。
        quotes_not_in_context: list[str] = []
        for entry in evidence:
            in_context = bool(entry["quote"]) and entry["quote"] in context_text
            entry["in_context"] = in_context
            if not in_context:
                quotes_not_in_context.append(entry["evidence_id"])
        bundle = {
            "schema": "investment.context_bundle",
            "schema_version": "1.0",
            "module": module,
            "scope": config["scope"],
            "inputs": inputs,
            "data_sections": section_metadata[0],
            "pdf_sections": section_metadata[1],
            "evidence": evidence,
            "coverage_states": {
                state: COVERAGE_STATE_MEANINGS[state]
                for state in (
                    {item.get("state") for item in [*section_metadata[0], *section_metadata[1]]}
                    | set(retained_coverage.values())
                )
                if state in COVERAGE_STATE_MEANINGS
            },
            "section_states": [
                {
                    "section": item.get("title") or item.get("section"),
                    "state": item.get("state"),
                    "selected_chars": item.get("selected_chars"),
                }
                for item in [*section_metadata[0], *section_metadata[1]]
                if item.get("state") != "full"
            ],
            "unavailable_inputs": unavailable_inputs,
            **({"source_periods": source_periods} if source_periods else {}),
            "context_text": context_text,
            "budget": {
                "max_chars": max_chars,
                "actual_chars": 0,
                "estimated_context_tokens": _estimate_tokens(context_text + "".join(item["quote"] for item in evidence)),
                "truncated": truncated or "omitted" in retained_coverage.values(),
            },
            "selection": {
                "data_section_prefixes": config["data_sections"],
                "pdf_section_ids": config["pdf_sections"],
                "keywords": config["keywords"],
                "evidence_coverage": retained_coverage,
                "quotes_not_in_context": quotes_not_in_context,
                # 同一段的第 2+ 块里没装进预算的那些（F22 的可判定披露）。
                "evidence_extra_omitted": sorted(set(truncated_extra)),
                "missing_pdf_sections": [key for key in config["pdf_sections"] if key not in parsed_pdf],
                # 期次不匹配的**可判定披露**（F29）：模块不能只看到「有附注证据」，
                # 还得看到「这份附注证据属于哪一期、和 primary_period 不同期」。
                **({"period_mismatches": period_mismatches} if period_mismatches else {}),
                **(
                    {
                        "prior_analysis_sections": config["prior_analysis"],
                        # ``omitted`` means the prior conclusion was available in
                        # the index but did not fit the budget: that is a visible
                        # gap too, not a silent success.
                        "missing_prior_analysis": [
                            key
                            for key in config["prior_analysis"]
                            if retained_coverage.get(f"prior_analysis:{key}") == "missing"
                        ],
                        "omitted_prior_analysis": [
                            key
                            for key in config["prior_analysis"]
                            if retained_coverage.get(f"prior_analysis:{key}") == "omitted"
                        ],
                    }
                    if config.get("prior_analysis")
                    else {}
                ),
            },
        }
        if run_id is not None:
            bundle["run"] = {"run_id": run_id, "status": "prepared"}
        if subject is not None:
            bundle["subject"] = subject
        if input_digest is not None:
            bundle["input_digest"] = input_digest
        if routing is not None:
            bundle["routing"] = routing
        while True:
            actual_chars = len(json.dumps(bundle, ensure_ascii=False, indent=2)) + 1
            if bundle["budget"]["actual_chars"] == actual_chars:
                break
            bundle["budget"]["actual_chars"] = actual_chars
        if actual_chars <= max_chars:
            return bundle
        if include_extras and extra_items:
            # 预算不够时**先牺牲同一段落的第 2+ 块**，再考虑压缩第一块：否则
            # 第二块摘录会把别段落的第一块挤出预算（「大表不能饿死别的段落」）。
            include_extras = False
            truncated_extra.extend(
                f"{item['source_id']}:{item['section']}" for item in extra_items
            )
            continue
        if content_budget == 0:
            raise ValueError(f"Unable to fit {module} context bundle within {max_chars} characters")
        content_budget = max(0, content_budget - max(1, actual_chars - max_chars))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a bounded context bundle for one module")
    parser.add_argument("--module", required=True, choices=sorted(MODULE_CONFIG))
    parser.add_argument("--data-pack")
    parser.add_argument("--pdf-sections")
    parser.add_argument("--evidence-index")
    # 不设默认值：None 表示「用该模块自己的预算」（MODULE_CONFIG，见 AC-2.5）。
    parser.add_argument("--max-chars", type=int, default=None)
    parser.add_argument("--max-evidence", type=int, default=None)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    bundle = build_module_context(
        args.module,
        data_pack_path=args.data_pack,
        pdf_sections_path=args.pdf_sections,
        evidence_index_path=args.evidence_index,
        max_chars=args.max_chars,
        max_evidence=args.max_evidence,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Context written: {args.output} "
        f"({bundle['budget']['actual_chars']}/{bundle['budget']['max_chars']} chars, "
        f"estimated {bundle['budget']['estimated_context_tokens']} context tokens)"
    )


if __name__ == "__main__":
    main()
