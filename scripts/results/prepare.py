#!/usr/bin/env python3
"""Prepare the bounded, evidence-first workspace for one analysis run."""

from __future__ import annotations

import argparse
import json
import re
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


#: 附注证据源的文件名。**中报包优先**：本期披露附注时用它，年报包是可选的对照来源；
#: 反过来的话「inputs/ 里同时有中报与年报附注包」会静默选中上年报的（发现 F29 的同族问题）。
#: 2026-09-25 实跑里 ``--input`` 清单从不含附注源 → 每一轮 run 都是 ``exists=false``
#: 且没有任何提示，模块只在 evidence_coverage 里看到一个 ``missing``（REQ-006.2 AC-2.6）。
FOOTNOTE_SOURCE_NAMES = ("data_pack_report_interim.md", "data_pack_report.md")
#: 附注包头部声明的报告期（机器可读）。规格见 ``prompts/phase2_PDF解析.md`` 的「输出格式」：
#: ``> 报告期：2026H1``。声明是**权威**的；缺失时才退回按文件名/资料截止日**推断**，
#: 推断结果一律带 ``basis`` 标注，绝不冒充声明（REQ-006.2 发现 F29）。
_DECLARED_PERIOD_RE = re.compile(r"^>\s*报告期\s*[：:]\s*(20\d{2}(?:Q1|H1|Q3|FY))\s*$", re.MULTILINE)
#: 年报包的头部会写「资料截止日：2025-12-31（年报财务报表及附注）」，作为推断年份的兜底。
_CUTOFF_DATE_RE = re.compile(r"资料截止日\s*[：:]\s*(\d{4})-\d{2}-\d{2}")


def _read_declared_period(pack_path: Path) -> tuple[str, bool]:
    """Read the ``> 报告期：YYYYH1`` header from a footnote pack.

    Returns ``(period, declared_line_present)``: the second flag is ``True`` when
    a ``报告期`` line exists at all, so a **malformed** declaration (e.g. a date
    instead of a period id) is not silently mistaken for a legacy pack.
    """

    header = _read_pack_header(pack_path)
    match = _DECLARED_PERIOD_RE.search(header)
    if match:
        return match.group(1), True
    return "", bool(re.search(r"^\s*>?\s*报告期\s*[：:]", header, re.MULTILINE))


def _read_pack_header(pack_path: Path) -> str:
    """Return the metadata block of a footnote pack (first lines only).

    只看头部：报告期必须和 PDF来源 / 总页数 一起出现在元数据块里，正文里的
    「报告期」字样不参与判定（否则正文提及历史报告期会污染判定）。
    """

    try:
        text = pack_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return "\n".join(text.splitlines()[:20])


def _infer_period_from_pack(pack_path: Path) -> str:
    """Infer the footnote period from the file name + the header's ``资料截止日``.

    The report type comes from the file name (``..._interim.md`` → 中报 / ``H1``,
    otherwise the annual pack / ``FY``); the **year** comes from the cut-off date,
    because an annual pack's PDF is the *previous* year's (``600887_2025_年报.pdf``
    carries ``资料截止日：2025-12-31``). Returns ``""`` when not inferable.
    """

    suffix = "H1" if pack_path.name.endswith("_interim.md") else "FY"
    cutoff = _CUTOFF_DATE_RE.search(_read_pack_header(pack_path))
    if not cutoff:
        return ""
    return f"{cutoff.group(1)}{suffix}"


def _footnote_period(pack_path: Path) -> tuple[str, str]:
    """Registered period of a footnote pack and the basis for it.

    ``basis`` is one of:

    * ``"declared"``: the pack's ``> 报告期：YYYYH1`` header (authoritative);
    * ``"filename+cutoff"``: inferred from the file name + ``资料截止日``;
    * ``"invalid"``: a `报告期` line exists but carries no usable period id —
      that **must** warn rather than pass as a legacy pack;
    * ``"absent"``: the pack carries **no** period metadata at all (legacy pack)
      — nothing to verify, and deliberately **not** a warning: warning on every
      pre-existing pack would be noise, while the declared-period contract below
      is what makes the check decidable from now on.
    """

    declared, has_declared_line = _read_declared_period(pack_path)
    if declared and is_valid_period(declared):
        return declared, "declared"
    inferred = _infer_period_from_pack(pack_path)
    if inferred:
        return inferred, "filename+cutoff"
    if has_declared_line:
        return "", "invalid"
    return "", "absent"


