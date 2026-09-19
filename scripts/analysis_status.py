#!/usr/bin/env python3
"""Decide whether a company's qualitative analysis is current, stale, or broken.

This is the single decision point that answers "do we need to rerun, and at what
level" (``docs/PERIODIC_UPDATE_PLAN.md`` §8.3). It reads the run-store ledger
(``latest.json`` / ``record.json`` / ``history.jsonl``), the local report PDFs,
the current framework fingerprints and the latest run's input manifest.

Network access is opt-in: ``discover_periods`` is only called with
``--check-upstream`` so the default and the test suite stay fully offline.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:  # Support importing the package with the repository root on sys.path.
    from scripts.periods import filename_to_period
except ImportError:  # Support importing with scripts/ on sys.path.
    from periods import filename_to_period

try:
    from scripts.version import framework_block, schema_versions
except ImportError:  # Support importing with scripts/ on sys.path.
    from version import framework_block, schema_versions

try:
    from scripts.results.manifest import validate_manifest_inputs
except ImportError:  # Support importing with scripts/ on sys.path.
    from results.manifest import validate_manifest_inputs

def _lazy_discover_periods(ticker: str):
    """Import the network-capable discovery module only when actually needed."""

    try:
        from scripts.discover_report import discover_periods as impl
    except ImportError:  # Support importing with scripts/ on sys.path.
        from discover_report import discover_periods as impl
    return impl(ticker)


#: Indirection so the default stays offline and tests can monkeypatch upstream
#: discovery without importing the network module at all.
discover_periods = _lazy_discover_periods


STATE_NO_RECORD = "no_record"
STATE_LEGACY_LAYOUT = "legacy_layout"
STATE_UP_TO_DATE = "up_to_date"
STATE_STALE = "stale"
STATE_BROKEN = "broken"
STATE_UNSUPPORTED_MARKET = "unsupported_market"

ACTION_NONE = "none"
ACTION_REPORT_UPDATE = "report-update"
ACTION_FULL_RERUN = "full-rerun"

#: Files that mark a directory as holding legacy (flat) analysis artifacts.
LEGACY_MARKERS = ("run_manifest.json", "evidence", "contexts", "modules", "synthesis", "qualitative_report.md")

_A_SHARE_SUFFIXES = {"SH", "SZ", "BJ"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _ticker_from_directory(company_dir: Path) -> str | None:
    match = re.match(r"^(\d{5,6})", company_dir.name)
    return match.group(1) if match else None


def _company_from_directory(company_dir: Path) -> str:
    match = re.match(r"^\d{5,6}[_-](.+)$", company_dir.name)
    return match.group(1) if match else company_dir.name


def _looks_like_stock_code(ticker: str | None) -> bool:
    """True when ``ticker`` carries a numeric code or an explicit HK/US suffix."""

    if not ticker:
        return False
    text = str(ticker).strip().upper()
    return bool(re.search(r"\d{5,6}", text)) or text.endswith((".HK", ".US"))


def is_supported_market(ticker: str | None, market: str | None = None) -> bool:
    """Return ``True`` only for A-share (``6xx/0xx/3xx``) subjects."""

    if market and str(market).strip().upper() not in {"CN", "A", "ASHARE", "A-SHARE", "A_SHARE"}:
        return False
    if not ticker:
        return False
    text = str(ticker).strip().upper()
    suffix_match = re.match(r"^(?:[A-Z]{2})?(\d{5,6})(?:\.([A-Z]{2}))?$", text)
    if not suffix_match:
        digits = re.sub(r"\D", "", text)
        return len(digits) == 6 and digits[0] in {"0", "3", "6"}
    code, suffix = suffix_match.groups()
    if suffix and suffix not in _A_SHARE_SUFFIXES:
        return False
    return len(code) == 6 and code[0] in {"0", "3", "6"}


def _subject(company_dir: Path, record: dict[str, Any] | None, ticker: str | None) -> dict[str, Any]:
    base = record.get("subject") if isinstance(record, dict) and isinstance(record.get("subject"), dict) else {}
    resolved_ticker = base.get("ticker") or ticker or _ticker_from_directory(company_dir)
    market = base.get("market")
    if not market and is_supported_market(resolved_ticker):
        market = "CN"
    return {
        "ticker": resolved_ticker,
        "company": base.get("company") or _company_from_directory(company_dir),
        "market": market,
    }


def _reason(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _local_pdf_periods(company_dir: Path) -> list[tuple[str, str]]:
    """Return ``(period, filename)`` for every locally available report PDF."""

    found: list[tuple[str, str]] = []
    seen: set[Path] = set()
    directories = (company_dir / "sources" / "pdf", company_dir / "sources", company_dir)
    for directory in directories:
        if not directory.is_dir():
            continue
        for pdf in sorted(directory.glob("*.pdf")):
            key = pdf.resolve()
            if key in seen:
                continue
            seen.add(key)
            period = filename_to_period(pdf.name)
            if period:
                found.append((period, pdf.name))
    return found


def _upstream_periods(ticker: str | None) -> list[str]:
    """Best-effort live period discovery; only called with ``--check-upstream``."""

    if not ticker:
        return []
    try:
        candidates = discover_periods(ticker)
    except Exception:  # Network/parse failure must not turn into a fake "stale".
        return []
    periods: list[str] = []
    for candidate in candidates or []:
        period = candidate.get("period") if isinstance(candidate, dict) else getattr(candidate, "period", None)
        if isinstance(period, str) and period:
            periods.append(period)
    return periods


def _has_legacy_artifacts(company_dir: Path) -> bool:
    for marker in LEGACY_MARKERS:
        if (company_dir / marker).exists():
            return True
    return any(company_dir.glob("*.pdf"))


def _build_result(
    *,
    state: str,
    action: str,
    reasons: list[dict[str, str]],
    latest_run: str | None,
    primary_period: str | None,
    subject: dict[str, Any],
) -> dict[str, Any]:
    return {
        "subject": subject,
        "state": state,
        "recommended_action": action,
        "reasons": reasons,
        "latest_run": latest_run,
        "primary_period": primary_period,
    }


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------


def evaluate_company(
    company_dir: str | Path,
    *,
    ticker: str | None = None,
    check_upstream: bool = False,
    current_framework: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify one company directory into a state plus recommended action."""

    company_path = Path(company_dir)
    record = _load_json(company_path / "record.json")
    latest = _load_json(company_path / "latest.json")
    subject = _subject(company_path, record, ticker)

    if subject.get("ticker") and not is_supported_market(subject.get("ticker"), subject.get("market")):
        # An explicitly requested ticker is always classified on its market; a
        # ticker merely guessed from a directory name only triggers this when it
        # actually looks like a stock code (so "mycompany/" is not "unsupported").
        if ticker is not None or _looks_like_stock_code(subject.get("ticker")) or subject.get("market"):
            return _build_result(
                state=STATE_UNSUPPORTED_MARKET,
                action=ACTION_NONE,
                reasons=[_reason("unsupported_market", f"only A-share subjects are supported: {subject.get('ticker')}")],
                latest_run=None,
                primary_period=None,
                subject=subject,
            )

    if latest is None and record is None:
        if _has_legacy_artifacts(company_path):
            return _build_result(
                state=STATE_LEGACY_LAYOUT,
                action=ACTION_FULL_RERUN,
                reasons=[_reason("legacy_layout", "flat artifacts without latest.json/record.json; adopt or rerun")],
                latest_run=None,
                primary_period=None,
                subject=subject,
            )
        return _build_result(
            state=STATE_NO_RECORD,
            action=ACTION_FULL_RERUN,
            reasons=[_reason("no_record", "no run-store ledger and no legacy artifacts")],
            latest_run=None,
            primary_period=None,
            subject=subject,
        )

    if latest is None or record is None:
        return _build_result(
            state=STATE_BROKEN,
            action=ACTION_FULL_RERUN,
            reasons=[_reason("incomplete_ledger", "latest.json and record.json must both exist")],
            latest_run=(record or latest or {}).get("latest_run") or (latest or {}).get("run_id"),
            primary_period=(record or {}).get("primary_period") or (latest or {}).get("primary_period"),
            subject=subject,
        )

    latest_run = record.get("latest_run") or latest.get("run_id")
    primary_period = record.get("primary_period") or latest.get("primary_period")
    run_dir_raw = latest.get("run_dir")
    run_dir = Path(run_dir_raw) if isinstance(run_dir_raw, str) and run_dir_raw else company_path / "runs" / str(latest_run)
    if not run_dir.is_absolute():
        run_dir = company_path / run_dir
    run_dir = run_dir.resolve()
    if not run_dir.is_dir() or not (run_dir / "run.json").is_file():
        return _build_result(
            state=STATE_BROKEN,
            action=ACTION_FULL_RERUN,
            reasons=[_reason("run_dir_missing", f"latest run directory is missing or incomplete: {run_dir}")],
            latest_run=latest_run,
            primary_period=primary_period,
            subject=subject,
        )

    reasons: list[dict[str, str]] = []

    covered = set(record.get("report_periods") or [])
    for period, filename in _local_pdf_periods(company_path):
        if period not in covered:
            reasons.append(_reason("new_report", f"{period} present locally as {filename} but not covered by the record"))
    if check_upstream:
        for period in _upstream_periods(subject.get("ticker")):
            if period not in covered:
                reasons.append(_reason("new_report", f"{period} published upstream but not covered by the record"))

    current = current_framework if current_framework is not None else framework_block()
    record_framework = record.get("framework")
    if not isinstance(record_framework, dict):
        reasons.append(_reason("framework_changed", "record has no framework block"))
    else:
        for key in ("version", "prompt_fingerprint", "code_fingerprint"):
            old, new = record_framework.get(key), current.get(key)
            if old != new:
                reasons.append(_reason("framework_changed", f"{key}: {old} -> {new}"))
        if (record_framework.get("schema_versions") or {}) != (current.get("schema_versions") or schema_versions()):
            reasons.append(
                _reason(
                    "schema_changed",
                    f"schema_versions: {record_framework.get('schema_versions')} -> {current.get('schema_versions')}",
                )
            )

    manifest_path = run_dir / "run_manifest.json"
    manifest = _load_json(manifest_path)
    if manifest is not None:
        errors = validate_manifest_inputs(manifest)
        if errors:
            reasons.append(_reason("inputs_changed", "; ".join(errors[:5])))

    if not reasons:
        return _build_result(
            state=STATE_UP_TO_DATE,
            action=ACTION_NONE,
            reasons=[],
            latest_run=latest_run,
            primary_period=primary_period,
            subject=subject,
        )

    codes = {reason["code"] for reason in reasons}
    action = ACTION_REPORT_UPDATE if codes <= {"new_report"} else ACTION_FULL_RERUN
    return _build_result(
        state=STATE_STALE,
        action=action,
        reasons=reasons,
        latest_run=latest_run,
        primary_period=primary_period,
        subject=subject,
    )


