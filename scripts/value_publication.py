#!/usr/bin/env python3
"""Publish the single current value-analysis report (``REQ-010``).

``/update-analysis`` advances the qualitative analysis to a new run and marks the
downstream consumers (``value_computed`` / ``buy_sell_basis``) stale, but it never
recomputes the valuation. The company directory therefore keeps a value report
that may silently belong to an older fiscal period, and ``latest.json`` only
describes the analysis run, not the value product. This module separates the two:

    <company_dir>/
      value_report.json              # the one pointer: which value report is current
      value_reports/
        <run_id>/
          revisions.jsonl            # append-only publish log for that run
          <sha12>/
            report.md                # immutable copy of the assembled report
            manifest.json            # provenance + completeness summary
            artifacts/               # immutable copies of value_computed.{md,json}
        failures.jsonl               # append-only failed-attempt log

Design rules (see ``docs/PERIODIC_UPDATE_PLAN.md`` §8.7):

- The pointer is the **only** entry consumers read; it names the source run, the
  fiscal period and the report digest, and it is replaced atomically.
- History is immutable and keyed by ``<run_id>/<sha12>``: a report is never
  rewritten, and any older version stays addressable by run id.
- A failure (crashed engine, missing/incomplete product) is logged and changes
  nothing else: the previous pointer, its bytes and its metadata stay put.
- Everything here is offline and standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # Support importing the package with the repository root on sys.path.
    from scripts.periods import is_valid_period, period_sort_key
except ImportError:  # Support importing with scripts/ on sys.path.
    from periods import is_valid_period, period_sort_key

try:
    from scripts.runs import read_history, read_latest, read_record
except ImportError:  # Support importing with scripts/ on sys.path.
    from runs import read_history, read_latest, read_record

try:
    from scripts.results.manifest import sha256_file
except ImportError:  # Support importing with scripts/ on sys.path.
    from results.manifest import sha256_file


POINTER_SCHEMA = "investment.value_report"
SNAPSHOT_SCHEMA = "investment.value_report_snapshot"
VALUE_SNAPSHOT_SCHEMA = "investment.value_snapshot"
SCHEMA_VERSION = "1.0"

#: Directory (inside the company dir) that holds the pointer, snapshots and logs.
VALUE_DIR_NAME = "value_reports"
POINTER_NAME = "value_report.json"
FAILURES_NAME = "failures.jsonl"
REVISIONS_NAME = "revisions.jsonl"
SNAPSHOT_REPORT_NAME = "report.md"
SNAPSHOT_MANIFEST_NAME = "manifest.json"
SNAPSHOT_ARTIFACTS_DIR = "artifacts"

#: Immutable copies of the deterministic value artifacts kept with every report.
SNAPSHOT_ARTIFACTS = ("value_computed.md", "value_computed.json")

#: Freshness states returned by :func:`read_current` (REQ-010 AC-2).
STATE_FRESH = "fresh"
STATE_STALE = "stale"
STATE_UNAVAILABLE = "unavailable"

#: Freshness reasons that make an otherwise valid pointer stale.
REASON_DOWNSTREAM_STALE = "downstream_stale"
REASON_NEWER_RUN = "newer_run"
REASON_NEWER_PERIOD = "newer_period"

#: Reasons that make the current report unavailable.
REASON_NO_POINTER = "no_pointer"
REASON_POINTER_UNREADABLE = "pointer_unreadable"
REASON_REPORT_MISSING = "report_missing"
REASON_DIGEST_MISSING = "digest_missing"
REASON_DIGEST_MISMATCH = "digest_mismatch"
REASON_SNAPSHOT_MISMATCH = "snapshot_mismatch"
REASON_NO_RUN_STORE = "no_run_store"
REASON_NO_SUCCESSFUL_RUN = "no_successful_run"
REASON_RUN_NOT_CONSUMABLE = "run_not_consumable"
REASON_RUN_NOT_FOUND = "run_not_found"

#: A report below this many bytes is treated as an empty stub, not a product.
MIN_REPORT_BYTES = 200

#: Value-analysis report file names produced by the assembler step.
REPORT_GLOB = "*价值分析报告.md"


class ValuePublicationError(Exception):
    """Invalid arguments, a failed publish, or an unavailable source (exit 2)."""


class ValueUnavailableError(ValuePublicationError):
    """The source run or a history revision cannot be resolved (exit 3)."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def utc_now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def iso_timestamp(moment: datetime | None = None) -> str:
    return utc_now(moment).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    """Write ``payload`` atomically (temp file in the same directory + rename)."""

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


