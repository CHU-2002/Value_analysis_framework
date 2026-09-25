#!/usr/bin/env python3
"""Build bounded, module-specific context bundles for analysis Agents."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

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


def _module_evidence(
    index: dict[str, Any], config: dict[str, Any], limit: int,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    # Prior-run conclusions are the comparison baseline for the incremental
    # delta module. They are selected first: a wide data pack otherwise fills
    # the whole evidence limit and starves them completely.
    groups: list[tuple[str, str]] = [
        ("prior_analysis", section) for section in config.get("prior_analysis", [])
    ]
    groups.extend(
        (source, section)
        for source, sections in (
            ("pdf_sections", config["pdf_sections"]),
            ("pdf_footnotes", config["footnote_evidence"]),
            ("market_data", config["market_evidence"]),
        )
        for section in sections
    )
    selected: list[dict[str, Any]] = []
    coverage = {}
    # Agent 专属占位段落（§7/§8/§10/§13.2）不进索引，这里的槽位必须显示为
    # ``unavailable``（按策略本 run 不填）而不是 ``missing``（看起来像数据丢了）
    # —— REQ-006.2 AC-2.3。
    unfilled = {
        (item.get("source_id"), item.get("section"))
        for item in index.get("unfilled_sections", []) or []
        if isinstance(item, dict)
    }
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
            ranked = select_evidence({"entries": candidates}, keywords=config["keywords"], limit=1)
        # Concrete guarantees take priority over accounting-policy references.
        if section in {"MATTERS", "P6"}:
            targeted = select_evidence(
                {"entries": candidates}, keywords=["担保总额", "担保逾期", "逾期金额", "对外担保"], limit=1
            )
            ranked = targeted or ranked
        choice = (ranked or candidates)[:1]
        key = f"{source}:{section}"
        coverage[key] = "missing" if not choice else "omitted"
        if not choice and (source, section) in unfilled:
            coverage[key] = "unavailable"
        if choice and len(selected) < limit:
            selected.extend(choice)
            coverage[key] = choice[0]["evidence_id"]
    return selected, coverage


def build_module_context(
    module: str,
    *,
    data_pack_path: str | Path | None = None,
    pdf_sections_path: str | Path | None = None,
    evidence_index_path: str | Path | None = None,
    max_chars: int = 24000,
    max_evidence: int = 12,
    run_id: str | None = None,
    subject: dict[str, Any] | None = None,
    input_digest: str | None = None,
    routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a bounded context bundle for one analysis module."""

    if module not in MODULE_CONFIG:
        raise ValueError(f"Unknown module: {module}. Expected one of {sorted(MODULE_CONFIG)}")
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

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
    while True:
        # Independent pools prevent a long financial table from starving PDF
        # sections. Rebuild all pools after measuring JSON, never slice the tail.
        weights = [25 if market else 0, 35 if pdf else 0, 40 if selected_evidence else 0]
        pools = [content_budget * weight // max(1, sum(weights)) for weight in weights]
        blocks: list[str] = []
        section_metadata: list[list[dict[str, Any]]] = [[], []]
        truncated = False
        for sections, pool, metadata, label, key in (
            (market, pools[0], section_metadata[0], "Market data:", "title"),
            (pdf, pools[1], section_metadata[1], "PDF", "section"),
        ):
            limits = _fair_limits([len(text) for _, text in sections], pool)
            for (title, text), limit in zip(sections, limits):
                excerpt, cut = _truncate(text, limit)
                metadata.append({key: title, "selected_chars": len(excerpt), "truncated": cut})
                if excerpt:
                    blocks.append(f"[{label} {title}]\n{excerpt}")
                truncated |= cut

        evidence = []
        limits = _fair_limits([len(item["quote"]) for item in selected_evidence], pools[2])
        retained_coverage = dict(coverage)
        for item, limit in zip(selected_evidence, limits):
            if limit < min(160, len(item["quote"])):
                retained_coverage[f"{item['source_id']}:{item['section']}"] = "omitted"
                truncated = True
                continue
            quote = item["quote"]
            terms = ["担保总额", "担保逾期", "逾期金额", "对外担保"] + config["keywords"]
            positions = [quote.find(term) for term in terms if term in quote]
            start = max(0, min(positions[0] - limit // 4, len(quote) - limit)) if positions else 0
            excerpt, _ = _truncate(quote[start:], limit)
            evidence.append({
                **{key: item[key] for key in ("evidence_id", "source_id", "locator", "content_hash") if key in item},
                "quote": excerpt,
            })
            truncated |= excerpt != quote
        context_text = "\n\n".join(blocks)
        bundle = {
            "schema": "investment.context_bundle",
            "schema_version": "1.0",
            "module": module,
            "scope": config["scope"],
            "inputs": inputs,
            "data_sections": section_metadata[0],
            "pdf_sections": section_metadata[1],
            "evidence": evidence,
            "unavailable_inputs": unavailable_inputs,
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
                "missing_pdf_sections": [key for key in config["pdf_sections"] if key not in parsed_pdf],
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
        if content_budget == 0:
            raise ValueError(f"Unable to fit {module} context bundle within {max_chars} characters")
        content_budget = max(0, content_budget - max(1, actual_chars - max_chars))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a bounded context bundle for one module")
    parser.add_argument("--module", required=True, choices=sorted(MODULE_CONFIG))
    parser.add_argument("--data-pack")
    parser.add_argument("--pdf-sections")
    parser.add_argument("--evidence-index")
    parser.add_argument("--max-chars", type=int, default=24000)
    parser.add_argument("--max-evidence", type=int, default=12)
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
        f"({bundle['budget']['actual_chars']}/{args.max_chars} chars, "
        f"estimated {bundle['budget']['estimated_context_tokens']} context tokens)"
    )


if __name__ == "__main__":
    main()
