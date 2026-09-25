#!/usr/bin/env python3
"""Create a small, addressable evidence index from text and JSON artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CHUNK_CHARS = 1200
DEFAULT_OVERLAP_CHARS = 120

#: Agent 专属段落（行业/竞争、MD&A、网络补充、治理定性信息）在**采集侧**只会留下占位符。
#: 按 REQ-006.2 AC-2.3 的策略（owner 2026-09-25 决定）：这些段落只由**全量分析**填充，
#: 增量更新流程不填；因此占位符**不得**被当成模块证据槽位（D3 曾把「待补充」当作行业证据）。
PLACEHOLDER_RE = re.compile(r"\*\[§[^\]]*待Agent WebSearch补充[^\]]*\]\*")
_HEADING_RE = re.compile(r"^[ \t]*#{1,6}[ \t].*$", re.MULTILINE)
_RULE_RE = re.compile(r"^[ \t]*-{3,}[ \t]*$", re.MULTILINE)
UNFILLED_REASON = (
    "该段落是 Agent 专属占位符，本 run 未填充（策略：仅全量分析填充，增量更新流程不填）；"
    "不得作为模块证据引用"
)


def unfilled_placeholder_markers(text: str) -> list[str]:
    """段落**只含**占位符（外加标题/分隔线）时返回占位符，否则返回空列表。"""

    markers = PLACEHOLDER_RE.findall(text)
    if not markers:
        return []
    residual = PLACEHOLDER_RE.sub("", text)
    residual = _HEADING_RE.sub("", residual)
    residual = _RULE_RE.sub("", residual)
    return markers if not residual.strip() else []


def strip_placeholder_lines(text: str) -> str:
    """去掉占位符行，留下段落里的真实内容（§13 这种混合段落用）。"""

    return PLACEHOLDER_RE.sub("", text)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def chunk_text(text: str, *, max_chars: int = DEFAULT_CHUNK_CHARS, overlap: int = DEFAULT_OVERLAP_CHARS) -> list[str]:
    """Split text without silently dropping content."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must be between 0 and max_chars")
    normalized = text.strip()
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + max_chars)
        if end < len(normalized):
            boundary = max(
                normalized.rfind("\n", start, end),
                normalized.rfind("。", start, end),
                normalized.rfind("；", start, end),
            )
            if boundary > start + max_chars // 2:
                end = boundary + 1
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(start + 1, end - overlap)
    return [chunk for chunk in chunks if chunk]


def _json_sections(path: Path, payload: Any) -> list[tuple[str, str]]:
    if not isinstance(payload, dict):
        return [(path.stem, json.dumps(payload, ensure_ascii=False, indent=2))]

    sections: list[tuple[str, str]] = []
    for key, value in payload.items():
        if key == "metadata":
            continue
        if isinstance(value, str) and value.strip():
            sections.append((str(key), value))
        elif value is not None:
            sections.append((str(key), json.dumps(value, ensure_ascii=False, indent=2)))
    return sections or [(path.stem, json.dumps(payload, ensure_ascii=False, indent=2))]


def _read_sections(path: Path) -> list[tuple[str, str]]:
    if path.suffix.lower() == ".json":
        return _json_sections(path, json.loads(path.read_text(encoding="utf-8")))
    if path.suffix.lower() == ".pdf":
        return [("pdf", "")]
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".md":
        # Numbered pack headings make financial tables and footnotes separately
        # addressable. Keep the heading in the quote, including units and dates.
        sections: list[tuple[str, str]] = []
        start = 0
        section = path.stem
        for match in re.finditer(r"^## ([^\n]+)", text, re.MULTILINE):
            if text[start:match.start()].strip():
                sections.append((section, text[start:match.start()]))
            title = match.group(1).strip()
            numbered = re.match(r"([A-Za-z]*\d+[A-Za-z]*|SUB)\.\s", title)
            section = numbered.group(1) if numbered else title
            start = match.start()
        if text[start:].strip():
            sections.append((section, text[start:]))
        return sections
    return [(path.stem, text)]