def _footnote_period_diagnostics(
    pack_path: Path, footnote_period: str, period_basis: str, primary_period: str,
) -> tuple[list[str], dict[str, str] | None]:
    """Period mismatch checks for the footnote source (REQ-006.2 发现 F29).

    ``AC-2.6`` only covered「源缺失」.  F29 is the harder case: the source exists
    but belongs to **another period** (run ``20260925T091012981574Z`` mixed a
    2025FY annual footnote pack into a 2026H1 interim run), so 担保/关联交易 etc.
    were cited as if they were same-period data.  Returns ``(warnings, mismatch)``.
    """

    if not primary_period:
        return [], None
    if footnote_period:
        if footnote_period == primary_period:
            return [], None
        return (
            [
                "附注源期次与 primary_period 不一致（period mismatch）："
                f"primary_period={primary_period}，附注源 {pack_path.name} 的期次为 "
                f"{footnote_period}（依据：{period_basis}）。"
                "附注数据不得当同期数据引用；请改用本期附注包"
                "（中报用 data_pack_report_interim.md），或确认本期确实不需要附注证据。"
            ],
            {
                "source_id": "pdf_footnotes",
                "path": str(pack_path),
                "primary_period": primary_period,
                "period": footnote_period,
                "basis": period_basis,
            },
        )
    if period_basis == "absent":
        # 旧格式附注包（连 `资料截止日` 都没有）期次判不了，但也不能凭空告警：
        # 「声明报告期」是本次新加的契约，历史包不会被追溯判红。
        return [], None
    # 有元数据但推不出期次：H1/Q1/Q3 run 拿到的年报附注包一定不是本期的，
    # 不能静默（F29 的静默形态）。声明存在但格式不对时同样要走这里。
    if primary_period.endswith("FY") and period_basis != "invalid":
        return [], None
    hint = (
        f"{pack_path.name} 的 `报告期` 行无法解析（应形如 `> 报告期：{primary_period}`）"
        if period_basis == "invalid"
        else f"{pack_path.name} 头部没有可判定的 `报告期` / `资料截止日`"
    )
    return (
        [
            f"附注源期次无法确定（period unverified）：primary_period={primary_period}，而 {hint}，"
            "prepare 无法证明它与本 run 同期。"
            f"请在附注包头部补 `> 报告期：{primary_period}`（见 prompts/phase2_PDF解析.md），"
            "或改用本期附注包（中报用 data_pack_report_interim.md）。"
        ],
        {
            "source_id": "pdf_footnotes",
            "path": str(pack_path),
            "primary_period": primary_period,
            "period": "",
            "basis": period_basis or "unknown",
        },
    )


