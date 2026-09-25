#!/usr/bin/env python3
"""Build reproducibility manifests for analysis runs."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


#: JSON 输入里「每次重算都会变、但与内容无关」的字段。2026-09-25 实跑（F1）：同一份 PDF
#: 重解析只改了 `metadata.extract_time`，输入指纹却变了，旧 run 的 manifest 立刻校验失败。
VOLATILE_INPUT_KEYS: tuple[str, ...] = ("extract_time", "generated_at")


def _scrub_volatile(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _scrub_volatile(value)
            for key, value in node.items()
            if key not in VOLATILE_INPUT_KEYS
        }
    if isinstance(node, list):
        return [_scrub_volatile(value) for value in node]
    return node


def input_content_sha256(path: str | Path) -> str:
    """输入内容指纹（JSON 去掉易变字段后再哈希）。

    F1：同一份 PDF 重解析只改 `metadata.extract_time`，不该让 run 的输入指纹变化；
    其它文件仍按原始字节哈希，手工替换表格这类真实改动必须能被检出。
    """

    input_path = Path(path)
    if input_path.suffix.lower() != ".json":
        return sha256_file(input_path)
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return sha256_file(input_path)
    encoded = json.dumps(
        _scrub_volatile(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def describe_input(path: str | Path, *, source_id: str | None = None) -> dict[str, Any]:
    input_path = Path(path).resolve()
    item: dict[str, Any] = {
        "source_id": source_id or input_path.name,
        "path": str(input_path),
        "exists": input_path.exists(),
    }
    if input_path.exists() and input_path.is_file():
        stat = input_path.stat()
        item.update(
            {
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "sha256": input_content_sha256(input_path),
            }
        )
    return item


def describe_artifact(path: str | Path, **metadata: Any) -> dict[str, Any]:
    """Describe a prepared artifact so later consumers can detect tampering."""

    artifact_path = Path(path).resolve()
    item: dict[str, Any] = {"path": str(artifact_path), **metadata}
    if not artifact_path.is_file():
        raise ValueError(f"prepared artifact is not a regular file: {artifact_path}")
    stat = artifact_path.stat()
    item.update({"size_bytes": stat.st_size, "sha256": sha256_file(artifact_path)})
    return item


def input_set_digest(inputs: Iterable[dict[str, Any]]) -> str:
    """Return a stable digest for the identity and content of all run inputs."""

    stable = [
        {
            "source_id": item.get("source_id"),
            "path": item.get("path"),
            "exists": item.get("exists"),
            "sha256": item.get("sha256"),
        }
        for item in inputs
    ]
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_manifest_inputs(manifest: dict[str, Any]) -> list[str]:
    """Detect source files changed, removed, or replaced after preparation."""

    inputs = manifest.get("inputs")
    if not isinstance(inputs, list):
        return ["manifest inputs must be a list"]
    errors: list[str] = []
    for position, expected in enumerate(inputs):
        if not isinstance(expected, dict) or not isinstance(expected.get("path"), str):
            errors.append(f"manifest inputs[{position}] is invalid")
            continue
        current = describe_input(expected["path"], source_id=expected.get("source_id"))
        if current.get("exists") != expected.get("exists"):
            errors.append(f"manifest input changed existence: {expected['path']}")
        elif expected.get("exists") and current.get("sha256") != expected.get("sha256"):
            errors.append(f"manifest input hash changed: {expected['path']}")
    if isinstance(manifest.get("input_digest"), str):
        current_inputs = [
            describe_input(item["path"], source_id=item.get("source_id"))
            for item in inputs
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ]
        if input_set_digest(current_inputs) != manifest["input_digest"]:
            errors.append("manifest input_digest no longer matches source files")
    return errors


def validate_manifest_artifacts(manifest: dict[str, Any]) -> list[str]:
    """Verify that deterministic artifacts still match their prepared bytes."""

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return ["manifest artifacts must be a list"]
    errors: list[str] = []
    for position, expected in enumerate(artifacts):
        if not isinstance(expected, dict) or not isinstance(expected.get("path"), str):
            errors.append(f"manifest artifacts[{position}] is invalid")
            continue
        path = Path(expected["path"])
        expected_hash = expected.get("sha256")
        if not isinstance(expected_hash, str) or not expected_hash:
            errors.append(f"manifest artifact hash is missing: {path}")
        elif not path.is_file():
            errors.append(f"manifest artifact is missing or not a regular file: {path}")
        elif sha256_file(path) != expected_hash:
            errors.append(f"manifest artifact hash changed: {path}")
    return errors


def build_manifest(
    *,
    run_id: str,
    subject: dict[str, Any],
    inputs: Iterable[dict[str, Any]],
    artifacts: Iterable[dict[str, Any]] = (),
    status: str = "running",
    schema_version: str = "1.0",
    framework: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_list = list(inputs)
    manifest: dict[str, Any] = {
        "schema": "investment.manifest",
        "schema_version": schema_version,
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "subject": subject,
        "input_digest": input_set_digest(input_list),
        "inputs": input_list,
        "artifacts": list(artifacts),
    }
    # Additive: omitted entirely when not supplied, so manifests built without a
    # framework block stay byte-for-byte identical to previous releases.
    if framework is not None:
        manifest["framework"] = framework
    return manifest


def write_manifest(manifest: dict[str, Any], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_input(value: str) -> tuple[str | None, str]:
    if "=" in value:
        source_id, path = value.split("=", 1)
        return source_id, path
    return None, value


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an investment analysis run manifest")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--market", default="CN")
    parser.add_argument("--input", action="append", default=[], help="path or source_id=path")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    inputs = [describe_input(path, source_id=source_id) for source_id, path in (_parse_input(item) for item in args.input)]
    manifest = build_manifest(
        run_id=args.run_id,
        subject={"ticker": args.ticker, "company": args.company, "market": args.market},
        inputs=inputs,
    )
    write_manifest(manifest, args.output)
    print(f"Manifest written: {args.output}")


if __name__ == "__main__":
    main()
