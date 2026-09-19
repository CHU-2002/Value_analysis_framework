"""双布局端到端：legacy 扁平布局与 run-store 布局下，下游仍能解析到结论。

覆盖需求：REQ-005（增量更新文档与下游接线）——
AC-1 命令必须经 `runs.py resolve` 取 run_dir（resolver 不跟随 `latest.json`）、
AC-2 两种布局下既有读取路径均可用、
AC-5 mock baseline → 注入新期次 → 增量 run → 旧 run 仍可解析。

本文件是 REQ-005 的验收证据：契约测试（tests/test_update_docs_contract.py）钉住文档写法，
这里钉住底层可执行行为。
"""

import hashlib
from pathlib import Path

import pytest

import runs
from results.resolve_qualitative import resolve_qualitative_input
from tests.test_results_pipeline import _write_complete_structured_run

TICKER = "600000.SH"


def _company(tmp_path: Path) -> Path:
    company = tmp_path / "600000_Example"
    _write_complete_structured_run(company, ticker=TICKER)
    return company


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _tree(root: Path) -> list:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_flat_layout_resolves_without_latest_pointer(tmp_path):
    """legacy 布局：公司目录本身就是 run 目录，resolver 直接可用。"""
    company = _company(tmp_path)

    assert not (company / "latest.json").exists()
    payload = resolve_qualitative_input(company, ticker=TICKER)
    assert payload["source"] == "structured", payload["warnings"]


def test_run_store_layout_requires_the_run_directory(tmp_path):
    """run-store 布局（已被 adopt --prune 接管）：必须解析出 run_dir 再交给 resolver。

    直接给公司目录会拿不到结构化结果——这正是各命令与协调器必须先跑
    `runs.py resolve --company-dir … --latest` 的原因。
    """
    company = _company(tmp_path)
    adopted = runs.adopt_legacy(company, prune=True)
    run_dir = Path(adopted["run_dir"])

    assert (company / "latest.json").exists()
    assert runs.resolve_run(company, latest=True) == run_dir.resolve()

    via_run_dir = resolve_qualitative_input(run_dir, ticker=TICKER)
    assert via_run_dir["source"] == "structured", via_run_dir["warnings"]

    via_company_dir = resolve_qualitative_input(company, ticker=TICKER)
    assert via_company_dir["source"] != "structured", via_company_dir


def test_incremental_run_keeps_the_baseline_resolvable(tmp_path):
    """baseline → 注入新期次 → 增量 run：旧 run 仍可解析，latest 指向新 run。"""
    company = _company(tmp_path)
    baseline = runs.adopt_legacy(company, prune=True)
    baseline_dir = Path(baseline["run_dir"])
    baseline_id = baseline["run_id"]
    baseline_tree = _tree(baseline_dir)
    baseline_manifest = _digest(baseline_dir / "run_manifest.json")

    new_pdf = _write(tmp_path / "src" / "600000_2026_半年报.pdf", "%PDF-1.4 fake\n")

    created = runs.create_run(
        company,
        ticker=TICKER,
        company="Example Co",
        kind="report-update",
        primary_period="2026H1",
        supersedes=baseline_id,
        inputs=[new_pdf],
        run_id="update-run",
    )
    runs.finish_run(company, created["run_dir"], primary_period="2026H1")
    update_dir = Path(created["run_dir"])

    # 新 run 纳入了新期次，指针与台账都切到它
    assert (update_dir / "inputs" / new_pdf.name).exists()
    assert runs.resolve_run(company, latest=True) == update_dir.resolve()
    assert runs.latest_history_entry(company)["supersedes"] == baseline_id

    # 旧 run 一个字节都没被动过，结论仍可解析
    assert _tree(baseline_dir) == baseline_tree
    assert _digest(baseline_dir / "run_manifest.json") == baseline_manifest
    still = resolve_qualitative_input(baseline_dir, ticker=TICKER)
    assert still["source"] == "structured", still["warnings"]


@pytest.mark.parametrize("doc", ["docs/ARCHITECTURE.md", "README.md"])
def test_run_store_layout_is_documented(doc):
    """AC-3 / AC-4：布局与用法必须写在文档里。"""
    content = (Path(__file__).resolve().parents[1] / doc).read_text(encoding="utf-8")
    assert "run-store" in content or "latest.json" in content, doc
