"""Contract tests for the periodic-update documentation.

The docs under review describe a run-store layout, so a command that resolves
the run directory must actually pass that run directory to the tools it calls.
These tests pin the two paths that an independent review found inconsistent.
"""

# 覆盖需求：REQ-005（增量更新文档与下游接线）—— AC-1 命令必须经 runs.py resolve
# 取 run_dir，不得把公司目录直接交给 resolver；AC-2 双布局下命令仍可用
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("command_dir", [".claude/commands", ".opencode/commands"])
def test_value_analysis_resolves_a_run_directory(command_dir):
    content = (ROOT / command_dir / "value-analysis.md").read_text(encoding="utf-8")

    # It must define and use {run_dir}, not hand the company directory to a
    # resolver that cannot follow latest.json.
    assert "runs.py resolve --company-dir" in content
    assert '{run_dir}' in content
    assert '--output-dir "{run_dir}"' in content
    assert 'resolve_qualitative --output-dir "{output_dir}"' not in content


@pytest.mark.parametrize("command_dir", [".claude/commands", ".opencode/commands"])
def test_value_analysis_does_not_auto_rerun_a_business_analysis_for_a_run_store(command_dir):
    content = (ROOT / command_dir / "value-analysis.md").read_text(encoding="utf-8")
    marker = "do **not** auto-run `/business-analysis`"
    assert marker in content
    # The gating sentence must precede the recovery instruction it qualifies.
    assert content.index(marker) < content.index("automatically run `/business-analysis")


def test_update_flow_documents_the_downstream_clearing_command():
    coordinator = (ROOT / "shared/qualitative/coordinator_update.md").read_text(encoding="utf-8")
    assert "runs.py downstream" in coordinator
    for command_dir in (".claude/commands", ".opencode/commands"):
        command = (ROOT / command_dir / "update-analysis.md").read_text(encoding="utf-8")
        assert 'runs.py downstream --company-dir "{company_dir}" --fresh' in command


def test_plan_does_not_claim_the_resolver_follows_the_pointer():
    plan = (ROOT / "docs/PERIODIC_UPDATE_PLAN.md").read_text(encoding="utf-8")
    assert "目录内只有 `latest.json`（公司目录）→ 先解析指针" not in plan
    assert "它不解析 `latest.json`" in plan


@pytest.mark.parametrize("name", ["value-analysis.md", "update-analysis.md"])
def test_mirrored_command_docs_stay_identical(name):
    opencode = (ROOT / ".opencode/commands" / name).read_text(encoding="utf-8")
    claude = (ROOT / ".claude/commands" / name).read_text(encoding="utf-8")
    assert opencode == claude


def test_value_strategy_coordinator_resolves_a_run_directory():
    """The coordinator is the pipeline spec /value-analysis tells the agent to run."""

    content = (ROOT / "strategies/value/coordinator.md").read_text(encoding="utf-8")

    assert "runs.py resolve --company-dir" in content
    assert '--output-dir "{run_dir}"' in content
    assert 'resolve_qualitative --output-dir "{output_dir}"' not in content
    assert "它不解析 `latest.json`" in content


def test_coordinator_gates_the_automatic_business_analysis_on_the_run_store():
    content = (ROOT / "strategies/value/coordinator.md").read_text(encoding="utf-8")

    # Both the prerequisite table and the exception table must refuse the
    # automatic full rerun once a run-store exists.
    assert content.count("{output_dir}/latest.json") >= 2
    assert "不得" in content
    assert "丢弃增量 run" in content


@pytest.mark.parametrize("command_dir", [".claude/commands", ".opencode/commands"])
def test_error_recovery_also_carries_the_run_store_gate(command_dir):
    content = (ROOT / command_dir / "value-analysis.md").read_text(encoding="utf-8")

    error_recovery = content.split("## Error Recovery", 1)[1]
    assert "latest.json" in error_recovery
    assert "do **not** auto-run `/business-analysis`" in error_recovery


def test_plan_has_no_contradictory_resolver_claim():
    plan = (ROOT / "docs/PERIODIC_UPDATE_PLAN.md").read_text(encoding="utf-8")

    assert "双入参兼容" not in plan
    assert "它不解析 `latest.json`" in plan


def test_docs_list_every_runs_subcommand():
    architecture = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for content in (architecture, readme):
        assert "downstream" in content


def test_market_refresh_does_not_suggest_a_full_rerun_under_a_run_store():
    content = (ROOT / "strategies/value/coordinator.md").read_text(encoding="utf-8")

    assert "重新执行 `/business-analysis`" not in content
    assert "/update-analysis {stock_code}" in content
    # The refresh step still must not redo the full analysis.
    assert "不重做完整 business-analysis" in content


@pytest.mark.parametrize("command_dir", [".claude/commands", ".opencode/commands"])
def test_value_analysis_clears_only_the_component_it_refreshes(command_dir):
    content = (ROOT / command_dir / "value-analysis.md").read_text(encoding="utf-8")

    # /value-analysis produces value_computed only; buy_sell_basis belongs to
    # /buy-sell-plan, so the aggregate must stay stale until both are refreshed.
    assert "--fresh value_computed" in content
    assert content.index("--fresh value_computed") < content.index("--fresh all")
    assert "buy_sell_basis" in content


@pytest.mark.parametrize(
    "path",
    [
        "strategies/value/coordinator.md",
        ".claude/commands/value-analysis.md",
        ".opencode/commands/value-analysis.md",
    ],
)
def test_runs_commands_use_the_same_interpreter_as_their_neighbours(path):
    """These files invoke everything through .venv/bin/python; stay consistent."""

    content = (ROOT / path).read_text(encoding="utf-8")
    for line in content.splitlines():
        if "scripts/runs.py" in line:
            assert ".venv/bin/python scripts/runs.py" in line, line


def test_implementation_record_names_the_main_touched_files():
    plan = (ROOT / "docs/PERIODIC_UPDATE_PLAN.md").read_text(encoding="utf-8")

    for needle in (
        "scripts/discover_report.py",
        "scripts/pdf_preprocessor.py",
        ".claude/commands/value-analysis.md",
        ".opencode/commands/value-analysis.md",
    ):
        assert needle in plan, needle
