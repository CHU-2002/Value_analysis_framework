#!/usr/bin/env python3
"""Run-store ledger for qualitative analysis runs (``docs/PERIODIC_UPDATE_PLAN.md`` §3).

A company directory keeps three small ledgers plus immutable run directories::

    <company_dir>/
      latest.json      # authoritative pointer to the currently effective run
      record.json      # human/tool readable analysis record card
      history.jsonl    # append-only one-JSON-object-per-run ledger
      runs/<run_id>/
        run.json       # immutable run metadata (kind / subject / period / framework)
        inputs/        # per-run input snapshots (hardlink first, copy fallback)

This module only ever writes inside the ``--company-dir`` it is given. It is
standard library only and is usable both as a CLI and as importable functions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:  # Support importing the package with the repository root on sys.path.
    from scripts.periods import filename_to_period, is_valid_period, sort_periods
except ImportError:  # Support importing with scripts/ on sys.path.
    from periods import filename_to_period, is_valid_period, sort_periods

try:
    from scripts.version import framework_block
except ImportError:  # Support importing with scripts/ on sys.path.
    from version import framework_block

try:
    from scripts.results.manifest import input_set_digest, sha256_file
except ImportError:  # Support importing with scripts/ on sys.path.
    from results.manifest import input_set_digest, sha256_file


LATEST_SCHEMA = "investment.latest"
RECORD_SCHEMA = "investment.record"
RUN_SCHEMA = "investment.run"
SOURCES_SCHEMA = "investment.sources_manifest"
LEDGER_SCHEMA_VERSION = "1.0"

RUN_KINDS = ("baseline", "report-update", "framework-update", "market-refresh", "rerun")
RUN_STATUSES = ("complete", "partial", "failed")

#: Flat legacy artifacts that ``adopt`` copies into ``runs/<baseline_id>/``.
FLAT_DIRECTORIES = ("evidence", "contexts", "modules", "synthesis")
FLAT_FILES = (
    "run_manifest.json",
    "d6_trigger.json",
    "qualitative_report.md",
    "qualitative_input.json",
    "data_pack_market.md",
    "data_pack_report.md",
)
FLAT_GLOBS = ("*.pdf", "pdf_sections*.json")

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class LedgerError(Exception):
    """Invalid arguments or an invalid ledger operation (exit code 2)."""


class DuplicateRunError(LedgerError):
    """A run id already exists in the append-only ledger (exit code 2)."""


class ResolutionError(Exception):
    """The requested run cannot be resolved (exit code 3)."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def utc_now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def iso_timestamp(moment: datetime | None = None) -> str:
    return utc_now(moment).isoformat()


def timestamp_run_id(now: datetime | None = None) -> str:
    return utc_now(now).strftime("%Y%m%dT%H%M%S%fZ")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _validate_run_id(run_id: str) -> str:
    candidate = (run_id or "").strip()
    if not candidate or not _RUN_ID_RE.match(candidate) or candidate in {".", ".."}:
        raise LedgerError(f"invalid run id: {run_id!r}")
    return candidate


def _validate_period(period: str | None) -> str | None:
    if period is None or period == "":
        return None
    if not is_valid_period(period):
        raise LedgerError(f"invalid primary period: {period!r}")
    return period.strip().upper()


def _parse_periods(values: Iterable[str] | None) -> list[str]:
    periods: list[str] = []
    for value in values or ():
        for chunk in str(value).split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if not is_valid_period(chunk):
                raise LedgerError(f"invalid report period: {chunk!r}")
            periods.append(chunk.strip().upper())
    return sort_periods(periods, reverse=False)