def _resolve_footnote_source(inputs_root: Path) -> tuple[Path, list[str]]:
    """解析附注证据源；两个文件名都认，都不存在时给出**显式 warning**。"""

    for name in FOOTNOTE_SOURCE_NAMES:
        candidate = inputs_root / name
        if candidate.is_file():
            return candidate, []
    return (
        inputs_root / FOOTNOTE_SOURCE_NAMES[0],
        [
            "附注证据源缺失（not_applicable）：在 "
            f"{inputs_root} 下找不到 {' 或 '.join(FOOTNOTE_SOURCE_NAMES)}；"
            "本 run 的证据链没有附注来源（pdf_footnotes 在 evidence_coverage 里会是 missing）。"
            "请把它加进 `runs.py new --input`，或确认本期不需要附注证据。"
        ],
    )


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
    max_chars: int | None = None,
    primary_period: str | None = None,
    prior_analysis: str | Path | None = None,
) -> dict[str, object]:
    """Create all deterministic inputs needed by module Agents.

    ``max_chars=None``（默认）让每个模块用它自己的字符预算（``MODULE_CONFIG``；
    period_delta 因为要同时装下必选节与完整 prior_analysis 而更大，见 AC-2.5）。

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
    annual_reports = sorted(inputs_root.glob("*.pdf"))
    pdf_sections, warnings = _select_pdf_sections(inputs_root, normalized_primary)
    footnote_report, footnote_warnings = _resolve_footnote_source(inputs_root)
    warnings.extend(footnote_warnings)
    # 附注源的期次必须可登记、可判定（REQ-006.2 发现 F29）。
    footnote_source_period: tuple[str, str] | None = None
    period_mismatches: list[dict[str, str]] = []
    if footnote_report.is_file():
        footnote_source_period = _footnote_period(footnote_report)
        period_warnings, mismatch = _footnote_period_diagnostics(
            footnote_report,
            footnote_source_period[0],
            footnote_source_period[1],
            normalized_primary,
        )
        warnings.extend(period_warnings)
        if mismatch is not None:
            period_mismatches.append(mismatch)

    prior_analysis_path = Path(prior_analysis) if prior_analysis else None
    if prior_analysis_path is not None and not prior_analysis_path.is_file():
        warnings.append(f"prior analysis not found: {prior_analysis_path}")
        prior_analysis_path = None

    sources = [
        {"source_id": "market_data", "path": str(data_pack)},
        {"source_id": "pdf_sections", "path": str(pdf_sections)},
        {"source_id": "pdf_footnotes", "path": str(footnote_report)},
    ]
    # 未选中的附注候选也要登记：inputs/ 里**事后**出现（或替换）附注包时，
    # `validate_manifest_inputs` 才会发现 run 的输入变了。只登记选中的那一个的话，
    # 「先 prepare 年报 run、再补中报附注包」这类改动会静默失效。
    for candidate_name in FOOTNOTE_SOURCE_NAMES:
        candidate = inputs_root / candidate_name
        if candidate != footnote_report:
            sources.append(
                {"source_id": f"pdf_footnotes:{candidate_name}", "path": str(candidate)}
            )
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
        if source["source_id"] == "pdf_footnotes" and footnote_source_period is not None:
            # 只有附注源支持「声明 + 推断」两级口径，故加一个 basis 字段说明期次是怎么来的。
            item["period"] = footnote_source_period[0]
            item["period_basis"] = footnote_source_period[1]
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
        if source_meta.get("source_id") == "pdf_footnotes" and footnote_source_period is not None:
            source_meta["period"] = footnote_source_period[0]
            source_meta["period_basis"] = footnote_source_period[1]
    # 期次不匹配必须随证据索引一起走：模块 bundle 只读索引，看不到 run_manifest
    # （REQ-006.2 发现 F29 —— 附注源是 2025FY、run 是 2026H1，却被当同期数据引用）。
    if period_mismatches:
        evidence_index["period_mismatches"] = period_mismatches
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
    # 缺失的输入必须**机器可读地**登记原因，而不是只让模块在 evidence_coverage
    # 里看到一个 `missing`（REQ-006.2 AC-2.6）。
    missing_reasons = {
        "pdf_footnotes": (
            "附注证据源缺失：把 data_pack_report.md（中报 data_pack_report_interim.md）"
            "加进 `runs.py new --input`，或确认本期不需要附注证据"
        ),
    }
    unavailable_inputs = [
        {
            "source_id": item.get("source_id"),
            "path": item.get("path"),
            "reason": missing_reasons.get(item.get("source_id"), "输入文件不存在"),
        }
        for item in input_paths
        # ``pdf_footnotes:<filename>`` 是「未选中的附注候选」，只用于监测输入变更，
        # 不是本 run 的证据源，缺了也不代表降级，所以不进 unavailable_inputs。
        if not item.get("exists")
        and not str(item.get("source_id", "")).startswith("pdf_footnotes:")
    ]
    manifest["unavailable_inputs"] = unavailable_inputs
    # 期次不匹配与「源缺失」是两件事：文件在、内容也在，但**期次不对**。
    # 单独给一个机器可读字段，不让它只活在一句 warning 文本里（REQ-006.2 发现 F29）。
    if period_mismatches:
        manifest["period_mismatches"] = period_mismatches
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
        "unavailable_inputs": unavailable_inputs,
        "period_mismatches": period_mismatches,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare evidence-first analysis inputs")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--market", default="CN")
    parser.add_argument("--run-id")
    # 不设默认值：None 表示「用每个模块自己的预算」（MODULE_CONFIG，见 AC-2.5）。
    parser.add_argument("--max-chars", type=int, default=None)
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
