#!/usr/bin/env python3
"""Build the bounded input bundle consumed by the final synthesis Agent."""

from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path
from typing import Any, Iterable

from .evidence import validate_result_evidence
from .schema import load_result, require_consistent_result_set, result_set_digest

#: 默认预算，单位是序列化字符。取值的依据是两次真实实跑（REQ-006.1）：
#: 2026-09-20 那次六模块载荷实际需要约 60k（当时靠人工传 `--max-chars 60000` 才跑通）；
#: 2026-09-25 的 AC-1.9 复跑实测**完整载荷 96,496 字符** —— 旧的 40,000 下丢弃 148 项
#: （含每个模块的 `quality.missing_inputs` 与本期关键减值口径），60,000 下仍丢 128 项，
#: 120,000 起才零丢弃。故默认值取 120,000，为后续期次留出余量。
DEFAULT_MAX_CHARS = 120000


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, indent=2)) + 1


def _referenced_evidence(value: Any) -> list[str]:
    references: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "evidence_ids" and isinstance(item, list):
                references.extend(ref for ref in item if isinstance(ref, str))
            else:
                references.extend(_referenced_evidence(item))
    elif isinstance(value, list):
        for item in value:
            references.extend(_referenced_evidence(item))
    return list(dict.fromkeys(references))


def _initial_card(result: dict[str, Any]) -> dict[str, Any]:
    # Keep the primary judgement and risk with their exact evidence even when
    # optional details are too large. Units/date/basis must accompany metrics.
    card = {key: result[key] for key in ("result_type", "run", "scope", "summary", "parameters")}
    high_risks = [item for item in result["risks"] if isinstance(item, dict) and item.get("severity") == "high"]
    card["risks"] = high_risks or result["risks"][:1]
    basis_keys = {"basis", "period_basis", "unit", "amount_unit", "currency", "period", "periods", "as_of", "period_end"}
    card["metrics"] = {key: value for key, value in result["metrics"].items() if key in basis_keys}
    card["claims"] = result["claims"][:1]
    card["watchlist"] = []
    quality = result["quality"]
    card["quality"] = {"completeness": quality["completeness"]}
    for key, value in quality.items():
        if isinstance(value, list):
            card["quality"][key] = value[:1] if value and _size(value[0]) <= 500 else []
    return card


def _candidates(result: dict[str, Any], card: dict[str, Any]):
    queues = []
    for key in ("metrics", "risks", "claims", "quality", "watchlist"):
        value = result[key]
        if key == "metrics":
            queues.append(deque((key, name, item) for name, item in value.items() if name not in card[key]))
        elif key == "quality":
            for name, items in value.items():
                if isinstance(items, list):
                    queues.append(deque((key, name, item) for item in items[len(card[key][name]):]))
                elif name not in card[key]:
                    queues.append(deque([(key, name, items)]))
        else:
            # 列表类条目（risks / claims / watchlist）也必须逐条记账：先前把 name 写成 None，
            # 于是标签恒为 "claims:None"，而 dropped_count 用 set() 去重会把一个模块里被丢的
            # 10 条 claims 折叠成 1 条（2026-09-25 实跑实测：报 148，实际 299）。
            queues.append(deque(
                (key, _list_item_label(key, item, position), item)
                for position, item in enumerate(value)
                if item not in card[key]
            ))
    while any(queues):
        for queue in queues:
            if queue:
                yield queue.popleft()


def _drop_label(key: str, name: str, item: Any) -> str:
    """给「被让出的这条卡片内容」一个可读且**逐条唯一**的标签。

    列表类条目用条目自己的标识（claim_id / risk / item / statement 或序号），
    其余用 ``key:name``。标签只用于 ``budget.dropped`` 与 ``dropped_count`` 去重计数，
    所以必须逐条不同：笼统标签会让 `dropped_count` 小于实际丢弃条数。
    """

    if key in {"claims", "risks", "watchlist"}:
        return f"{key}:{_list_item_label(key, item, 0)}"
    return f"{key}:{name}"


def _list_item_label(key: str, item: Any, position: int) -> str:
    """给列表类条目起一个稳定且可读的丢弃标签（用于 budget.dropped 与 dropped_count）。"""

    if isinstance(item, dict):
        for field in ("claim_id", "risk", "item", "statement"):
            value = item.get(field)
            if isinstance(value, str) and value:
                return value[:40]
    elif isinstance(item, str) and item:
        return item[:40]
    return f"#{position}"