def build_evidence_index(
    sources: Iterable[dict[str, str]],
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    run_id: str | None = None,
    subject: dict[str, Any] | None = None,
    input_digest: str | None = None,
) -> dict[str, Any]:
    """Build an index whose entries can be selectively handed to an Agent."""

    entries: list[dict[str, Any]] = []
    source_manifest: list[dict[str, Any]] = []
    unfilled_sections: list[dict[str, Any]] = []
    for source in sources:
        source_id = source["source_id"]
        path = Path(source["path"])
        source_item: dict[str, Any] = {
            "source_id": source_id,
            "path": str(path),
            "exists": path.exists(),
            "format": path.suffix.lower().lstrip(".") or "text",
        }
        if not path.exists():
            source_manifest.append(source_item)
            continue

        source_text = path.read_text(encoding="utf-8") if path.suffix.lower() != ".pdf" else ""
        source_item.update(
            {
                "size_chars": len(source_text),
                "content_hash": _hash_text(source_text) if source_text else None,
                "extractable": bool(source_text),
            }
        )
        source_manifest.append(source_item)

        section_counts: dict[str, int] = {}
        for section, section_text in _read_sections(path):
            markers = unfilled_placeholder_markers(section_text)
            if markers:
                # 占位符不进索引：否则模块会把它当成真实证据（REQ-006.2 AC-2.3）。
                unfilled_sections.append(
                    {
                        "source_id": source_id,
                        "section": section,
                        "reason": UNFILLED_REASON,
                        "markers": markers,
                        "locator": {"path": str(path), "section": section},
                    }
                )
                continue
            # 混合段落（如 §13：有真实 13.1 + 占位符 13.2）只去掉占位符行，
            # 真实内容照常入索引，占位符不会出现在任何 quote 里。
            section_text = strip_placeholder_lines(section_text)
            for chunk in chunk_text(section_text, max_chars=chunk_chars, overlap=overlap_chars):
                chunk_number = section_counts.get(section, 0) + 1
                section_counts[section] = chunk_number
                evidence_id = f"{source_id}:{section}:{chunk_number:03d}"
                entries.append(
                    {
                        "evidence_id": evidence_id,
                        "source_id": source_id,
                        "section": section,
                        "chunk_number": chunk_number,
                        "quote": chunk,
                        "locator": {
                            "path": str(path),
                            "section": section,
                            "chunk": chunk_number,
                        },
                        "content_hash": _hash_text(chunk),
                    }
                )

    index = {
        "schema": "investment.evidence_index",
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": source_manifest,
        "entries": entries,
        "unfilled_sections": unfilled_sections,
    }
    if run_id is not None:
        index["run"] = {"run_id": run_id}
    if subject is not None:
        index["subject"] = subject
    if input_digest is not None:
        index["input_digest"] = input_digest
    return index


def bundle_evidence_ids(bundle: dict[str, Any]) -> set[str]:
    """context bundle 里出现过的 evidence id 集合（即模块被允许引用的全部证据）。"""

    ids: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "evidence_id" and isinstance(value, str):
                    ids.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(bundle)
    return ids


def validate_bundle_evidence(result: dict[str, Any], bundle: dict[str, Any]) -> list[str]:
    """模块结果引用的证据必须落在这个模块自己的 bundle 里。

    实跑观察（REQ-006.1 第 7 条）：模块 agent 大量引用 bundle 之外的 evidence/index.json
    条目（environment 16 条里 9 条、business_moat 26 条里 21 条）——quote 逐字可验证，
    但绕过了契约里「只用 bundle 提供的证据 id」与上下文预算的设计。本函数把这条边界变成
    可执行的检查（CLI：`validate_result.py --context <bundle.json>`）。
    """

    allowed = bundle_evidence_ids(bundle)
    if not allowed:
        return ["context bundle contains no evidence ids"]
    errors: list[str] = []
    for position, evidence in enumerate(result.get("evidence", [])):
        evidence_id = evidence.get("evidence_id") if isinstance(evidence, dict) else None
        if isinstance(evidence_id, str) and evidence_id not in allowed:
            errors.append(
                f"evidence[{position}] ID {evidence_id!r} is outside this module's context bundle"
            )
    for position, claim in enumerate(result.get("claims", [])):
        if not isinstance(claim, dict):
            continue
        for evidence_id in claim.get("evidence_ids", []) or []:
            if isinstance(evidence_id, str) and evidence_id not in allowed:
                errors.append(
                    f"claims[{position}] references {evidence_id!r} outside this module's context bundle"
                )
    return errors


