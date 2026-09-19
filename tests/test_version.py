"""Tests for the framework version / fingerprint helpers."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import version
from version import (
    FRAMEWORK_VERSION,
    code_fingerprint,
    framework_block,
    prompt_fingerprint,
    schema_versions,
)


def _make_root(tmp_path):
    root = tmp_path / "repo"
    (root / "strategies" / "value").mkdir(parents=True)
    (root / "shared" / "qualitative" / "agents").mkdir(parents=True)
    (root / ".claude" / "commands").mkdir(parents=True)
    (root / ".opencode" / "commands").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "strategies" / "value" / "value.md").write_text("value prompt", encoding="utf-8")
    (root / "shared" / "qualitative" / "agents" / "moat.md").write_text("moat prompt", encoding="utf-8")
    (root / ".claude" / "commands" / "analyze.md").write_text("claude command", encoding="utf-8")
    (root / ".opencode" / "commands" / "analyze.md").write_text("opencode command", encoding="utf-8")
    (root / "scripts" / "engine.py").write_text("print('engine')\n", encoding="utf-8")
    (root / "scripts" / "notes.txt").write_text("not code", encoding="utf-8")
    return root


def test_framework_version_is_next_semver():
    assert FRAMEWORK_VERSION == "0.2.0"
    assert version.FRAMEWORK_VERSION == FRAMEWORK_VERSION


def test_schema_versions_match_result_protocol():
    assert schema_versions() == {
        "result": "1.0",
        "manifest": "1.0",
        "evidence_index": "1.0",
        "context_bundle": "1.0",
    }
    # A fresh copy is returned so callers cannot mutate module state.
    first = schema_versions()
    first["result"] = "9.9"
    assert schema_versions()["result"] == "1.0"


def test_prompt_fingerprint_is_stable_and_location_independent(tmp_path):
    root_a = _make_root(tmp_path / "a")
    root_b = _make_root(tmp_path / "b")
    first = prompt_fingerprint(root_a)
    second = prompt_fingerprint(root_a)
    assert first == second
    assert first.startswith("sha256:")
    # Relative paths are hashed, so two identical trees at different locations match.
    assert prompt_fingerprint(root_b) == first


def test_prompt_fingerprint_tracks_prompt_and_command_files(tmp_path):
    root = _make_root(tmp_path)
    baseline = prompt_fingerprint(root)
    (root / "strategies" / "value" / "value.md").write_text("changed prompt", encoding="utf-8")
    assert prompt_fingerprint(root) != baseline

    root = _make_root(tmp_path / "second")
    baseline = prompt_fingerprint(root)
    (root / ".claude" / "commands" / "new-command.md").write_text("new", encoding="utf-8")
    assert prompt_fingerprint(root) != baseline

    # Python code is covered by code_fingerprint, not the prompt fingerprint.
    root = _make_root(tmp_path / "third")
    baseline = prompt_fingerprint(root)
    (root / "scripts" / "engine.py").write_text("print('different')\n", encoding="utf-8")
    assert prompt_fingerprint(root) == baseline


def test_prompt_fingerprint_rejects_missing_root(tmp_path):
    with pytest.raises(ValueError):
        prompt_fingerprint(tmp_path / "does-not-exist")


def test_code_fingerprint_falls_back_to_hashing_scripts(tmp_path):
    root = _make_root(tmp_path)
    first = code_fingerprint(root)
    second = code_fingerprint(root)
    assert first == second
    assert first.startswith("sha256:")
    (root / "scripts" / "engine.py").write_text("print('changed')\n", encoding="utf-8")
    assert code_fingerprint(root) != first


def test_code_fingerprint_uses_git_when_available(tmp_path, monkeypatch):
    root = _make_root(tmp_path)

    def fake_git(_root, *arguments):
        if arguments == ("rev-parse", "HEAD"):
            return "abc1234"
        if arguments == ("status", "--porcelain"):
            return ""
        return None

    monkeypatch.setattr(version, "_git", fake_git)
    assert code_fingerprint(root) == "abc1234"

    monkeypatch.setattr(
        version,
        "_git",
        lambda _root, *arguments: "abc1234" if arguments == ("rev-parse", "HEAD") else " M scripts/engine.py",
    )
    assert code_fingerprint(root) == "abc1234-dirty"


def test_framework_block_degrades_without_root(tmp_path):
    block = framework_block(tmp_path / "missing-root")
    assert block["version"] == FRAMEWORK_VERSION
    assert block["git_commit"] is None
    assert block["dirty"] is None
    assert block["prompt_fingerprint"] is None
    assert block["code_fingerprint"] is None
    assert block["schema_versions"] == schema_versions()


def test_framework_block_on_non_git_root_uses_hash_fallbacks(tmp_path):
    root = _make_root(tmp_path)
    block = framework_block(root)
    assert set(block) == {
        "version",
        "git_commit",
        "dirty",
        "prompt_fingerprint",
        "code_fingerprint",
        "schema_versions",
    }
    assert block["git_commit"] is None
    assert block["dirty"] is None
    assert block["prompt_fingerprint"].startswith("sha256:")
    assert block["code_fingerprint"].startswith("sha256:")
    # The whole block is stable for the same input.
    assert framework_block(root) == block


def test_cli_prints_json_block(tmp_path, capsys):
    root = _make_root(tmp_path)
    assert version.main(["--root", str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == FRAMEWORK_VERSION
    assert payload["prompt_fingerprint"] == prompt_fingerprint(root)


def test_runs_as_package_and_as_script(tmp_path):
    repo_root = Path(version.__file__).resolve().parent.parent
    as_package = subprocess.run(
        [sys.executable, "-c", "import scripts.version as v; print(v.FRAMEWORK_VERSION)"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert as_package.returncode == 0, as_package.stderr
    assert as_package.stdout.strip() == FRAMEWORK_VERSION

    as_script = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "version.py"), "--root", str(tmp_path), "--json"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert as_script.returncode == 0, as_script.stderr
    assert json.loads(as_script.stdout)["version"] == FRAMEWORK_VERSION
