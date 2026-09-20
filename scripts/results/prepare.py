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

try:
    from scripts.periods import filename_to_period, is_valid_period
except ImportError:  # Support importing the package with scripts/ on sys.path.
    from periods import filename_to_period, is_valid_period

try:
    from scripts.version import framework_block
except ImportError:  # Support importing the package with scripts/ on sys.path.
    from version import framework_block


def _period_arg(value: str) -> str:
    """Argparse type for ``--primary-period``: normalize and reject unknowns."""

    normalized = str(value).strip().upper()
    if not is_valid_period(normalized):
        raise argparse.ArgumentTypeError(
            f"invalid period {value!r}: expected one of YYYYQ1, YYYYH1, YYYYQ3, YYYYFY"
        )
    return normalized


def _normalize_primary_period(primary_period: str | None) -> str:
    """Return the canonical primary period, or ``""`` when none was requested."""

    if primary_period is None:
        return ""
    normalized = str(primary_period).strip().upper()
    if not is_valid_period(normalized):
        raise ValueError(f"Invalid primary period: {primary_period!r}")
    return normalized


def _read_recorded_period(pdf_sections_path: Path) -> str:
    """Read ``metadata.period`` from a pdf_sections JSON file (``""`` if absent)."""

    try:
        payload = json.loads(pdf_sections_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    recorded = metadata.get("period")
    return recorded.strip().upper() if isinstance(recorded, str) else ""


def _select_pdf_sections(inputs_root: Path, primary_period: str) -> tuple[Path, list[str]]:
    """Choose the pdf_sections evidence source for this run.

    A per-period file (``pdf_sections_{period}.json``) wins when the caller
    asked for that primary period and the file exists. Otherwise the legacy
    ``pdf_sections.json`` is used; if it declares a different period than the
    requested primary period a warning is recorded so consumers can see the
    evidence does not match the run's primary period.
    """

    warnings: list[str] = []
    legacy = inputs_root / "pdf_sections.json"
    per_period = inputs_root / f"pdf_sections_{primary_period}.json" if primary_period else None
    if per_period is not None and per_period.is_file():
        recorded = _read_recorded_period(per_period)
        if recorded != primary_period:
            warnings.append(
                f"period mismatch: {per_period.name} metadata.period="
                f"{recorded or '<unknown>'!r} does not match primary_period={primary_period!r}"
            )
        return per_period, warnings

    if primary_period and legacy.is_file():
        recorded = _read_recorded_period(legacy)
        if recorded != primary_period:
            warnings.append(
                f"period mismatch: pdf_sections.json metadata.period="
                f"{recorded or '<unknown>'!r} does not match primary_period={primary_period!r}"
            )
    return legacy, warnings


def prepare_run(
    output_dir: str | Path,
    *,
    ticker: str,
    company: str,
    market: str = "CN",
    run_id: str | None = None,
    max_chars: int = 24000,
    primary_period: str | None = None,
    prior_analysis: str | Path | None = None,
) -> dict[str, object]:
    """Create all deterministic inputs needed by module Agents.

    Inputs are read from ``<output_dir>/inputs/`` when that directory exists
    (the run-store snapshot layout) and from ``<output_dir>/`` otherwise. The
    produced evidence/manifest/contexts structure is identical either way.

    ``prior_analysis`` registers a previous run's ``synthesis/result.json`` as
    the ``prior_analysis`` evidence source so the incremental ``period_delta``
    module can cite what the earlier conclusions actually said.
    """

    root = Path(output_dir).resolve()
    normalized_primary = _normalize_primary_period(primary_period)
    root.mkdir(parents=True, exist_ok=True)
    evidence_dir = root / "evidence"
    contexts_dir = root / "contexts"
    modules_dir = root / "modules"
    synthesis_dir = root / "synthesis"
    for directory in (evidence_dir, contexts_dir, modules_dir, synthesis_dir):
        directory.mkdir(parents=True, exist_ok=True)
    for module in MODULE_CONFIG:
        (modules_dir / module).mkdir(exist_ok=True)

    inputs_dir = root / "inputs"
    inputs_root = inputs_dir if inputs_dir.is_dir() else root

    data_pack = inputs_root / "data_pack_market.md"
    footnote_report = inputs_root / "data_pack_report.md"
    annual_reports = sorted(inputs_root.glob("*.pdf"))
    pdf_sections, warnings = _select_pdf_sections(inputs_root, normalized_primary)

    prior_analysis_path = Path(prior_analysis) if prior_analysis else None
    if prior_analysis_path is not None and not prior_analysis_path.is_file():
        warnings.append(f"prior analysis not found: {prior_analysis_path}")
        prior_analysis_path = None

    sources = [
        {"source_id": "market_data", "path": str(data_pack)},
        {"source_id": "pdf_sections", "path": str(pdf_sections)},
        {"source_id": "pdf_footnotes", "path": str(footnote_report)},
    ]
    if prior_analysis_path is not None:
        sources.append({"source_id": "prior_analysis", "path": str(prior_analysis_path.resolve())})
    # ``annual_report:{stem}`` is a hard contract: evidence ids are embedded in
    # completed runs, so the source_id never changes -- only metadata is added.
    report_periods: dict[str, str] = {}
    for report in annual_reports:
        source_id = f"annual_report:{report.stem}"
        sources.append({"source_id": source_id, "path": str(report)})
        period = filename_to_period(report.name)
        if period:
            report_periods[source_id] = period

    resolved_run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    # run-store 布局下 ``runs.py new`` 已经签发了 run_id 并写进 run.json：此处必须以它为准。
    # 不这样做的话，「文档漏传 --run-id」会让 prepare 另生成一个 id，台账 id 与 run 内部 id 分叉，
    # 而且没有任何报错（2026-09-20 实跑实测）。
    run_meta_path = root / "run.json"
    if run_meta_path.is_file():
        try:
            recorded_run_id = json.loads(run_meta_path.read_text(encoding="utf-8")).get("run_id")
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"run.json is unreadable: {run_meta_path}: {exc}") from exc
        if isinstance(recorded_run_id, str) and recorded_run_id:
            if run_id and run_id != recorded_run_id:
                raise ValueError(
                    f"run_id {run_id!r} does not match this run directory's run.json "
                    f"({recorded_run_id!r}); pass the run_id issued by `runs.py new` so the "
                    "ledger id and the run's internal id cannot diverge"
                )
            resolved_run_id = recorded_run_id
    subject = {"ticker": validate_stock_code(ticker), "company": company, "market": market}
    input_paths = []
    for source in sources:
        item = describe_input(source["path"], source_id=source["source_id"])
        period = report_periods.get(source["source_id"])
        if period:
            item["period"] = period
        input_paths.append(item)
    input_digest = input_set_digest(input_paths)

    evidence_index = build_evidence_index(
        sources,
        run_id=resolved_run_id,
        subject=subject,
        input_digest=input_digest,
    )
    for source_meta in evidence_index.get("sources", []):
        period = report_periods.get(source_meta.get("source_id"))
        if period:
            source_meta["period"] = period
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
        framework=framework_block(),
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
    # Additive metadata only: ``schema_version`` stays "1.0" for old consumers.
    if normalized_primary:
        manifest["primary_period"] = normalized_primary
    if warnings:
        manifest["warnings"] = warnings
    manifest_path = root / "run_manifest.json"
    write_manifest(manifest, manifest_path)
    return {
        "output_dir": str(root),
        "run_manifest": str(manifest_path),
        "evidence_index": str(evidence_path),
        "d6_trigger": str(d6_trigger_path),
        "d6_triggered": d6_trigger["triggered"],
        "contexts": context_paths,
        "primary_period": normalized_primary,
        "prior_analysis": str(prior_analysis_path.resolve()) if prior_analysis_path else "",
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare evidence-first analysis inputs")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--market", default="CN")
    parser.add_argument("--run-id")
    parser.add_argument("--max-chars", type=int, default=24000)
    parser.add_argument(
        "--primary-period",
        type=_period_arg,
        default=None,
        help=(
            "Primary report period for this run (e.g. 2026H1). Selects the "
            "matching pdf_sections_{period}.json and is recorded in the manifest"
        ),
    )
    parser.add_argument(
        "--prior-analysis",
        default=None,
        help=(
            "Previous run's synthesis/result.json. Registers it as the "
            "prior_analysis evidence source for the incremental period_delta module"
        ),
    )
    args = parser.parse_args()

    result = prepare_run(
        args.output_dir,
        ticker=args.ticker,
        company=args.company,
        market=args.market,
        run_id=args.run_id,
        max_chars=args.max_chars,
        primary_period=args.primary_period,
        prior_analysis=args.prior_analysis,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
