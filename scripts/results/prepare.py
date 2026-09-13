#!/usr/bin/env python3
"""Prepare the bounded, evidence-first workspace for one analysis run."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .context import MODULE_CONFIG, build_module_context
from .evidence import build_evidence_index
from .manifest import build_manifest, describe_artifact, describe_input, input_set_digest, write_manifest

try:
    from scripts.split_data_pack import check_d6_trigger, parse_sections
except ImportError:  # Support importing the package with scripts/ on sys.path.
    from split_data_pack import check_d6_trigger, parse_sections

try:
    from scripts.config import validate_stock_code
except ImportError:  # Support importing the package with scripts/ on sys.path.
    from config import validate_stock_code


def prepare_run(
    output_dir: str | Path,
    *,
    ticker: str,
    company: str,
    market: str = "CN",
    run_id: str | None = None,
    max_chars: int = 24000,
) -> dict[str, object]:
    """Create all deterministic inputs needed by module Agents."""

    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    evidence_dir = root / "evidence"
    contexts_dir = root / "contexts"
    modules_dir = root / "modules"
    synthesis_dir = root / "synthesis"
    for directory in (evidence_dir, contexts_dir, modules_dir, synthesis_dir):
        directory.mkdir(parents=True, exist_ok=True)
    for module in MODULE_CONFIG:
        (modules_dir / module).mkdir(exist_ok=True)

    data_pack = root / "data_pack_market.md"
    pdf_sections = root / "pdf_sections.json"
    footnote_report = root / "data_pack_report.md"
    annual_reports = sorted(root.glob("*.pdf"))
    sources = [
        {"source_id": "market_data", "path": str(data_pack)},
        {"source_id": "pdf_sections", "path": str(pdf_sections)},
        {"source_id": "pdf_footnotes", "path": str(footnote_report)},
    ]
    for report in annual_reports:
        sources.append({"source_id": f"annual_report:{report.stem}", "path": str(report)})

    resolved_run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    subject = {"ticker": validate_stock_code(ticker), "company": company, "market": market}
    input_paths = [
        describe_input(source["path"], source_id=source["source_id"])
        for source in sources
    ]
    input_digest = input_set_digest(input_paths)

    evidence_index = build_evidence_index(
        sources,
        run_id=resolved_run_id,
        subject=subject,
        input_digest=input_digest,
    )
    evidence_path = evidence_dir / "index.json"
    evidence_path.write_text(json.dumps(evidence_index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if data_pack.exists():
        d6_trigger = check_d6_trigger(parse_sections(data_pack.read_text(encoding="utf-8")))
        d6_trigger["status"] = "required" if d6_trigger["triggered"] else "not_required"
    else:
        d6_trigger = {
            "triggered": None,
            "status": "unknown",
            "reasons": ["data_pack_market.md 不存在，无法判断是否触发 D6"],
            "note": "D6 未判断",
        }
    d6_trigger["run"] = {"run_id": resolved_run_id}
    d6_trigger["subject"] = subject
    d6_trigger_path = root / "d6_trigger.json"
    d6_trigger_path.write_text(json.dumps(d6_trigger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    context_paths: dict[str, str] = {}
    for module in MODULE_CONFIG:
        context = build_module_context(
            module,
            data_pack_path=data_pack if data_pack.exists() else None,
            pdf_sections_path=pdf_sections if pdf_sections.exists() else None,
            evidence_index_path=evidence_path,
            max_chars=max_chars,
            run_id=resolved_run_id,
            subject=subject,
            input_digest=input_digest,
            routing=d6_trigger if module == "holding_structure" else None,
        )
        context_path = contexts_dir / f"{module}.json"
        context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        context_paths[module] = str(context_path)

    manifest = build_manifest(
        run_id=resolved_run_id,
        subject=subject,
        inputs=input_paths,
        artifacts=[
            describe_artifact(evidence_path, format="json", role="evidence_index"),
            describe_artifact(d6_trigger_path, format="json", role="routing_decision"),
            *[
                describe_artifact(path, format="json", role="context_bundle", module=module)
                for module, path in context_paths.items()
            ],
        ],
        status="prepared",
    )
    manifest_path = root / "run_manifest.json"
    write_manifest(manifest, manifest_path)
    return {
        "output_dir": str(root),
        "run_manifest": str(manifest_path),
        "evidence_index": str(evidence_path),
        "d6_trigger": str(d6_trigger_path),
        "d6_triggered": d6_trigger["triggered"],
        "contexts": context_paths,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare evidence-first analysis inputs")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--market", default="CN")
    parser.add_argument("--run-id")
    parser.add_argument("--max-chars", type=int, default=24000)
    args = parser.parse_args()

    result = prepare_run(
        args.output_dir,
        ticker=args.ticker,
        company=args.company,
        market=args.market,
        run_id=args.run_id,
        max_chars=args.max_chars,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