def _unique_destination(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def _snapshot_file(source: Path, destination: Path, *, hardlink: bool = False) -> str:
    """Freeze ``source`` into ``destination`` and return the method used.

    The default is a real ``shutil.copy2`` copy. Hardlinks are *not* safe by
    default because this repository refreshes shared artifacts in place
    (``pdf_preprocessor`` / ``tushare_collector`` open the same path with ``"w"``,
    ``download_report`` may ``os.rename`` over a PDF), which would silently
    rewrite the "immutable" run snapshot through the shared inode. A hardlink is
    only attempted when explicitly requested via ``hardlink=True``.
    """

    if hardlink:
        try:
            os.link(source, destination)
            return "hardlink"
        except OSError:
            pass
    shutil.copy2(source, destination)
    return "copy"


def _snapshot_digest(run_path: Path) -> str | None:
    manifest = _read_json(run_path / "inputs" / "sources_manifest.json")
    if not manifest:
        return None
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        return None
    stable = [
        {
            "snapshot_path": item.get("snapshot_path"),
            "sha256": item.get("sha256"),
            "size_bytes": item.get("size_bytes"),
        }
        for item in sources
        if isinstance(item, dict)
    ]
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _history_lines(company_dir: Path) -> list[str]:
    history_path = company_dir / "history.jsonl"
    if not history_path.is_file():
        return []
    return [line for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_history(company_dir: str | Path) -> list[dict[str, Any]]:
    """Return every parsed ledger entry (malformed lines are skipped)."""

    entries: list[dict[str, Any]] = []
    for line in _history_lines(Path(company_dir)):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def read_latest(company_dir: str | Path) -> dict[str, Any] | None:
    return _read_json(Path(company_dir) / "latest.json")


def read_record(company_dir: str | Path) -> dict[str, Any] | None:
    return _read_json(Path(company_dir) / "record.json")


def latest_history_entry(company_dir: str | Path) -> dict[str, Any] | None:
    entries = read_history(company_dir)
    return entries[-1] if entries else None


# ---------------------------------------------------------------------------
# new
# ---------------------------------------------------------------------------


def create_run(
    company_dir: str | Path,
    *,
    ticker: str,
    company: str,
    market: str = "CN",
    kind: str = "baseline",
    primary_period: str | None = None,
    supersedes: str | None = None,
    inputs: Iterable[str | Path] = (),
    run_id: str | None = None,
    framework: dict[str, Any] | None = None,
    hardlink: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create an immutable run directory and snapshot its inputs.

    Input snapshots are real copies by default (see :func:`_snapshot_file`);
    ``hardlink=True`` is an explicit opt-in for callers who know the sources are
    never rewritten in place.
    """

    if kind not in RUN_KINDS:
        raise LedgerError(f"unknown run kind: {kind!r} (expected one of {', '.join(RUN_KINDS)})")
    period = _validate_period(primary_period)
    company_path = Path(company_dir).resolve()
    company_path.mkdir(parents=True, exist_ok=True)

    resolved_run_id = _validate_run_id(run_id) if run_id else timestamp_run_id(now)
    run_path = company_path / "runs" / resolved_run_id
    if (run_path / "run.json").exists() or run_path.exists():
        raise DuplicateRunError(f"run already exists: {run_path}")

    # Validate every input *before* creating anything, so a bad invocation leaves
    # no half-built run directory behind and does not burn the run id.
    resolved_inputs: list[Path] = []
    for item in inputs:
        source = Path(item).expanduser()
        if not source.is_file():
            raise LedgerError(f"input is not a regular file: {source}")
        resolved_inputs.append(source.resolve())

    inputs_dir = run_path / "inputs"
    snapshots: list[dict[str, Any]] = []
    created_at = iso_timestamp(now)
    try:
        inputs_dir.mkdir(parents=True, exist_ok=True)
        for source in resolved_inputs:
            destination = _unique_destination(inputs_dir, source.name)
            method = _snapshot_file(source, destination, hardlink=hardlink)
            snapshots.append(
                {
                    "source_path": str(source),
                    "snapshot_path": str(destination),
                    "sha256": sha256_file(destination),
                    "size_bytes": destination.stat().st_size,
                    "hardlinked": method == "hardlink",
                    "method": method,
                }
            )

        if snapshots:
            _write_json(
                inputs_dir / "sources_manifest.json",
                {
                    "schema": SOURCES_SCHEMA,
                    "schema_version": LEDGER_SCHEMA_VERSION,
                    "run_id": resolved_run_id,
                    "created_at": created_at,
                    "sources": snapshots,
                },
            )

        framework_meta = framework if framework is not None else framework_block()
        run_meta = {
            "schema": RUN_SCHEMA,
            "schema_version": LEDGER_SCHEMA_VERSION,
            "run_id": resolved_run_id,
            "kind": kind,
            "created_at": created_at,
            "subject": {"ticker": ticker, "company": company, "market": market},
            "primary_period": period,
            "supersedes": supersedes,
            "framework": framework_meta,
        }
        _write_json(run_path / "run.json", run_meta)
    except OSError as exc:
        shutil.rmtree(run_path, ignore_errors=True)
        raise LedgerError(f"failed to snapshot run inputs: {exc}") from exc

    return {
        "company_dir": str(company_path),
        "run_id": resolved_run_id,
        "run_dir": str(run_path),
        "run_json": str(run_path / "run.json"),
        "sources_manifest": str(inputs_dir / "sources_manifest.json") if snapshots else None,
        "sources": snapshots,
        "run": run_meta,
    }


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def resolve_run(
    company_dir: str | Path,
    *,
    run_id: str | None = None,
    latest: bool = False,
) -> Path:
    """Return the absolute directory of the requested run.

    ``run_id`` takes precedence; otherwise ``latest.json`` is consulted (which
    is also the default when neither is supplied).
    """

    company_path = Path(company_dir).resolve()
    if run_id:
        candidate = company_path / "runs" / _validate_run_id(run_id)
        if not candidate.is_dir():
            raise ResolutionError(f"run not found: {run_id}")
        return candidate
    pointer = read_latest(company_path)
    if not pointer:
        raise ResolutionError(f"no latest.json pointer in {company_path}")
    run_dir = pointer.get("run_dir")
    if not isinstance(run_dir, str) or not run_dir:
        raise ResolutionError(f"latest.json has no run_dir: {company_path / 'latest.json'}")
    candidate = Path(run_dir)
    if not candidate.is_absolute():
        candidate = company_path / candidate
    candidate = candidate.resolve()
    if not candidate.is_dir():
        raise ResolutionError(f"latest run directory is missing: {candidate}")
    return candidate


# ---------------------------------------------------------------------------
# finish
# ---------------------------------------------------------------------------


def _parse_artifacts(run_path: Path, artifacts: Iterable[str] | dict[str, str] | None) -> dict[str, str]:
    resolved: dict[str, str] = {}
    if artifacts is None:
        return resolved
    if isinstance(artifacts, dict):
        items = artifacts.items()
    else:
        parsed: list[tuple[str, str]] = []
        for value in artifacts:
            if "=" not in value:
                raise LedgerError(f"--artifact expects name=path, got: {value!r}")
            name, path = value.split("=", 1)
            parsed.append((name.strip(), path.strip()))
        items = parsed
    for name, path in items:
        if not name or not path:
            raise LedgerError("--artifact expects a non-empty name and path")
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = run_path / candidate
        resolved[name] = str(candidate.resolve())
    return resolved


def finish_run(
    company_dir: str | Path,
    run_dir: str | Path,
    *,
    status: str = "complete",
    primary_period: str | None = None,
    artifacts: Iterable[str] | dict[str, str] | None = None,
    conclusions_changed: Iterable[str] = (),
    report_periods: Iterable[str] | None = None,
    trigger_type: str = "manual",
    trigger_periods: Iterable[str] | None = None,
    force: bool = False,
    framework: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record a finished run in ``history.jsonl`` + ``latest.json`` + ``record.json``."""

    if status not in RUN_STATUSES:
        raise LedgerError(f"unknown run status: {status!r} (expected one of {', '.join(RUN_STATUSES)})")
    company_path = Path(company_dir).resolve()
    company_path.mkdir(parents=True, exist_ok=True)
    run_path = Path(run_dir)
    if not run_path.is_absolute():
        # Accept both a cwd-relative path and a company-dir-relative path.
        cwd_candidate = Path.cwd() / run_path
        run_path = cwd_candidate if cwd_candidate.exists() else company_path / run_path
    run_path = run_path.resolve()
    runs_root = (company_path / "runs").resolve()
    try:
        run_path.relative_to(runs_root)
    except ValueError as exc:
        raise LedgerError(f"run directory must live under {runs_root}: {run_path}") from exc
    run_meta = _read_json(run_path / "run.json")
    if not run_meta:
        raise LedgerError(f"run.json is missing or invalid: {run_path / 'run.json'}")

    run_id = _validate_run_id(str(run_meta.get("run_id") or run_path.name))
    period = _validate_period(primary_period) if primary_period else _validate_period(run_meta.get("primary_period"))
    periods = _parse_periods(report_periods)
    if not periods:
        periods = _parse_periods(run_meta.get("report_periods") or ())
    if not periods and period:
        periods = [period]

    artifact_map = _parse_artifacts(run_path, artifacts)
    framework_meta = framework if framework is not None else framework_block()
    subject = run_meta.get("subject") if isinstance(run_meta.get("subject"), dict) else {}
    moment = utc_now(now)
    entry = {
        "run_id": run_id,
        "kind": run_meta.get("kind", "baseline"),
        "created_at": run_meta.get("created_at") or iso_timestamp(moment),
        "supersedes": run_meta.get("supersedes"),
        "trigger": {
            "type": trigger_type,
            "periods": _parse_periods(trigger_periods) or list(periods),
            "detected_by": "runs.finish",
        },
        "subject": subject,
        "report_periods": periods,
        "primary_period": period,
        "framework": framework_meta,
        "input_digest": _snapshot_digest(run_path),
        "status": status,
        "conclusions_changed": [str(item) for item in conclusions_changed],
        "artifacts": artifact_map,
        "recorded_at": iso_timestamp(moment),
    }

    existing = read_history(company_path)
    duplicate = any(item.get("run_id") == run_id for item in existing)
    if duplicate and not force:
        raise DuplicateRunError(f"run id already recorded in history.jsonl: {run_id}")

    if duplicate:  # --force replaces the existing line, keeping append-only ordering.
        raw_lines = [
            line
            for line in _history_lines(company_path)
            if not _line_matches_run(line, run_id)
        ]
    else:
        raw_lines = _history_lines(company_path)
    raw_lines.append(json.dumps(entry, ensure_ascii=False))
    history_path = company_path / "history.jsonl"
    temporary = history_path.with_name(history_path.name + ".tmp")
    temporary.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")
    os.replace(temporary, history_path)

    needs_refresh = status != "complete" or entry["kind"] != "baseline"
    downstream = {
        "stale": needs_refresh,
        "value_computed": needs_refresh,
        "buy_sell_basis": needs_refresh,
    }

    latest = {
        "schema": LATEST_SCHEMA,
        "schema_version": LEDGER_SCHEMA_VERSION,
        "run_id": run_id,
        "primary_period": period,
        "published_at": iso_timestamp(moment),
        "run_dir": str(run_path),
        "artifacts": artifact_map,
    }
    _write_json(company_path / "latest.json", latest)

    record = {
        "schema": RECORD_SCHEMA,
        "schema_version": LEDGER_SCHEMA_VERSION,
        "subject": subject,
        "latest_run": run_id,
        "kind": entry["kind"],
        "status": status,
        "report_periods": periods,
        "primary_period": period,
        "framework": framework_meta,
        "downstream": downstream,
        "updated_at": iso_timestamp(moment),
    }
    _write_json(company_path / "record.json", record)

    return {
        "company_dir": str(company_path),
        "run_id": run_id,
        "run_dir": str(run_path),
        "entry": entry,
        "latest": latest,
        "record": record,
        "replaced": bool(duplicate),
    }


def _line_matches_run(line: str, run_id: str) -> bool:
    try:
        payload = json.loads(line)
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("run_id") == run_id


#: Downstream consumers that a new report/framework run marks stale.
DOWNSTREAM_COMPONENTS = ("value_computed", "buy_sell_basis")


def mark_downstream_fresh(
    company_dir: str | Path,
    *,
    components: Iterable[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mark downstream components fresh again after they have been refreshed.

    A new report or framework run sets every component's *stale* flag to
    ``True`` (``record.json:downstream``). After ``/value-analysis`` or
    ``/buy-sell-plan`` reruns, this clears the corresponding flags, so
    ``analysis_status`` can return to ``up_to_date``. The aggregate
    ``downstream.stale`` is ``True`` while any component is still stale.
    """

    company_path = Path(company_dir).resolve()
    record = read_record(company_path)
    if not record:
        raise ResolutionError(f"no record.json in {company_path}")
    selected = list(DOWNSTREAM_COMPONENTS if components is None else components)
    unknown = [name for name in selected if name not in DOWNSTREAM_COMPONENTS]
    if unknown:
        raise LedgerError(
            f"unknown downstream component(s): {', '.join(unknown)} "
            f"(expected one of {', '.join(DOWNSTREAM_COMPONENTS)})"
        )
    downstream = record.get("downstream")
    downstream = dict(downstream) if isinstance(downstream, dict) else {}
    for name in selected:
        downstream[name] = False
    downstream["stale"] = any(bool(downstream.get(name)) for name in DOWNSTREAM_COMPONENTS)
    record["downstream"] = downstream
    record["updated_at"] = iso_timestamp(now)
    _write_json(company_path / "record.json", record)
    return record


# ---------------------------------------------------------------------------
# adopt
# ---------------------------------------------------------------------------


def infer_report_periods(company_dir: str | Path) -> list[str]:
    """Infer covered periods from PDF names and ``pdf_sections*.json`` metadata.

    A ``metadata.pdf_file`` clue is only trusted when that PDF actually exists in
    the directory; otherwise a stale or edited sections file would invent a
    phantom period that was never analyzed.
    """

    company_path = Path(company_dir)
    periods: set[str] = set()
    pdf_names: set[str] = set()
    for pdf in sorted(company_path.glob("*.pdf")):
        pdf_names.add(pdf.name)
        period = filename_to_period(pdf.name)
        if period:
            periods.add(period)
    for sections in sorted(company_path.glob("pdf_sections*.json")):
        payload = _read_json(sections)
        if not payload:
            continue
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            continue
        pdf_file = metadata.get("pdf_file")
        if isinstance(pdf_file, str) and pdf_file in pdf_names:
            period = filename_to_period(pdf_file)
            if period:
                periods.add(period)
    return sort_periods(periods, reverse=False)


def _legacy_items(company_dir: Path) -> list[Path]:
    items: list[Path] = []
    for name in FLAT_FILES:
        candidate = company_dir / name
        if candidate.is_file():
            items.append(candidate)
    for name in FLAT_DIRECTORIES:
        candidate = company_dir / name
        if candidate.is_dir():
            items.append(candidate)
    for pattern in FLAT_GLOBS:
        for candidate in sorted(company_dir.glob(pattern)):
            if candidate.is_file() and candidate not in items:
                items.append(candidate)
    return items


def _copy_item(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _verify_copy(source: Path, destination: Path) -> None:
    if source.is_dir():
        if not destination.is_dir():
            raise LedgerError(f"adopt verification failed: missing directory {destination}")
        source_files = {
            path.relative_to(source).as_posix(): path for path in source.rglob("*") if path.is_file()
        }
        destination_files = {
            path.relative_to(destination).as_posix(): path
            for path in destination.rglob("*")
            if path.is_file()
        }
        if set(source_files) != set(destination_files):
            raise LedgerError(f"adopt verification failed: file set differs for {destination}")
        for relative, source_file in source_files.items():
            if sha256_file(source_file) != sha256_file(destination_files[relative]):
                raise LedgerError(f"adopt verification failed: content differs for {relative}")
        return
    if not destination.is_file():
        raise LedgerError(f"adopt verification failed: missing file {destination}")
    if sha256_file(source) != sha256_file(destination):
        raise LedgerError(f"adopt verification failed: content differs for {destination}")


def _remap_to_run(raw: Any, run_path: Path, company_root: Path) -> Path | None:
    """Return the run-local copy of a company-dir path, if one exists."""

    if not isinstance(raw, str):
        return None
    try:
        relative = Path(raw).resolve().relative_to(company_root)
    except (OSError, ValueError):
        return None
    candidate = run_path / relative
    return candidate if candidate.is_file() else None


def _rewrite_manifest_paths(run_path: Path, company_dir: Path) -> str | None:
    """Point a copied legacy manifest at run-local copies and return its digest.

    Rewrites ``inputs[].path`` (recomputing ``input_digest``) and
    ``artifacts[].path``. The returned digest must then be propagated into the
    evidence index and context bundles, and the artifact hashes re-stamped, or
    ``resolve_qualitative`` will reject the adopted run.
    """

    manifest_path = run_path / "run_manifest.json"
    manifest = _read_json(manifest_path)
    if not manifest:
        return None
    company_root = company_dir.resolve()

    def _remap(entry: Any) -> Any:
        candidate = _remap_to_run(entry.get("path") if isinstance(entry, dict) else None, run_path, company_root)
        if candidate is None:
            return entry
        entry = dict(entry)
        entry["path"] = str(candidate)
        return entry

    inputs = manifest.get("inputs")
    if isinstance(inputs, list):
        remapped = [_remap(item) for item in inputs]
        if remapped != inputs:
            manifest["inputs"] = remapped
            manifest["input_digest"] = input_set_digest(
                [item for item in remapped if isinstance(item, dict)]
            )
    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, list):
        manifest["artifacts"] = [_remap(item) for item in artifacts]

    _write_json(manifest_path, manifest)
    digest = manifest.get("input_digest")
    return digest if isinstance(digest, str) and digest else None


def _propagate_input_digest(run_path: Path, input_digest: str, company_dir: Path) -> set[Path]:
    """Re-stamp the new input digest into the copied evidence index/contexts.

    ``prepare`` embeds ``input_digest`` in ``evidence/index.json`` and every
    ``contexts/*.json``; ``resolve_qualitative`` cross-checks the evidence index
    against the manifest, so adopting a run without this step makes it
    unconsumable. Returns the resolved paths that were actually rewritten.
    """

    company_root = company_dir.resolve()
    rewritten: set[Path] = set()
    evidence_path = run_path / "evidence" / "index.json"
    evidence = _read_json(evidence_path)
    if evidence is not None:
        evidence["input_digest"] = input_digest
        sources = evidence.get("sources")
        if isinstance(sources, list):
            remapped_sources = []
            for item in sources:
                if not isinstance(item, dict):
                    remapped_sources.append(item)
                    continue
                candidate = _remap_to_run(item.get("path"), run_path, company_root)
                if candidate is not None:
                    item = dict(item)
                    item["path"] = str(candidate)
                remapped_sources.append(item)
            evidence["sources"] = remapped_sources
        _write_json(evidence_path, evidence)
        rewritten.add(evidence_path.resolve())

    contexts_dir = run_path / "contexts"
    if contexts_dir.is_dir():
        for context_path in sorted(contexts_dir.glob("*.json")):
            bundle = _read_json(context_path)
            if bundle is None:
                continue
            bundle["input_digest"] = input_digest
            _write_json(context_path, bundle)
            rewritten.add(context_path.resolve())
    return rewritten


def _restamp_manifest_artifacts(run_path: Path, rewritten: set[Path]) -> None:
    """Re-stamp only the artifacts this migration actually rewrote.

    Artifacts that were merely copied keep their recorded hashes, so
    ``validate_manifest_artifacts`` still surfaces pre-existing tampering
    instead of having it laundered by the adoption.
    """

    manifest_path = run_path / "run_manifest.json"
    manifest = _read_json(manifest_path)
    if not manifest:
        return
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return
    restamped = []
    for item in artifacts:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            path = Path(item["path"])
            if path.is_file() and path.resolve() in rewritten:
                item = dict(item)
                item["size_bytes"] = path.stat().st_size
                item["sha256"] = sha256_file(path)
        restamped.append(item)
    manifest["artifacts"] = restamped
    _write_json(manifest_path, manifest)


def adopt_legacy(
    company_dir: str | Path,
    *,
    run_id: str | None = None,
    prune: bool = False,
    framework: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Adopt an existing flat analysis directory as the baseline run.

    Copying is the default; originals are only removed by ``prune`` and only
    after every copied file is verified to match byte-for-byte.
    """

    company_path = Path(company_dir).resolve()
    if not company_path.is_dir():
        raise LedgerError(f"company directory does not exist: {company_path}")
    if (company_path / "latest.json").exists():
        raise LedgerError(f"directory already has a run-store ledger: {company_path}")

    items = _legacy_items(company_path)
    if not items:
        raise LedgerError(f"no legacy artifacts found to adopt in {company_path}")

    manifest_meta = _read_json(company_path / "run_manifest.json") or {}
    legacy_run_id = manifest_meta.get("run_id")
    legacy_run_id = legacy_run_id if isinstance(legacy_run_id, str) and legacy_run_id else None
    if run_id and legacy_run_id and run_id != legacy_run_id:
        # The copied manifest / evidence index / module results all embed the
        # legacy run id; overriding it here would split the run's identity.
        raise LedgerError(
            f"run id {run_id!r} conflicts with legacy manifest run id {legacy_run_id!r}; "
            "omit --run-id to adopt under the recorded identity"
        )
    resolved_run_id = (
        _validate_run_id(run_id)
        if run_id
        else _validate_run_id(legacy_run_id)
        if legacy_run_id
        else timestamp_run_id(now)
    )
    run_path = company_path / "runs" / resolved_run_id
    if run_path.exists():
        raise DuplicateRunError(f"run already exists: {run_path}")
    run_path.mkdir(parents=True)

    copied: list[tuple[Path, Path]] = []
    for source in items:
        destination = run_path / source.name
        _copy_item(source, destination)
        copied.append((source, destination))
    for source, destination in copied:
        _verify_copy(source, destination)

    input_digest = _rewrite_manifest_paths(run_path, company_path)
    if input_digest:
        rewritten = _propagate_input_digest(run_path, input_digest, company_path)
        _restamp_manifest_artifacts(run_path, rewritten)

    periods = infer_report_periods(company_path)
    primary_period = periods[-1] if periods else None

    subject = manifest_meta.get("subject") if isinstance(manifest_meta.get("subject"), dict) else {}
    if not subject:
        subject = _subject_from_directory(company_path)
    created_at = manifest_meta.get("generated_at") if isinstance(manifest_meta.get("generated_at"), str) else None
    moment = utc_now(now)
    if not created_at:
        created_at = iso_timestamp(moment)

    framework_meta = framework if framework is not None else framework_block()
    run_meta = {
        "schema": RUN_SCHEMA,
        "schema_version": LEDGER_SCHEMA_VERSION,
        "run_id": resolved_run_id,
        "kind": "baseline",
        "created_at": created_at,
        "subject": subject,
        "primary_period": primary_period,
        "supersedes": None,
        "framework": framework_meta,
        "adopted_from": str(company_path),
    }
    _write_json(run_path / "run.json", run_meta)

    if prune:
        for source, _destination in copied:
            if source.is_dir():
                shutil.rmtree(source)
            else:
                source.unlink()

    artifacts: dict[str, str] = {}
    for name, relative in (
        ("manifest", "run_manifest.json"),
        ("report", "qualitative_report.md"),
        ("qualitative_input", "qualitative_input.json"),
    ):
        candidate = run_path / relative
        if candidate.is_file():
            artifacts[name] = str(candidate)

    finish = finish_run(
        company_path,
        run_path,
        status="complete",
        primary_period=primary_period,
        artifacts=artifacts,
        report_periods=periods,
        trigger_type="adopt",
        trigger_periods=periods,
        framework=framework_meta,
        now=moment,
    )
    return {
        "company_dir": str(company_path),
        "run_id": resolved_run_id,
        "run_dir": str(run_path),
        "pruned": bool(prune),
        "copied": [str(destination) for _source, destination in copied],
        "report_periods": periods,
        "primary_period": primary_period,
        "ledger": finish,
    }


def _subject_from_directory(company_dir: Path) -> dict[str, Any]:
    match = re.match(r"^(\d{5,6})[_-]?(.*)$", company_dir.name)
    if match:
        code, company = match.groups()
        return {"ticker": code, "company": company or company_dir.name, "market": "CN"}
    # No A-share code in the directory name: record an explicit unknown subject
    # rather than pretending the directory name is a ticker.
    return {"ticker": None, "company": company_dir.name, "market": None}


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def export_ledger(company_dir: str | Path, *, format: str = "md") -> str:
    """Render a lightweight ledger summary (never PDFs or report bodies)."""

    company_path = Path(company_dir).resolve()
    record = read_record(company_path)
    if not record:
        raise ResolutionError(f"no record.json to export in {company_path}")
    entries = read_history(company_path)
    subject = record.get("subject") if isinstance(record.get("subject"), dict) else {}
    framework = record.get("framework") if isinstance(record.get("framework"), dict) else {}

    if format == "json":
        payload = {
            "company_dir": str(company_path),
            "subject": subject,
            "latest_run": record.get("latest_run"),
            "kind": record.get("kind"),
            "status": record.get("status"),
            "primary_period": record.get("primary_period"),
            "report_periods": record.get("report_periods") or [],
            "framework": framework,
            "downstream": record.get("downstream") or {},
            "runs": [
                {
                    "run_id": entry.get("run_id"),
                    "kind": entry.get("kind"),
                    "created_at": entry.get("created_at"),
                    "status": entry.get("status"),
                    "primary_period": entry.get("primary_period"),
                    "report_periods": entry.get("report_periods") or [],
                    "supersedes": entry.get("supersedes"),
                    "conclusions_changed": entry.get("conclusions_changed") or [],
                    "framework_version": (entry.get("framework") or {}).get("version"),
                    "artifacts": entry.get("artifacts") or {},
                }
                for entry in entries
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
    if format != "md":
        raise LedgerError(f"unknown export format: {format!r}")

    ticker = subject.get("ticker") or company_path.name
    company = subject.get("company") or ""
    lines = [
        f"# {ticker} {company} · 分析台账".rstrip(),
        "",
        f"- 公司目录：`{company_path}`",
        f"- 最新 run：`{record.get('latest_run')}`（{record.get('kind')} / {record.get('status')}）",
        f"- 主期次：{record.get('primary_period')}",
        f"- 覆盖期次：{', '.join(record.get('report_periods') or []) or '—'}",
        f"- 框架版本：{framework.get('version')}（git {framework.get('git_commit')}，dirty={framework.get('dirty')}）",
        f"- 提示词指纹：{framework.get('prompt_fingerprint')}",
        f"- 下游需刷新：{(record.get('downstream') or {}).get('stale')}",
        "",
        "## Run 台账",
        "",
        "| run_id | kind | created_at | status | primary_period | 结论变化 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in entries:
        changes = "; ".join(entry.get("conclusions_changed") or []) or "—"
        lines.append(
            f"| {entry.get('run_id')} | {entry.get('kind')} | {entry.get('created_at')} | "
            f"{entry.get('status')} | {entry.get('primary_period')} | {changes} |"
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run-store ledger for analysis runs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="create an immutable run directory")
    new_parser.add_argument("--company-dir", required=True)
    new_parser.add_argument("--ticker", required=True)
    new_parser.add_argument("--company", required=True)
    new_parser.add_argument("--market", default="CN")
    new_parser.add_argument("--kind", default="baseline", choices=RUN_KINDS)
    new_parser.add_argument("--primary-period")
    new_parser.add_argument("--supersedes")
    new_parser.add_argument("--input", action="append", default=[], help="file to snapshot into inputs/")
    new_parser.add_argument("--run-id")
    new_parser.add_argument(
        "--hardlink",
        action="store_true",
        help=(
            "opt in to hardlink snapshots instead of copying; only safe when the sources are "
            "never rewritten in place (copies cost ~20-25 ms/MB, so the default is cheap)"
        ),
    )

    resolve_parser = subparsers.add_parser("resolve", help="print a run directory path")
    resolve_parser.add_argument("--company-dir", required=True)
    resolve_parser.add_argument("--latest", action="store_true")
    resolve_parser.add_argument("--run-id")

    finish_parser = subparsers.add_parser("finish", help="record a finished run in the ledger")
    finish_parser.add_argument("--company-dir", required=True)
    finish_parser.add_argument("--run-dir", required=True)
    finish_parser.add_argument("--status", default="complete", choices=RUN_STATUSES)
    finish_parser.add_argument("--primary-period")
    finish_parser.add_argument("--artifact", action="append", default=[], help="name=path")
    finish_parser.add_argument("--conclusion-changed", action="append", default=[])
    finish_parser.add_argument("--report-periods", help="comma separated periods")
    finish_parser.add_argument("--trigger-type", default="manual")
    finish_parser.add_argument("--trigger-period", action="append", default=[])
    finish_parser.add_argument("--force", action="store_true")

    adopt_parser = subparsers.add_parser("adopt", help="adopt a flat directory as the baseline run")
    adopt_parser.add_argument("--company-dir", required=True)
    adopt_parser.add_argument("--run-id")
    adopt_parser.add_argument("--prune", action="store_true")

    export_parser = subparsers.add_parser("export", help="print a lightweight ledger summary")
    export_parser.add_argument("--company-dir", required=True)
    export_parser.add_argument("--format", default="md", choices=("md", "json"))

    downstream_parser = subparsers.add_parser(
        "downstream",
        help="mark downstream consumers fresh after /value-analysis or /buy-sell-plan reruns",
        description=(
            "A new report or framework run marks downstream consumers stale "
            "(record.json:downstream). After rerunning /value-analysis or "
            "/buy-sell-plan, use this command to clear the matching flags; once "
            "every component is fresh, analysis_status returns to up_to_date."
        ),
    )
    downstream_parser.add_argument("--company-dir", required=True)
    downstream_parser.add_argument(
        "--fresh",
        default="all",
        help="comma separated components to mark fresh: value_computed,buy_sell_basis or all (default: all)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "new":
            result = create_run(
                args.company_dir,
                ticker=args.ticker,
                company=args.company,
                market=args.market,
                kind=args.kind,
                primary_period=args.primary_period,
                supersedes=args.supersedes,
                inputs=args.input,
                run_id=args.run_id,
                hardlink=args.hardlink,
            )
            print(result["run_dir"])
            return 0
        if args.command == "resolve":
            run_path = resolve_run(args.company_dir, run_id=args.run_id, latest=args.latest)
            print(str(run_path))
            return 0
        if args.command == "finish":
            result = finish_run(
                args.company_dir,
                args.run_dir,
                status=args.status,
                primary_period=args.primary_period,
                artifacts=args.artifact,
                conclusions_changed=args.conclusion_changed,
                report_periods=[args.report_periods] if args.report_periods else None,
                trigger_type=args.trigger_type,
                trigger_periods=args.trigger_period,
                force=args.force,
            )
            print(json.dumps(result["latest"], ensure_ascii=False))
            return 0
        if args.command == "adopt":
            result = adopt_legacy(args.company_dir, run_id=args.run_id, prune=args.prune)
            print(result["run_dir"])
            return 0
        if args.command == "export":
            print(export_ledger(args.company_dir, format=args.format), end="")
            return 0
        if args.command == "downstream":
            components = None if args.fresh.strip().lower() == "all" else [
                name.strip() for name in args.fresh.split(",") if name.strip()
            ]
            record = mark_downstream_fresh(args.company_dir, components=components)
            print(json.dumps(record["downstream"], ensure_ascii=False))
            return 0
    except ResolutionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except (LedgerError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2  # pragma: no cover - argparse rejects unknown commands first


if __name__ == "__main__":
    raise SystemExit(main())