def _result_card(result: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any]:
    card = dict(selected)
    by_id = {item["evidence_id"]: item for item in result["evidence"]}
    references = _referenced_evidence(card)
    missing = [ref for ref in references if ref not in by_id]
    if missing:
        raise ValueError(f"{result['result_type']} references evidence without a module excerpt: {missing}")
    card["evidence"] = [by_id[ref] for ref in references]
    card["omitted"] = {
        key: len(result[key]) - len(card[key])
        for key in ("metrics", "claims", "risks", "watchlist", "evidence")
    }
    card["omitted"]["quality"] = {
        key: len(value) - len(card["quality"].get(key, [])) if isinstance(value, list) else int(key not in card["quality"])
        for key, value in result["quality"].items() if key != "completeness"
    }
    return card


def _shared_evidence(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shared: dict[str, dict[str, Any]] = {}
    for card in cards:
        references = []
        for item in card["evidence"]:
            evidence_id = item["evidence_id"]
            entry = shared.setdefault(evidence_id, {
                key: item[key]
                for key in ("evidence_id", "source_id", "locator", "quote", "content_hash") if key in item
            })
            quotes = [entry["quote"], *entry.get("alternative_quotes", [])]
            if item["quote"] not in quotes:
                entry.setdefault("alternative_quotes", []).append(item["quote"])
                quotes.append(item["quote"])
            references.append({"evidence_id": evidence_id, "quote_index": quotes.index(item["quote"])})
        card["evidence"] = references
    return list(shared.values())


def _require_artifact_identity(
    artifact: dict[str, Any],
    *,
    schema: str,
    run_id: str,
    subject: dict[str, Any],
) -> None:
    if not isinstance(artifact, dict) or artifact.get("schema") != schema:
        raise ValueError(f"Expected {schema} artifact")
    if not isinstance(artifact.get("run"), dict) or artifact["run"].get("run_id") != run_id:
        raise ValueError(f"{schema} run_id does not match module results")
    if artifact.get("subject") != subject:
        raise ValueError(f"{schema} subject does not match module results")


def build_synthesis_context(
    result_paths: Iterable[str | Path],
    *,
    optional_result_paths: Iterable[str | Path] = (),
    reconciliation_path: str | Path,
    evidence_index_path: str | Path,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Create a compact, valid JSON handoff for Final Synthesis Agent.

    默认预算见 :data:`DEFAULT_MAX_CHARS`：它按**真实载荷**设定（2026-09-25 实测完整载荷
    96,496 字符），而不是按更小的样本估算。原来的 40,000 默认值在真实 run 上会丢掉
    148 项卡片内容，包括每个模块的 `quality.missing_inputs` —— 即「缺口披露」本身被丢掉。
    """

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    optional_result_paths = list(optional_result_paths)
    missing_modules = [str(path) for path in optional_result_paths if not Path(path).exists()]
    present_paths = [*result_paths, *(path for path in optional_result_paths if Path(path).exists())]
    results = [load_result(path) for path in present_paths]
    results = require_consistent_result_set(results)
    reconciliation = json.loads(Path(reconciliation_path).read_text(encoding="utf-8"))
    evidence_index = json.loads(Path(evidence_index_path).read_text(encoding="utf-8"))
    run_id = results[0]["run"]["run_id"]
    subject = results[0]["subject"]
    upstream_digest = result_set_digest(results)
    _require_artifact_identity(
        reconciliation,
        schema="investment.reconciliation",
        run_id=run_id,
        subject=subject,
    )
    if reconciliation.get("result_digest") != upstream_digest:
        raise ValueError("investment.reconciliation result_digest does not match module results")
    _require_artifact_identity(
        evidence_index,
        schema="investment.evidence_index",
        run_id=run_id,
        subject=subject,
    )
    evidence_errors = [
        f"{result['result_type']}: {error}"
        for result in results
        for error in validate_result_evidence(result, evidence_index)
    ]
    if evidence_errors:
        raise ValueError("Invalid module evidence:\n- " + "\n- ".join(evidence_errors))

    # 装不下的卡片内容不能静默消失：逐条记账，写进 budget["dropped"]（REQ-006.1 AC-1.5）。
    # 声明必须在 build_payload 第一次调用之前：它是闭包变量。
    dropped: dict[str, list[str]] = {}
    selections = [_initial_card(result) for result in results]
    for card, path in zip(selections, present_paths):
        card["result_path"] = str(path)

    def build_payload() -> dict[str, Any]:
        cards = [
            _result_card(result, selected)
            for result, selected in zip(results, selections)
        ]
        selected = _shared_evidence(cards)
        payload = {
            "schema": "investment.synthesis_context",
            "schema_version": "1.0",
            "run": {"run_id": run_id},
            "subject": subject,
            "upstream_digest": upstream_digest,
            "modules": cards,
            "reconciliation": reconciliation,
            "evidence": selected,
            "missing_modules": missing_modules,
            "instructions": {
                "must_rewrite": True,
                "must_address_conflicts": True,
                "must_cite_evidence": True,
                "omissions_require_targeted_lookup": True,
                "quote_index": "0 = quote; n > 0 = alternative_quotes[n - 1]. Copy one continuous excerpt; never concatenate alternatives.",
                # REQ-006.2 AC-2.5 / 发现 F25：同一个块的不同摘录要能被**分别引用**，
                # 否则同一块里的两个数值只能有一个进最终 sidecar。
                "sub_excerpts": (
                    "When one chunk's several values must each be cited, reference them "
                    "separately as `<evidence_id>#<n>` (n = quote_index of that excerpt); "
                    "each reference keeps its own continuous quote. A bare id equals `#0`."
                ),
                "source_of_truth": "module results plus reconciliation; numerical metrics remain deterministic",
            },
        }
        payload["budget"] = {
            "max_chars": max_chars,
            "actual_chars": 0,
            "module_count": len(results),
            "evidence_count": len(payload["evidence"]),
            # 丢弃记录写进 payload 本身，因此也受预算约束：装不下时不会反过来把 payload 顶爆。
            # 只留模块 + 最多 3 个字段名做样本，完整条数看 dropped_count。
            "dropped": {
                module: sorted(set(fields))[:3]
                for module, fields in sorted(dropped.items())
            },
            "dropped_count": sum(len(set(fields)) for fields in dropped.values()),
        }
        while True:
            actual_chars = _size(payload)
            if payload["budget"]["actual_chars"] == actual_chars:
                break
            payload["budget"]["actual_chars"] = actual_chars
        return payload

    payload = build_payload()
    if payload["budget"]["actual_chars"] > max_chars:
        raise ValueError(
            f"Unable to fit protected synthesis content within {max_chars} characters "
            f"(requires {payload['budget']['actual_chars']}); parameters, primary claims, "
            "key risks, metric basis and reconciliation cannot be discarded"
        )
    # Fair round-robin admission measures the complete JSON including shared
    # excerpts and omission metadata. Large entries cannot block small ones.
    candidates = [deque(_candidates(result, card)) for result, card in zip(results, selections)]
    admitted: list[tuple] = []
    while any(candidates):
        for result, card, queue in zip(results, selections, candidates):
            if not queue:
                continue
            key, name, item = queue.popleft()
            target = card[key]
            is_list = key not in {"metrics", "quality"} or (
                key == "quality" and isinstance(target.get(name), list)
            )
            if key == "quality" and is_list:
                target = target[name]
            if is_list:
                target.append(item)
            else:
                target[name] = item
            trial = build_payload()
            if trial["budget"]["actual_chars"] <= max_chars:
                payload = trial
                admitted.append(
                    (
                        result["result_type"],
                        target,
                        None if is_list else name,
                        # 被让出时要能报出**这条**内容的名字，否则只能报一个笼统标签，
                        # dropped_count 会小于 omitted 之和（自检不变量见测试）。
                        _drop_label(key, name, item),
                    )
                )
            elif is_list:
                target.pop()
                dropped.setdefault(result["result_type"], []).append(_drop_label(key, name, item))
            else:
                del target[name]
                dropped.setdefault(result["result_type"], []).append(_drop_label(key, name, item))
    # 记账本身也占字符：加上最后一批丢弃记录后若超预算，就继续让出最大的卡片内容。
    payload = build_payload()
    while _size(payload) > max_chars and admitted:
        module, target, name, label = admitted.pop()
        if name is None:
            target.pop()
        else:
            del target[name]
        dropped.setdefault(module, []).append(label)
        payload = build_payload()
    payload["budget"]["actual_chars"] = _size(payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a bounded final synthesis context")
    parser.add_argument("--input", action="append", required=True, help="module result.json")
    parser.add_argument("--optional-input", action="append", default=[], help="optional module result.json")
    parser.add_argument("--reconciliation", required=True)
    parser.add_argument("--evidence-index", required=True)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = build_synthesis_context(
        args.input,
        optional_result_paths=args.optional_input,
        reconciliation_path=args.reconciliation,
        evidence_index_path=args.evidence_index,
        max_chars=args.max_chars,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Synthesis context written: {args.output} ({payload['budget']['actual_chars']} chars)")
    dropped = payload["budget"].get("dropped") or {}
    if dropped:
        detail = "；".join(f"{module}: {', '.join(fields)}" for module, fields in dropped.items())
        print(f"预算不足，已丢弃 {payload['budget']['dropped_count']} 项卡片内容（{detail}）；"
              "需要完整卡片请上调 --max-chars")


if __name__ == "__main__":
    main()
