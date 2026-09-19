#!/usr/bin/env python3
"""Framework version and reproducibility fingerprints.

The run-store ledger records *which* framework produced every analysis run:
the semantic version, the git commit (and whether the work tree was dirty),
a fingerprint over the prompts/commands that steer the Agents, a fingerprint
over the Python code, and the versions of the structured result schemas.

Everything here is standard library only and must work both as
``python -c "import scripts.version"`` and ``python scripts/version.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

#: Next semantic version after the current ``[Unreleased]`` CHANGELOG section.
FRAMEWORK_VERSION = "0.2.0"

#: Root-relative directories whose file contents define the prompt fingerprint.
PROMPT_ROOTS: tuple[str, ...] = (
    "strategies",
    "shared/qualitative",
    ".claude/commands",
    ".opencode/commands",
)

#: Kept byte-for-byte aligned with the schema constants in ``scripts/results``.
SCHEMA_VERSIONS: dict[str, str] = {
    "result": "1.0",
    "manifest": "1.0",
    "evidence_index": "1.0",
    "context_bundle": "1.0",
}

#: Paths whose working-tree changes count as "dirty" for :func:`code_fingerprint`.
#: Unrelated untracked files (scratch notes, local output) must not invalidate a run.
DIRTY_TRACKED_PATHS: tuple[str, ...] = (
    "scripts",
    "strategies",
    "shared",
    ".claude/commands",
    ".opencode/commands",
)

_GIT_TIMEOUT_SECONDS = 10


def repo_root() -> Path:
    """Return the repository root inferred from this file's location."""

    return Path(__file__).resolve().parent.parent


def schema_versions() -> dict[str, str]:
    """Return the structured-result schema versions this framework speaks."""

    return dict(SCHEMA_VERSIONS)


def _require_directory(root: str | Path) -> Path:
    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise ValueError(f"framework root is not an existing directory: {resolved}")
    return resolved


def _iter_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(directory).as_posix(),
    )


def _hash_files(pairs: Iterable[tuple[str, Path]]) -> str:
    digest = hashlib.sha256()
    for relative, path in pairs:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:  # Unreadable file: still fold in the path so the run is traceable.
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def prompt_fingerprint(root: str | Path | None = None) -> str:
    """Hash every prompt/command file below ``root`` into one stable digest.

    Files are collected from :data:`PROMPT_ROOTS`, ordered by their
    root-relative POSIX path, and fed to sha256 as ``relative-path NUL content
    NUL``. Raises ``ValueError`` when ``root`` is not an existing directory.
    """

    resolved = _require_directory(repo_root() if root is None else root)
    pairs: list[tuple[str, Path]] = []
    for relative_root in PROMPT_ROOTS:
        base = resolved / relative_root
        for path in _iter_files(base):
            pairs.append((path.relative_to(resolved).as_posix(), path))
    pairs.sort(key=lambda item: item[0])
    return _hash_files(pairs)


def _git(root: Path, *arguments: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def code_fingerprint(root: str | Path | None = None) -> str:
    """Return a stable identity for the code that produced a run.

    Prefers git (``<commit>`` or ``<commit>-dirty``) and degrades to a sha256
    over ``scripts/**/*.py`` when git is unavailable or ``root`` is not a
    repository. ``dirty`` only considers changes to :data:`DIRTY_TRACKED_PATHS`
    (``scripts``/``strategies``/``shared``/command definitions): unrelated
    untracked files such as scratch notes must not invalidate every run.
    Raises ``ValueError`` when ``root`` does not exist.
    """

    resolved = _require_directory(repo_root() if root is None else root)
    commit = _git(resolved, "rev-parse", "HEAD")
    if commit:
        porcelain = _git(resolved, "status", "--porcelain", "--", *DIRTY_TRACKED_PATHS)
        dirty = bool(porcelain)
        return f"{commit}-dirty" if dirty else commit
    pairs = [
        (path.relative_to(resolved).as_posix(), path)
        for path in _iter_files(resolved / "scripts")
        if path.suffix == ".py"
    ]
    pairs.sort(key=lambda item: item[0])
    return _hash_files(pairs)


def framework_block(root: str | Path | None = None) -> dict[str, Any]:
    """Return the framework block embedded into manifests and ledger entries.

    A missing ``root`` never raises: fingerprints and git fields degrade to
    ``None`` so a caller can still record an explicit "unknown framework".
    """

    resolved = Path(root).resolve() if root is not None else repo_root()
    block: dict[str, Any] = {
        "version": FRAMEWORK_VERSION,
        "git_commit": None,
        "dirty": None,
        "prompt_fingerprint": None,
        "code_fingerprint": None,
        "schema_versions": schema_versions(),
    }
    if not resolved.is_dir():
        return block
    block["prompt_fingerprint"] = prompt_fingerprint(resolved)
    block["code_fingerprint"] = code_fingerprint(resolved)
    commit = _git(resolved, "rev-parse", "HEAD")
    if commit:
        block["git_commit"] = commit
        block["dirty"] = bool(_git(resolved, "status", "--porcelain"))
    return block


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show framework version and fingerprints")
    parser.add_argument("--root", help="repository root (default: this file's repository)")
    parser.add_argument("--json", action="store_true", help="print the block as JSON")
    args = parser.parse_args(argv)

    try:
        block = framework_block(args.root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(block, ensure_ascii=False, indent=2))
    else:
        print(f"framework_version: {block['version']}")
        print(f"git_commit: {block['git_commit']}")
        print(f"dirty: {block['dirty']}")
        print(f"prompt_fingerprint: {block['prompt_fingerprint']}")
        print(f"code_fingerprint: {block['code_fingerprint']}")
        print(f"schema_versions: {json.dumps(block['schema_versions'], ensure_ascii=False, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