def validate_result_evidence(result: dict[str, Any], index: dict[str, Any]) -> list[str]:
    """Verify result evidence against the immutable evidence index."""

    if not isinstance(index, dict) or index.get("schema") != "investment.evidence_index":
        return ["evidence index has an invalid schema"]
    entries = index.get("entries")
    if not isinstance(entries, list):
        return ["evidence index entries must be a list"]
    by_id = {
        item.get("evidence_id"): item
        for item in entries
        if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
    }
    errors: list[str] = []
    for position, evidence in enumerate(result.get("evidence", [])):
        if not isinstance(evidence, dict):
            continue
        evidence_id = evidence.get("evidence_id")
        indexed = by_id.get(evidence_id) if isinstance(evidence_id, str) else None
        if indexed is None:
            errors.append(f"evidence[{position}] ID {evidence_id!r} is absent from the evidence index")
            continue
        if evidence.get("source_id") != indexed.get("source_id"):
            errors.append(f"evidence[{position}] source_id does not match the evidence index")
        if evidence.get("locator") != indexed.get("locator"):
            errors.append(f"evidence[{position}] locator does not match the evidence index")
        quote = evidence.get("quote", "")
        indexed_quote = indexed.get("quote", "")
        if (
            not isinstance(quote, str)
            or not quote.strip()
            or not isinstance(indexed_quote, str)
            or quote not in indexed_quote
        ):
            errors.append(f"evidence[{position}] quote does not match the evidence index")
        content_hash = evidence.get("content_hash")
        if content_hash is not None and content_hash != indexed.get("content_hash"):
            errors.append(f"evidence[{position}] content_hash does not match the evidence index")
    return errors


def select_evidence(
    index: dict[str, Any],
    *,
    evidence_ids: Iterable[str] | None = None,
    keywords: Iterable[str] = (),
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Select evidence by explicit IDs first, then keyword relevance."""

    if limit <= 0:
        return []
    entries = index.get("entries", [])
    by_id = {item.get("evidence_id"): item for item in entries if isinstance(item, dict)}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    for evidence_id in evidence_ids or ():
        item = by_id.get(evidence_id)
        if item and evidence_id not in seen:
            selected.append(item)
            seen.add(evidence_id)
            if len(selected) >= limit:
                return selected

    normalized_keywords = [keyword.lower() for keyword in keywords if keyword]
    scored = []
    for item in entries:
        evidence_id = item.get("evidence_id")
        if evidence_id in seen:
            continue
        haystack = f"{item.get('section', '')} {item.get('quote', '')}".lower()
        score = sum(keyword in haystack for keyword in normalized_keywords)
        if score:
            scored.append((score, item))

    for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True):
        evidence_id = item.get("evidence_id")
        if evidence_id in seen:
            continue
        selected.append(item)
        seen.add(evidence_id)
        if len(selected) >= limit:
            break
    return selected


def _parse_source(value: str) -> dict[str, str]:
    if "=" not in value:
        raise ValueError("source must use source_id=path")
    source_id, path = value.split("=", 1)
    if not source_id or not path:
        raise ValueError("source must use source_id=path")
    return {"source_id": source_id, "path": path}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an addressable evidence index")
    parser.add_argument("--source", action="append", required=True, help="source_id=path")
    parser.add_argument("--output", required=True)
    parser.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    parser.add_argument("--overlap-chars", type=int, default=DEFAULT_OVERLAP_CHARS)
    args = parser.parse_args()

    index = build_evidence_index(
        [_parse_source(value) for value in args.source],
        chunk_chars=args.chunk_chars,
        overlap_chars=args.overlap_chars,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Evidence index written: {args.output} ({len(index['entries'])} entries)")


if __name__ == "__main__":
    main()