def _append_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:  # pragma: no cover - defensive, mirrors analysis_status
        return False


def _reason(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _value_dir(company_dir: str | Path) -> Path:
    return Path(company_dir) / VALUE_DIR_NAME


def _file_summary(path: Path) -> dict[str, Any]:
    """Digest and size of one artifact (``None`` values when it is missing)."""

    if not path.is_file():
        return {"path": str(path), "sha256": None, "size_bytes": None, "present": False}
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "present": True,
    }


# ---------------------------------------------------------------------------
# source run resolution
# ---------------------------------------------------------------------------


def latest_successful_run(company_dir: str | Path) -> dict[str, Any] | None:
    """The newest analysis run recorded as ``complete`` (``None`` if there is none).

    ``record.json`` only describes the *latest* run, which may be ``partial`` or
    ``failed``. The value product must be attributed to a run that actually
    produced conclusions, so this walks the append-only history backwards.
    """

    company_path = Path(company_dir)
    for entry in reversed(read_history(company_path)):
        if entry.get("status") == "complete" and entry.get("run_id"):
            return {
                "run_id": entry.get("run_id"),
                "primary_period": entry.get("primary_period"),
                "kind": entry.get("kind"),
                "status": "complete",
            }
    record = read_record(company_path)
    if isinstance(record, dict) and record.get("status") == "complete" and record.get("latest_run"):
        return {
            "run_id": record.get("latest_run"),
            "primary_period": record.get("primary_period"),
            "kind": record.get("kind"),
            "status": "complete",
        }
    return None


def _run_dir_for(company_path: Path, run_id: str) -> Path | None:
    pointer = read_latest(company_path)
    if isinstance(pointer, dict) and pointer.get("run_id") == run_id:
        run_dir = pointer.get("run_dir")
        if isinstance(run_dir, str) and run_dir:
            candidate = Path(run_dir)
            if not candidate.is_absolute():
                candidate = company_path / candidate
            if candidate.is_dir():
                return candidate.resolve()
    candidate = company_path / "runs" / run_id
    return candidate.resolve() if candidate.is_dir() else None


def resolve_source_run(company_dir: str | Path, *, run_id: str | None = None) -> dict[str, Any]:
    """Resolve the qualitative run a value report may be built from (AC-3).

    The run must be recorded as ``complete`` and be consumable: its directory
    still exists and carries ``run_manifest.json`` (the manifest-backed layout
    ``resolve_qualitative`` reads). Raises :class:`ValueUnavailableError`
    otherwise, so a publish can never silently attribute a report to a run that
    the downstream resolver would refuse.
    """

    company_path = Path(company_dir).resolve()
    if run_id:
        entry = next(
            (item for item in read_history(company_path) if item.get("run_id") == run_id),
            None,
        )
        if entry is None or entry.get("status") != "complete":
            raise ValueUnavailableError(
                f"run {run_id!r} is not recorded as a successful analysis run in {company_path}"
            )
        source = {
            "run_id": run_id,
            "primary_period": entry.get("primary_period"),
            "kind": entry.get("kind"),
            "status": "complete",
        }
    else:
        source = latest_successful_run(company_path)
        if source is None:
            raise ValueUnavailableError(
                f"no successful analysis run in {company_path} "
                "(register the run-store first: runs.py adopt / update-analysis)"
            )

    run_dir = _run_dir_for(company_path, str(source["run_id"]))
    if run_dir is None:
        raise ValueUnavailableError(f"run directory is missing for run {source['run_id']!r}")
    if not (run_dir / "run_manifest.json").is_file():
        raise ValueUnavailableError(
            f"run {source['run_id']!r} is not consumable: {run_dir / 'run_manifest.json'} is missing"
        )
    return {**source, "run_dir": str(run_dir)}