def exit_code_for(result: dict[str, Any]) -> int:
    """Map a classification to the documented process exit code."""

    state = result.get("state")
    if state == STATE_UP_TO_DATE:
        return 0
    if state == STATE_STALE and result.get("recommended_action") == ACTION_REPORT_UPDATE:
        return 1
    return 3


# ---------------------------------------------------------------------------
# batch scanning
# ---------------------------------------------------------------------------


def looks_like_company_dir(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    if (directory / "latest.json").exists() or (directory / "record.json").exists():
        return True
    if any((directory / marker).exists() for marker in LEGACY_MARKERS):
        return True
    return any(directory.glob("*.pdf"))


def discover_company_dirs(root: str | Path) -> list[Path]:
    root_path = Path(root)
    if not root_path.is_dir():
        raise NotADirectoryError(f"root is not a directory: {root_path}")
    found: list[Path] = []
    for child in sorted(root_path.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name.startswith("_"):
            continue
        if looks_like_company_dir(child):
            found.append(child)
    return found


def evaluate_root(
    root: str | Path,
    *,
    check_upstream: bool = False,
    ticker: str | None = None,
    current_framework: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate every company directory below ``root`` and summarise it."""

    companies: list[dict[str, Any]] = []
    for company_path in discover_company_dirs(root):
        result = evaluate_company(
            company_path,
            ticker=ticker,
            check_upstream=check_upstream,
            current_framework=current_framework,
        )
        if ticker and (result.get("subject") or {}).get("ticker") != ticker:
            continue
        position = dict(result)
        position["company_dir"] = str(company_path.resolve())
        position["exit_code"] = exit_code_for(result)
        companies.append(position)

    summary = {
        "total": len(companies),
        "stale": sum(1 for item in companies if item["state"] in {STATE_STALE, STATE_LEGACY_LAYOUT, STATE_UNSUPPORTED_MARKET}),
        "up_to_date": sum(1 for item in companies if item["state"] == STATE_UP_TO_DATE),
        "no_record": sum(1 for item in companies if item["state"] == STATE_NO_RECORD),
        "broken": sum(1 for item in companies if item["state"] == STATE_BROKEN),
    }
    return {"companies": companies, "summary": summary}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render_line(result: dict[str, Any], company_dir: str | None = None) -> str:
    subject = result.get("subject") or {}
    reasons = "; ".join(f"{reason['code']}: {reason['detail']}" for reason in result.get("reasons") or [])
    prefix = f"{company_dir} " if company_dir else ""
    return (
        f"{prefix}{subject.get('ticker') or '?'} {result.get('state')} "
        f"action={result.get('recommended_action')} {reasons}".rstrip()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify analysis freshness for one or all companies")
    parser.add_argument("--company-dir")
    parser.add_argument("--ticker")
    parser.add_argument("--root")
    parser.add_argument("--all", action="store_true", dest="scan_all")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check-upstream", action="store_true")
    args = parser.parse_args(argv)

    if args.scan_all:
        if not args.root:
            parser.error("--all requires --root")
        root_path = Path(args.root)
        if not root_path.is_dir():
            parser.error(f"root is not a directory: {root_path}")
        payload = evaluate_root(root_path, check_upstream=args.check_upstream, ticker=args.ticker)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for item in payload["companies"]:
                print(_render_line(item, item["company_dir"]))
            summary = payload["summary"]
            print(
                "total={total} up_to_date={up_to_date} stale={stale} no_record={no_record} broken={broken}".format(**summary)
            )
        codes = [item["exit_code"] for item in payload["companies"]]
        return max(codes) if codes else 0

    if not args.company_dir:
        parser.error("--company-dir is required unless --root --all is used")
    company_path = Path(args.company_dir)
    if not company_path.is_dir():
        parser.error(f"company directory is not a directory: {company_path}")
    result = evaluate_company(company_path, ticker=args.ticker, check_upstream=args.check_upstream)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_render_line(result, str(company_path.resolve())))
    return exit_code_for(result)


if __name__ == "__main__":
    raise SystemExit(main())
