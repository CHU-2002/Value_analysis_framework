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
    """Lock the reported versions to the constants/builders that write them."""

    from results.context import build_module_context
    from results.evidence import build_evidence_index
    from results.manifest import build_manifest
    from results.schema import RESULT_SCHEMA_VERSION

    written = {
        "result": RESULT_SCHEMA_VERSION,
        "manifest": build_manifest(run_id="probe", subject={}, inputs=[])["schema_version"],
        "evidence_index": build_evidence_index([], input_digest="probe")["schema_version"],
        "context_bundle": build_module_context("business_moat", input_digest="probe")["schema_version"],
    }
    assert schema_versions() == written

    # A fresh copy is returned so callers cannot mutate module state.
    first = schema_versions()
    first["result"] = "9.9"
    assert schema_versions()["result"] == written["result"]


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
        if arguments[:2] == ("status", "--porcelain"):
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


def _git_repo(tmp_path):
    """Create a tiny git repo with code, a prompt and a skill, all committed."""

    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "prompts").mkdir()
    (root / ".claude" / "skills" / "demo").mkdir(parents=True)
    (root / "scripts" / "engine.py").write_text("print('engine')\n", encoding="utf-8")
    (root / "prompts" / "phase2_PDF解析.md").write_text("prompt v1\n", encoding="utf-8")
    (root / ".claude" / "skills" / "demo" / "SKILL.md").write_text("skill v1\n", encoding="utf-8")

    def git(*arguments):
        return subprocess.run(
            ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", *arguments],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )

    if git("init").returncode != 0:
        pytest.skip("git is not available in this environment")
    git("add", "-A")
    if git("commit", "-m", "initial").returncode != 0:
        pytest.skip("cannot create a git commit in this environment")
    return root, git


def test_code_fingerprint_dirty_scopes_to_code_prompts_and_skills(tmp_path):
    """S7 + NEW-1: unrelated files are ignored, real prompt/skill edits count."""

    root, git = _git_repo(tmp_path)
    clean = code_fingerprint(root)
    assert not clean.endswith("-dirty")

    # A scratch file at the repo root is irrelevant to the framework identity.
    (root / "NOTES_scratch.md").write_text("scratch\n", encoding="utf-8")
    assert code_fingerprint(root) == clean

    # NEW-1: prompts/phase2_PDF解析.md is still read by business-analysis commands.
    (root / "prompts" / "phase2_PDF解析.md").write_text("prompt v2\n", encoding="utf-8")
    assert code_fingerprint(root).endswith("-dirty")
    git("checkout", "--", ".")
    assert code_fingerprint(root) == clean

    # NEW-1: .claude/skills is part of the Agent-facing surface too.
    (root / ".claude" / "skills" / "demo" / "SKILL.md").write_text("skill v2\n", encoding="utf-8")
    assert code_fingerprint(root).endswith("-dirty")
    git("checkout", "--", ".")
    assert code_fingerprint(root) == clean

    # Editing a fingerprinted code path counts.
    (root / "scripts" / "engine.py").write_text("print('changed')\n", encoding="utf-8")
    assert code_fingerprint(root).endswith("-dirty")


def test_framework_block_dirty_matches_code_fingerprint(tmp_path):
    """NEW-2: framework_block().dirty must use the same scoped pathspec."""

    root, _git = _git_repo(tmp_path)
    block = framework_block(root)
    assert block["dirty"] is False
    assert not block["code_fingerprint"].endswith("-dirty")

    (root / "NOTES_scratch.md").write_text("scratch\n", encoding="utf-8")
    assert framework_block(root)["dirty"] is False

    (root / "scripts" / "engine.py").write_text("print('changed')\n", encoding="utf-8")
    block = framework_block(root)
    assert block["dirty"] is True
    assert block["code_fingerprint"].endswith("-dirty")


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


def test_contract_file_is_part_of_the_dirty_scope(tmp_path):
    """An uncommitted edit to the buy/sell contract must count as framework drift."""

    root, git = _git_repo(tmp_path)
    (root / "docs").mkdir()
    contract = root / "docs" / "BUY_SELL_CONTRACT.md"
    contract.write_text("# contract\n", encoding="utf-8")
    assert git("add", "-A").returncode == 0
    assert git("commit", "-m", "add contract").returncode == 0

    assert version.framework_block(root)["dirty"] is False

    contract.write_text("# contract (edited)\n", encoding="utf-8")

    block = version.framework_block(root)
    assert block["dirty"] is True
    assert block["code_fingerprint"].endswith("-dirty")