# ---------------------------------------------------------------------------
# product validation / publication
# ---------------------------------------------------------------------------


def discover_report(company_dir: str | Path) -> Path | None:
    """The single assembled value report in the company dir (``None`` if not unique)."""

    matches = sorted(Path(company_dir).glob(REPORT_GLOB))
    return matches[0] if len(matches) == 1 else None


def _resolve_report(company_path: Path, report: str | Path | None) -> Path:
    if report is None:
        found = discover_report(company_path)
        if found is None:
            raise ValuePublicationError(
                f"cannot pick a unique {REPORT_GLOB} in {company_path}; pass --report explicitly"
            )
        return found.resolve()
    candidate = Path(report)
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    return candidate


def validate_product(company_dir: str | Path, report_path: str | Path) -> dict[str, Any]:
    """Build the completeness summary for a value product (never raises).

    The summary is registered with every publish so a consumer can tell a real
    report from a stub. ``complete`` is ``False`` when any check fails; the
    failed check names land in ``problems`` and the missing artifacts in
    ``missing``.
    """

    company_path = Path(company_dir)
    report = Path(report_path)
    problems: list[str] = []
    missing: list[str] = []

    report_info = _file_summary(report)
    if not report_info["present"]:
        problems.append("report_present")
        missing.append(str(report))
    else:
        if report_info["size_bytes"] < MIN_REPORT_BYTES:
            problems.append("report_non_empty")
        if not report.read_text(encoding="utf-8", errors="replace").strip():
            problems.append("report_readable")

    artifacts: dict[str, Any] = {}
    for name in SNAPSHOT_ARTIFACTS:
        info = _file_summary(company_path / name)
        artifacts[name] = info
        if not info["present"]:
            problems.append(f"{name}_present")
            missing.append(str(company_path / name))

    snapshot = _read_json(company_path / "value_computed.json")
    if snapshot is None and (company_path / "value_computed.json").is_file():
        problems.append("value_computed_json_parsable")
    elif snapshot is not None:
        if snapshot.get("schema") != VALUE_SNAPSHOT_SCHEMA:
            problems.append("value_computed_json_schema")
        values = snapshot.get("values")
        if not isinstance(values, dict) or not values.get("V_base"):
            problems.append("value_computed_json_values")

    summary: dict[str, Any] = {
        "complete": not problems,
        "checked_at": iso_timestamp(),
        "report": report_info,
        "artifacts": artifacts,
        "missing": sorted(set(missing)),
        "problems": problems,
    }
    if snapshot is not None:
        summary["financial_period"] = snapshot.get("financial_period")
    return summary


def _copy_immutable(source: Path, destination: Path) -> None:
    """Copy ``source`` to ``destination`` without ever rewriting an existing file."""

    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with source.open("rb") as reader, temporary.open("wb") as writer:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            writer.write(chunk)
    os.replace(temporary, destination)


