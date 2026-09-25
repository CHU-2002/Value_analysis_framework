
# 覆盖需求：REQ-003（分析迭代台账）—— AC-1 框架版本 / 提示词指纹 / 代码指纹 / schema 版本
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


def test_code_fingerprint_is_a_content_digest_not_the_head_sha(tmp_path, monkeypatch):
    """AC-2.7 / F28：指纹是**内容摘要**，与 git HEAD 无关。

    旧实现 `code_fingerprint` 直接取 `git rev-parse HEAD`，于是「记录实跑的那次文档提交」
    本身就把 run 判成 `framework_changed`。现在 `git_commit` 仍是独立的溯源字段，
    `code_fingerprint` 只反映 `DIRTY_TRACKED_PATHS` 下的文件内容。
    """

    root = _make_root(tmp_path)

    # HEAD 变来变去都不影响内容摘要。
    monkeypatch.setattr(
        version, "_git", lambda _root, *arguments: "abc1234" if arguments == ("rev-parse", "HEAD") else ""
    )
    first = code_fingerprint(root)
    monkeypatch.setattr(
        version, "_git", lambda _root, *arguments: "def5678" if arguments == ("rev-parse", "HEAD") else ""
    )
    assert code_fingerprint(root) == first
    assert first.startswith("sha256:")

    # 溯源字段照旧记录 HEAD。
    block = framework_block(root)
    assert block["git_commit"] == "def5678"
    assert block["code_fingerprint"] == first


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


def test_code_fingerprint_scopes_to_code_prompts_and_skills(tmp_path):
    """S7 + NEW-1 + F28：只有被分析读取的文件内容才改变指纹。"""

    root, git = _git_repo(tmp_path)
    clean = code_fingerprint(root)

    # 仓库根目录的随手记与框架身份无关。
    (root / "NOTES_scratch.md").write_text("scratch\n", encoding="utf-8")
    assert code_fingerprint(root) == clean

    # F28：**只改 docs/** 的提交不得改变指纹**（旧实现会因 HEAD 变化而改变）。
    (root / "docs").mkdir()
    (root / "docs" / "requirements").mkdir()
    (root / "docs" / "requirements" / "REQ-006.md").write_text("req v1\n", encoding="utf-8")
    git("add", "-A")
    assert git("commit", "-m", "docs only").returncode == 0
    assert code_fingerprint(root) == clean

    # NEW-1：prompts/phase2_PDF解析.md 仍被 business-analysis 命令读取，改它必须变。
    (root / "prompts" / "phase2_PDF解析.md").write_text("prompt v2\n", encoding="utf-8")
    assert code_fingerprint(root) != clean
    git("checkout", "--", ".")
    assert code_fingerprint(root) == clean

    # NEW-1：.claude/skills 也属于 Agent 面向的接口。
    (root / ".claude" / "skills" / "demo" / "SKILL.md").write_text("skill v2\n", encoding="utf-8")
    assert code_fingerprint(root) != clean
    git("checkout", "--", ".")
    assert code_fingerprint(root) == clean

    # 改被指纹覆盖的代码路径同样要变。
    (root / "scripts" / "engine.py").write_text("print('changed')\n", encoding="utf-8")
    assert code_fingerprint(root) != clean


def test_framework_block_dirty_stays_a_git_scoped_flag(tmp_path):
    """NEW-2：`framework_block().dirty` 仍用同一套 pathspec，与内容摘要各司其职。"""

    root, _git = _git_repo(tmp_path)
    block = framework_block(root)
    assert block["dirty"] is False

    (root / "NOTES_scratch.md").write_text("scratch\n", encoding="utf-8")
    assert framework_block(root)["dirty"] is False

    # 改了内容：dirty 为真，且内容摘要随之改变（不再是 `-dirty` 后缀）。
    clean = block["code_fingerprint"]
    (root / "scripts" / "engine.py").write_text("print('changed')\n", encoding="utf-8")
    block = framework_block(root)
    assert block["dirty"] is True
    assert block["code_fingerprint"] != clean


def test_docs_only_commit_does_not_invalidate_a_recorded_run(tmp_path):
    """F28 原始场景：记录 run 的那次**纯文档提交**不能让 run 变 `framework_changed`。

    旧实现 `code_fingerprint` 取 HEAD sha，于是在 `56a7d48`（只改 docs）上 `analysis_status`
    由 exit 1 变 exit 3/`full-rerun` —— 记录实跑本身令该 run 失效。这里用真实 git 提交复现：
    改动只落在 `docs/**` 时 HEAD 变了但指纹不变；改动 `scripts/**` 时指纹必须变。
    """

    root, git = _git_repo(tmp_path)
    before = framework_block(root)

    (root / "docs" / "run-records").mkdir(parents=True)
    (root / "docs" / "run-records" / "2026-09-25-run.md").write_text("实跑记录\n", encoding="utf-8")
    assert git("add", "-A").returncode == 0
    assert git("commit", "-m", "docs: record the run").returncode == 0

    after = framework_block(root)
    assert after["git_commit"] != before["git_commit"], "测试前提：HEAD 必须真的变了"
    assert after["code_fingerprint"] == before["code_fingerprint"]
    assert after["prompt_fingerprint"] == before["prompt_fingerprint"]
    assert after["dirty"] is False

    (root / "scripts" / "engine.py").write_text("print('engine v2')\n", encoding="utf-8")
    assert git("add", "-A").returncode == 0
    assert git("commit", "-m", "fix: engine").returncode == 0
    changed = framework_block(root)
    assert changed["code_fingerprint"] != after["code_fingerprint"]
    assert changed["prompt_fingerprint"] == after["prompt_fingerprint"]


def test_code_fingerprint_ignores_bytecode_and_cache_artifacts(tmp_path):
    """指纹只反映框架内容：`__pycache__` / `*.pyc` / `.DS_Store` 的出现不得改变它。"""

    root = _make_root(tmp_path)
    clean = code_fingerprint(root)

    cache = root / "scripts" / "__pycache__"
    cache.mkdir()
    (cache / "engine.cpython-312.pyc").write_bytes(b"\x00\x01")
    (root / "scripts" / ".DS_Store").write_bytes(b"\x00")
    assert code_fingerprint(root) == clean

    # 真内容改了还是要变。
    (root / "scripts" / "engine.py").write_text("print('changed')\n", encoding="utf-8")
    assert code_fingerprint(root) != clean


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


def test_contract_file_is_part_of_the_fingerprint_and_dirty_scope(tmp_path):
    """买卖合约既是框架输入（改内容 → 指纹变），也在 dirty 的 pathspec 里。"""

    root, git = _git_repo(tmp_path)
    (root / "docs").mkdir()
    contract = root / "docs" / "BUY_SELL_CONTRACT.md"
    contract.write_text("# contract\n", encoding="utf-8")
    assert git("add", "-A").returncode == 0
    assert git("commit", "-m", "add contract").returncode == 0

    clean = version.framework_block(root)
    assert clean["dirty"] is False

    contract.write_text("# contract (edited)\n", encoding="utf-8")

    block = version.framework_block(root)
    assert block["dirty"] is True
    assert block["code_fingerprint"] != clean["code_fingerprint"]
