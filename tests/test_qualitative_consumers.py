"""Contract tests for downstream qualitative-result consumers."""

from pathlib import Path
import shlex

import numpy as np

from portfolio_engine import DEFAULT_WEIGHTS, parse_profile_weights
from results.schema import RESULT_TYPE_CONTRACTS


ROOT = Path(__file__).resolve().parents[1]


def test_analysis_commands_resolve_structured_qualitative_input():
    command_names = ("value-analysis.md", "valuation.md")
    for command_dir in (ROOT / ".claude/commands", ROOT / ".opencode/commands"):
        for name in command_names:
            content = (command_dir / name).read_text(encoding="utf-8")
            assert "scripts.results.resolve_qualitative" in content
            assert "qualitative_input.json" in content
            assert "source=legacy" in content
            assert "status 3" in content.lower() or "状态 3" in content or "退出状态为 3" in content


def test_strategy_agents_use_resolved_qualitative_input():
    paths = (
        ROOT / "strategies/value/phase2_value_analysis.md",
        ROOT / "strategies/value/valuation/phase2_valuation.md",
    )
    for path in paths:
        content = path.read_text(encoding="utf-8")
        assert "qualitative_input.json" in content
        assert "source=structured" in content
        assert "source=legacy" in content


def test_portfolio_schema_profile_matches_engine_contract():
    profile_schema = ROOT / "strategies/portfolio/references/portfolio_schema.md"
    assert np.allclose(parse_profile_weights(profile_schema), DEFAULT_WEIGHTS)


def test_portfolio_commands_request_full_precompute():
    coordinator = (ROOT / "strategies/portfolio/coordinator.md").read_text(encoding="utf-8")
    assert "portfolio_engine.py --mode full" in coordinator
    assert "scripts.results.resolve_qualitative" in coordinator

    for path in (
        ROOT / ".claude/commands/portfolio-strategy.md",
        ROOT / ".opencode/commands/portfolio-strategy.md",
    ):
        content = path.read_text(encoding="utf-8")
        assert "scripts.results.resolve_qualitative" in content
        assert "$ARGUMENTS" in content


def test_portfolio_schema_uses_canonical_engine_asset_keys():
    content = (ROOT / "strategies/portfolio/references/portfolio_schema.md").read_text(
        encoding="utf-8"
    )
    for key in ("china_a", "hk", "us", "china_bond", "us_bond", "gold", "cash"):
        assert f"| {key} |" in content or f"| `{key}` |" in content
    for alias in ("china_a_select", "hk_select", "us_select", "china_bonds", "us_bonds"):
        assert f"| {alias} |" not in content


def test_portfolio_commands_use_full_profile_precomputation():
    for path in (
        ROOT / ".claude/commands/portfolio-strategy.md",
        ROOT / ".opencode/commands/portfolio-strategy.md",
    ):
        content = path.read_text(encoding="utf-8")
        assert "portfolio_engine.py --mode full --profile" in content
        assert "组合引擎基准权重" in content
        assert "$ARGUMENTS" in content


def test_portfolio_schema_has_balanced_fences_and_dynamic_final_weights():
    content = (ROOT / "strategies/portfolio/references/portfolio_schema.md").read_text(
        encoding="utf-8"
    )
    assert [line for line in content.splitlines() if line.startswith("```")] == ["```markdown", "```"] * 6
    final_template = content.split("## §6 最终报告输出", 1)[1]
    assert "从 tactical_allocation.md 读取" in final_template
    assert "权益类 █" not in final_template


def test_market_refresh_commands_preserve_manifest_inputs():
    paths = [
        ROOT / command_dir / name
        for command_dir in (".claude/commands", ".opencode/commands")
        for name in ("value-analysis.md",)
    ] + [ROOT / "strategies" / strategy / "coordinator.md" for strategy in ("value",)]
    for path in paths:
        content = path.read_text(encoding="utf-8")
        refresh_commands = [
            line for line in content.splitlines()
            if "tushare_collector.py --code" in line and "--refresh-market" in line
        ]
        assert refresh_commands, path
        for command in refresh_commands:
            args = shlex.split(command)
            assert args[args.index("--output") + 1] == "{output_dir}/data_pack_market_current.md", path
            assert args[args.index("--code") + 1] == "{ticker}", path


def test_business_analysis_commands_build_and_validate_synthesis_sidecar():
    for path in (
        ROOT / ".claude/commands/business-analysis.md",
        ROOT / ".opencode/commands/business-analysis.md",
    ):
        content = path.read_text(encoding="utf-8")
        assert "python3 -m scripts.results.synthesis" in content
        assert "scripts.results.validate_result" in content
        assert "scripts.results.resolve_qualitative" in content
        assert "must finish before `scripts.results.prepare`" in content
        validators = [line for line in content.splitlines() if "scripts.results.validate_result" in line]
        assert all("--evidence-index" in line for line in validators)


def test_parameter_contract_matches_documented_types_and_enums():
    content = (ROOT / "shared/qualitative/references/output_schema.md").read_text(encoding="utf-8")
    documented = {}
    type_rules = {"string": "string", "bool": "boolean", "float": "number", "float / null": "nullable_number", "list": "list"}
    for line in content.splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) != 6 or cells[2] not in (*type_rules, "enum"):
            continue
        documented[cells[1]] = set(cells[3].split(" / ")) if cells[2] == "enum" else type_rules[cells[2]]
    implemented = {
        key: rule for contract in RESULT_TYPE_CONTRACTS.values() for key, rule in contract["parameters"].items()
    }
    assert documented == implemented