def record_failure(
    company_dir: str | Path,
    *,
    run_id: str | None,
    reason: str,
    detail: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append a failed attempt to ``value_reports/failures.jsonl`` (AC-4).

    Nothing else is touched: the pointer, its bytes and the history stay exactly
    as they were, so a failed recompute cannot displace the last good report.
    """

    entry = {
        "recorded_at": iso_timestamp(now),
        "source_run": run_id,
        "reason": reason,
    }
    if detail:
        entry["detail"] = detail
    _append_line(_value_dir(company_dir) / FAILURES_NAME, entry)
    return entry


def read_failures(company_dir: str | Path) -> list[dict[str, Any]]:
    """Every recorded failed attempt, oldest first (malformed lines skipped)."""

    path = _value_dir(company_dir) / FAILURES_NAME
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def publish(
    company_dir: str | Path,
    *,
    report: str | Path | None = None,
    run_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Publish ``report`` as the current value report (AC-3, AC-4, AC-5).

    Validation runs *before* anything is written: on an incomplete product the
    attempt is logged and :class:`ValuePublicationError` is raised, leaving the
    pointer, its bytes and every earlier revision untouched. On success the
    report and the deterministic artifacts are frozen under
    ``value_reports/<run_id>/<sha12>/`` and the pointer is replaced atomically.
    """

    company_path = Path(company_dir).resolve()
    source = resolve_source_run(company_path, run_id=run_id)
    report_path = _resolve_report(company_path, report)
    summary = validate_product(company_path, report_path)
    if not summary["complete"]:
        record_failure(
            company_path,
            run_id=source["run_id"],
            reason="incomplete_product",
            detail=", ".join(summary["problems"]),
            now=now,
        )
        raise ValuePublicationError(
            "value product validation failed, the current pointer was left untouched: "
            + ", ".join(summary["problems"])
        )

    digest = str(summary["report"]["sha256"])
    revision_dir = _value_dir(company_path) / str(source["run_id"]) / digest[:12]
    manifest_path = revision_dir / SNAPSHOT_MANIFEST_NAME
    reused = manifest_path.is_file()
    if not reused:
        _copy_immutable(report_path, revision_dir / SNAPSHOT_REPORT_NAME)
        for name in SNAPSHOT_ARTIFACTS:
            _copy_immutable(company_path / name, revision_dir / SNAPSHOT_ARTIFACTS_DIR / name)
        manifest = {
            "schema": SNAPSHOT_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "source_run": source["run_id"],
            "run_kind": source.get("kind"),
            "primary_period": source.get("primary_period"),
            "published_at": iso_timestamp(now),
            "report_sha256": digest,
            "completeness": summary,
        }
        _write_json(manifest_path, manifest)
        _append_line(
            _value_dir(company_path) / str(source["run_id"]) / REVISIONS_NAME,
            {
                "published_at": manifest["published_at"],
                "report_sha256": digest,
                "snapshot_dir": str(revision_dir),
                "primary_period": source.get("primary_period"),
            },
        )

    pointer = {
        "schema": POINTER_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "source_run": source["run_id"],
        "primary_period": source.get("primary_period"),
        "report": str(revision_dir / SNAPSHOT_REPORT_NAME),
        "report_sha256": digest,
        "snapshot_dir": str(revision_dir),
        "published_at": iso_timestamp(now),
        "completeness": summary,
    }
    _write_json(company_path / POINTER_NAME, pointer)
    return {"pointer": pointer, "snapshot_dir": str(revision_dir), "reused": reused}


def _revision_order(run_dir: Path) -> list[Path]:
    """Revision directories of one run in **publish order**.

    ``revisions.jsonl`` is the authority (publish order, not digest order); a
    directory that somehow never made it into the log is appended afterwards so
    history is still discoverable.
    """

    ordered: list[Path] = []
    log = run_dir / REVISIONS_NAME
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            snapshot = payload.get("snapshot_dir") if isinstance(payload, dict) else None
            if not isinstance(snapshot, str):
                continue
            candidate = Path(snapshot)
            if candidate.is_dir() and (candidate / SNAPSHOT_MANIFEST_NAME).is_file() and candidate not in ordered:
                ordered.append(candidate)
    for candidate in sorted(
        path for path in run_dir.iterdir() if path.is_dir() and (path / SNAPSHOT_MANIFEST_NAME).is_file()
    ):
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def resolve_revision(
    company_dir: str | Path,
    run_id: str,
    *,
    digest: str | None = None,
) -> dict[str, Any]:
    """Resolve a historical value report by run id (AC-5).

    ``digest`` (full sha256 or its 12-char prefix) selects one revision; without
    it the newest revision published for that run is returned.
    """

    company_path = Path(company_dir).resolve()
    run_dir = _value_dir(company_path) / run_id
    if not run_dir.is_dir():
        raise ValueUnavailableError(f"no value report history for run {run_id!r}")
    candidates = _revision_order(run_dir)
    if not candidates:
        raise ValueUnavailableError(f"no value report history for run {run_id!r}")

    wanted = None
    if digest:
        wanted = next(
            (path for path in candidates if path.name == digest[:12] or path.name == digest),
            None,
        )
        if wanted is None:
            raise ValueUnavailableError(f"no value report revision {digest!r} for run {run_id!r}")
        candidates = [wanted]

    manifest = _read_json(candidates[-1] / SNAPSHOT_MANIFEST_NAME) or {}
    return {
        "source_run": run_id,
        "snapshot_dir": str(candidates[-1]),
        "report": str(candidates[-1] / SNAPSHOT_REPORT_NAME),
        "report_sha256": manifest.get("report_sha256"),
        "primary_period": manifest.get("primary_period"),
        "manifest": manifest,
        "revisions": [
            {
                "snapshot_dir": str(path),
                "report_sha256": (_read_json(path / SNAPSHOT_MANIFEST_NAME) or {}).get("report_sha256"),
            }
            for path in candidates
        ],
    }


# ---------------------------------------------------------------------------
# the single read entry
# ---------------------------------------------------------------------------


def _resolve_snapshot_dir(company_path: Path, pointer: dict[str, Any]) -> Path | None:
    """Return the frozen revision a pointer is tied to, or ``None`` (AC-5).

    A pointer is only trusted when **all** of these hold:

    - it carries a digest and a ``snapshot_dir``;
    - that directory sits under ``value_reports/<source_run>/``;
    - its ``manifest.json`` records the same digest;
    - the ``report`` it names is exactly that revision's ``report.md``.

    Anything else means the pointer is not tied to an immutable artifact, so the
    bytes it names can be rewritten at will — the read entry must refuse it.
    """

    recorded = pointer.get("report_sha256")
    snapshot_raw = pointer.get("snapshot_dir")
    if not isinstance(recorded, str) or not recorded:
        return None
    if not isinstance(snapshot_raw, str) or not snapshot_raw:
        return None

    snapshot = Path(snapshot_raw)
    if not snapshot.is_absolute():
        snapshot = company_path / snapshot
    try:
        snapshot = snapshot.resolve()
        expected_root = (company_path / VALUE_DIR_NAME / str(pointer.get("source_run"))).resolve()
        report_resolved = _resolved_report_path(company_path, pointer)
    except OSError:  # pragma: no cover - defensive: unreadable/long paths
        return None
    if report_resolved is None or not snapshot.is_relative_to(expected_root):
        return None
    if report_resolved.parent != snapshot:
        return None

    manifest = _read_json(snapshot / SNAPSHOT_MANIFEST_NAME)
    if manifest is None or manifest.get("report_sha256") != recorded:
        return None
    return snapshot


def _resolved_report_path(company_path: Path, pointer: dict[str, Any]) -> Path | None:
    """Absolute path of the report a pointer names (``None`` when it has none)."""

    raw = pointer.get("report")
    if not isinstance(raw, str) or not raw:
        return None
    report = Path(raw)
    if not report.is_absolute():
        report = company_path / report
    try:
        return report.resolve()
    except OSError:  # pragma: no cover - defensive
        return None


def _period_is_newer(candidate: Any, baseline: Any) -> bool:
    if not isinstance(candidate, str) or not isinstance(baseline, str):
        return False
    if not is_valid_period(candidate) or not is_valid_period(baseline):
        return False
    return period_sort_key(candidate) > period_sort_key(baseline)


def read_current(company_dir: str | Path) -> dict[str, Any]:
    """Resolve the current value report and its freshness (AC-1, AC-2).

    This is the only entry consumers need. It always returns a ``state``:

    - ``fresh``   — the pointer's digest still matches its frozen revision and no
      newer successful analysis run (nor a stale ``value_computed`` flag) exists;
    - ``stale``   — a valid report exists, but a newer analysis run, a newer
      fiscal period, or ``record.json:downstream.value_computed`` says it is out
      of date; the source run, period and report are still returned;
    - ``unavailable`` — there is no usable report at all (no pointer, missing
      report, digest mismatch, or no run-store to attribute it to).

    A stale/old report is **never** reported as ``fresh``: that is the whole
    point of the entry.
    """

    company_path = Path(company_dir).resolve()
    latest = latest_successful_run(company_path)
    base: dict[str, Any] = {
        "state": STATE_UNAVAILABLE,
        "basis": "run-store" if _path_exists(company_path / "latest.json") else "none",
        "source_run": None,
        "primary_period": None,
        "report": None,
        "report_sha256": None,
        "snapshot_dir": None,
        "latest_successful_run": latest,
        "reason": None,
    }

    pointer = _read_json(company_path / POINTER_NAME)
    if pointer is None:
        reason = (
            _reason(REASON_POINTER_UNREADABLE, f"unreadable pointer: {company_path / POINTER_NAME}")
            if _path_exists(company_path / POINTER_NAME)
            else _reason(
                REASON_NO_POINTER,
                f"no published value report ({POINTER_NAME}); run /value-analysis to publish one",
            )
        )
        return {**base, "reason": reason}
    if not pointer.get("report") or not pointer.get("source_run"):
        return {
            **base,
            "reason": _reason(REASON_POINTER_UNREADABLE, "pointer has no source_run/report"),
        }

    report_path = Path(str(pointer["report"]))
    base["source_run"] = pointer.get("source_run")
    base["primary_period"] = pointer.get("primary_period")
    base["report"] = str(report_path)
    base["report_sha256"] = pointer.get("report_sha256")

    recorded = pointer.get("report_sha256")
    if not isinstance(recorded, str) or not recorded:
        # 指针必须自带摘要：否则「指针摘要 == 历史产物摘要」（AC-5）无从校验，
        # 一份被就地改写的报告就会因为「没有摘要可对」而被读成 current。
        return {
            **base,
            "reason": _reason(
                REASON_DIGEST_MISSING,
                "pointer carries no report digest, so the published bytes cannot be trusted",
            ),
        }

    # AC-5 的不变量是「指针指向的报告 == 它自己的冻结历史产物」，所以指针必须能被
    # 钉到 value_reports/<source_run>/<sha12>/ 上，且那份 manifest 的摘要与指针一致。
    # 只校验「这份文件 == 指针里那个摘要」是不够的：把指针改指到历史目录之外的
    # 可变报告、再用该文件的自洽摘要「自签」，就能让一份随时可改的报告被读成 fresh。
    snapshot = _resolve_snapshot_dir(company_path, pointer)
    if snapshot is None:
        return {
            **base,
            "reason": _reason(
                REASON_SNAPSHOT_MISMATCH,
                "pointer is not tied to a frozen revision under "
                f"{VALUE_DIR_NAME}/{pointer.get('source_run')}/",
            ),
        }
    base["snapshot_dir"] = str(snapshot)

    if not report_path.is_file():
        return {
            **base,
            "reason": _reason(REASON_REPORT_MISSING, f"published report is missing: {report_path}"),
        }
    actual = sha256_file(report_path)
    if actual != recorded:
        return {
            **base,
            "reason": _reason(
                REASON_DIGEST_MISMATCH,
                f"report digest {actual} does not match the published {recorded}",
            ),
        }

    if latest is None:
        return {
            **base,
            "state": STATE_STALE,
            "reason": _reason(
                REASON_NO_SUCCESSFUL_RUN,
                "no successful analysis run is recorded; cannot confirm the report is current",
            ),
        }

    record = read_record(company_path) or {}
    downstream = record.get("downstream") if isinstance(record.get("downstream"), dict) else {}
    reasons: list[dict[str, str]] = []
    if latest.get("run_id") != pointer.get("source_run"):
        reasons.append(
            _reason(
                REASON_NEWER_RUN,
                f"report was published for run {pointer.get('source_run')}, "
                f"latest successful run is {latest.get('run_id')}",
            )
        )
    if _period_is_newer(latest.get("primary_period"), pointer.get("primary_period")):
        reasons.append(
            _reason(
                REASON_NEWER_PERIOD,
                f"latest analysis period {latest.get('primary_period')} is newer than "
                f"the value basis {pointer.get('primary_period')}",
            )
        )
    if downstream.get("value_computed"):
        reasons.append(
            _reason(REASON_DOWNSTREAM_STALE, "record.json marks value_computed stale for the latest run")
        )
    if reasons:
        return {**base, "state": STATE_STALE, "reason": reasons[0], "reasons": reasons}
    return {**base, "state": STATE_FRESH}


def value_status(company_dir: str | Path) -> dict[str, Any]:
    """Compact value block embedded into ``analysis_status`` output (AC-1)."""

    current = read_current(company_dir)
    return {
        "state": current["state"],
        "basis": current["basis"],
        "source_run": current["source_run"],
        "primary_period": current["primary_period"],
        "report": current["report"],
        "report_sha256": current["report_sha256"],
        "reason": current["reason"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_EXIT_FOR_STATE = {STATE_FRESH: 0, STATE_STALE: 1, STATE_UNAVAILABLE: 3}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish / read the single current value-analysis report (REQ-010)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish_parser = subparsers.add_parser(
        "publish",
        help="publish a value report as the current one (validates first, atomic pointer swap)",
        description=(
            "Validate the value product (report + value_computed.{md,json}), freeze it under "
            "value_reports/<run_id>/<sha12>/, then atomically update value_report.json. "
            "On an incomplete product the attempt is logged to value_reports/failures.jsonl "
            "and the current pointer is left untouched."
        ),
    )
    publish_parser.add_argument("--company-dir", required=True)
    publish_parser.add_argument("--report", help=f"assembled report path (default: the unique {REPORT_GLOB})")
    publish_parser.add_argument("--run-id", help="source run (default: latest successful run)")
    publish_parser.add_argument("--json", action="store_true")

    read_parser = subparsers.add_parser(
        "read",
        help="print the current value report, its source run, period and freshness",
        description=(
            "Exit codes: 0 fresh, 1 stale (a valid report exists but is out of date), "
            "3 unavailable (no usable report)."
        ),
    )
    read_parser.add_argument("--company-dir", required=True)
    read_parser.add_argument("--json", action="store_true")

    resolve_parser = subparsers.add_parser("resolve", help="resolve a historical version by run id")
    resolve_parser.add_argument("--company-dir", required=True)
    resolve_parser.add_argument("--run-id", required=True)
    resolve_parser.add_argument("--digest", help="full sha256 or its 12-char prefix")
    resolve_parser.add_argument("--json", action="store_true")

    fail_parser = subparsers.add_parser(
        "fail",
        help="record a failed value-analysis attempt without touching the current report",
        description=(
            "Use this when /value-analysis crashed before a product existed (no report to "
            "validate): the attempt is appended to value_reports/failures.jsonl and the "
            "pointer, its bytes and the history stay exactly as they were."
        ),
    )
    fail_parser.add_argument("--company-dir", required=True)
    fail_parser.add_argument("--reason", required=True)
    fail_parser.add_argument("--detail")
    fail_parser.add_argument("--run-id")
    fail_parser.add_argument("--json", action="store_true")
    return parser


def _render_current(current: dict[str, Any]) -> str:
    parts = [
        f"state={current['state']}",
        f"source_run={current['source_run'] or '—'}",
        f"primary_period={current['primary_period'] or '—'}",
    ]
    if current.get("report"):
        parts.append(f"report={current['report']}")
    reason = current.get("reason")
    if isinstance(reason, dict):
        parts.append(f"reason={reason['code']}: {reason['detail']}")
    return " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "publish":
            result = publish(args.company_dir, report=args.report, run_id=args.run_id)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(result["pointer"]["report"])
            return 0
        if args.command == "read":
            current = read_current(args.company_dir)
            if args.json:
                print(json.dumps(current, ensure_ascii=False, indent=2))
            else:
                print(_render_current(current))
            return _EXIT_FOR_STATE[current["state"]]
        if args.command == "resolve":
            revision = resolve_revision(args.company_dir, args.run_id, digest=args.digest)
            if args.json:
                print(json.dumps(revision, ensure_ascii=False, indent=2))
            else:
                print(revision["report"])
            return 0
        if args.command == "fail":
            entry = record_failure(
                args.company_dir,
                run_id=args.run_id,
                reason=args.reason,
                detail=args.detail,
            )
            if args.json:
                print(json.dumps(entry, ensure_ascii=False, indent=2))
            else:
                print(f"recorded: {entry['reason']}")
            return 0
    except ValueUnavailableError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except (ValuePublicationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2  # pragma: no cover - argparse rejects unknown commands first


if __name__ == "__main__":
    raise SystemExit(main())
