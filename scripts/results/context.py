#!/usr/bin/env python3
"""Build bounded, module-specific context bundles for analysis Agents."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .evidence import select_evidence


MODULE_CONFIG: dict[str, dict[str, Any]] = {
    "business_moat": {
        "scope": ["D1", "D2"],
        "data_sections": ["1.", "2.", "3.", "3P.", "4.", "4P.", "5.", "8.", "9.", "12.", "17."],
        "pdf_sections": ["MDA", "SUB", "P3", "P4"],
        "keywords": ["护城河", "品牌", "竞争", "主营", "毛利率", "ROE", "现金流"],
    },
    "environment": {
        "scope": ["D3"],
        "data_sections": ["1.", "3.", "8.", "10.", "12.", "14."],
        "pdf_sections": ["MDA", "P13"],
        "keywords": ["行业", "周期", "监管", "政策", "需求", "竞争"],
    },
    "governance": {
        "scope": ["D4"],
        "data_sections": ["1.", "7.", "10.", "13.", "15.", "16."],
        "pdf_sections": ["GOV", "MATTERS", "P2", "P13", "P4", "P6"],
        "keywords": ["审计", "治理", "关联交易", "质押", "诉讼", "承诺", "重大事项"],
    },
    "mda_quality": {
        "scope": ["D5"],
        "data_sections": ["1.", "3.", "5.", "6.", "10.", "12.", "15.", "17."],
        "pdf_sections": ["MDA", "P13", "P3", "P6"],
        "keywords": ["收入", "利润", "现金流", "分红", "回购", "指引", "风险"],
    },
    "holding_structure": {
        "scope": ["D6"],
        "data_sections": ["1.", "4.", "4P.", "9."],
        "pdf_sections": ["SUB", "P6", "P4"],
        "keywords": ["子公司", "控股", "参股", "长期股权投资", "合并范围"],
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

    config = MODULE_CONFIG[module]
    text_limit = max(1, int(max_chars * 0.85))
    section_budget = max(1, int(text_limit * 0.7))
    data_sections: list[dict[str, Any]] = []
    pdf_sections: list[dict[str, Any]] = []
    blocks: list[str] = []
    used_chars = 0
    truncated = False
    inputs: list[str] = []

    if data_pack_path:
        data_path = Path(data_pack_path)
        inputs.append(str(data_path))
        if data_path.exists():
            parsed = _parse_markdown_sections(data_path.read_text(encoding="utf-8"))
            for prefix in config["data_sections"]:
                match = _find_section(parsed, prefix)
                if not match:
                    continue
                title, content = match
                remaining = section_budget - used_chars
                if remaining <= 0:
                    truncated = True
                    break
                selected, was_truncated = _truncate(content, remaining)
                data_sections.append(
                    {"title": title, "selected_chars": len(selected), "truncated": was_truncated}
                )
                blocks.append(f"[Market data: {title}]\n{selected}")
                used_chars += len(selected)
                truncated = truncated or was_truncated

    pdf_path = Path(pdf_sections_path) if pdf_sections_path else None
    if pdf_path:
        inputs.append(str(pdf_path))
    parsed_pdf = _load_pdf_sections(pdf_path)
    for section_id in config["pdf_sections"]:
        content = parsed_pdf.get(section_id)
        if not content:
            continue
        remaining = section_budget - used_chars
        if remaining <= 0:
            truncated = True
            break
        selected, was_truncated = _truncate(content, remaining)
        pdf_sections.append(
            {"section": section_id, "selected_chars": len(selected), "truncated": was_truncated}
        )
        blocks.append(f"[PDF {section_id}]\n{selected}")
        used_chars += len(selected)
        truncated = truncated or was_truncated

    evidence: list[dict[str, Any]] = []
    if evidence_index_path:
        evidence_path = Path(evidence_index_path)
        inputs.append(str(evidence_path))
        if evidence_path.exists():
            index = json.loads(evidence_path.read_text(encoding="utf-8"))
            selected_evidence = select_evidence(index, keywords=config["keywords"], limit=max_evidence)
            for item in selected_evidence:
                evidence_text = f"[Evidence {item['evidence_id']}]\n{item.get('quote', '')}"
                remaining = text_limit - used_chars
                if remaining <= 0:
                    truncated = True
                    break
                selected, was_truncated = _truncate(evidence_text, remaining)
                blocks.append(selected)
                used_chars += len(selected)
                truncated = truncated or was_truncated
                evidence.append(
                    {
                        "evidence_id": item.get("evidence_id"),
                        "source_id": item.get("source_id"),
                        "locator": item.get("locator", {}),
                        "content_hash": item.get("content_hash"),
                    }
                )

    context_text, final_truncated = _truncate("\n\n".join(blocks), text_limit)
    truncated = truncated or final_truncated
    bundle = {
        "schema": "investment.context_bundle",
        "schema_version": "1.0",
        "module": module,
        "scope": config["scope"],
        "inputs": inputs,
        "data_sections": data_sections,
        "pdf_sections": pdf_sections,
        "evidence": evidence,
        "context_text": context_text,
        "budget": {
            "max_chars": max_chars,
            "actual_chars": 0,
            "estimated_context_tokens": _estimate_tokens(context_text),
            "truncated": truncated,
        },
        "selection": {
            "data_section_prefixes": config["data_sections"],
            "pdf_section_ids": config["pdf_sections"],
            "keywords": config["keywords"],
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
        while True:
            actual_chars = len(json.dumps(bundle, ensure_ascii=False, indent=2)) + 1
            if bundle["budget"]["actual_chars"] == actual_chars:
                break
            bundle["budget"]["actual_chars"] = actual_chars
        if actual_chars <= max_chars:
            return bundle
        if bundle["evidence"]:
            removed = bundle["evidence"].pop()
            marker = f"[Evidence {removed['evidence_id']}]"
            marker_position = bundle["context_text"].rfind(marker)
            if marker_position >= 0:
                bundle["context_text"] = bundle["context_text"][:marker_position].rstrip()
            bundle["budget"]["truncated"] = True
            bundle["budget"]["estimated_context_tokens"] = _estimate_tokens(bundle["context_text"])
            continue
        overflow = actual_chars - max_chars
        target_length = len(bundle["context_text"]) - overflow - 8
        if target_length <= 0:
            raise ValueError(f"Unable to fit {module} context bundle within {max_chars} characters")
        bundle["context_text"], _ = _truncate(bundle["context_text"], target_length)
        bundle["budget"]["truncated"] = True
        bundle["budget"]["estimated_context_tokens"] = _estimate_tokens(bundle["context_text"])


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
