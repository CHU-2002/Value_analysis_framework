"""Tests for the seven-asset portfolio engine contract."""

import json
import sys

import numpy as np
import pytest

from portfolio_engine import (
    DEFAULT_ASSETS,
    DEFAULT_CORR_MATRIX,
    DEFAULT_WEIGHTS,
    compute_risk_parity,
    main,
    parse_profile_weights,
    valuation_headwind_analysis,
)


def _write_profile(path, weights):
    rows = "\n".join(f"| `{key}` | {value}% |" for key, value in weights)
    path.write_text(
        "# User profile\n\n"
        "## 组合引擎基准权重\n\n"
        "| asset_key | weight_pct |\n"
        "|---|---:|\n"
        f"{rows}\n\n"
        "## 约束条件\n",
        encoding="utf-8",
    )


def test_default_weights_follow_seven_asset_contract():
    assert len(DEFAULT_ASSETS) == len(DEFAULT_WEIGHTS) == 7
    assert np.isclose(DEFAULT_WEIGHTS.sum(), 1.0)


def test_profile_parser_uses_asset_keys_independent_of_order(tmp_path):
    profile = tmp_path / "profile.md"
    weights = [
        ("cash", 22),
        ("gold", 3),
        ("us_bond", 5),
        ("china_bond", 8),
        ("us", 15),
        ("hk", 12),
        ("china_a", 35),
    ]
    _write_profile(profile, weights)

    parsed = parse_profile_weights(profile)

    assert np.allclose(parsed, DEFAULT_WEIGHTS)


def test_profile_parser_rejects_missing_asset(tmp_path):
    profile = tmp_path / "profile.md"
    _write_profile(
        profile,
        [(asset.key, weight * 100) for asset, weight in zip(DEFAULT_ASSETS[:-1], DEFAULT_WEIGHTS[:-1])],
    )

    with pytest.raises(ValueError, match="缺少资产键: cash"):
        parse_profile_weights(profile)


def test_main_uses_profile_weights(tmp_path, monkeypatch, capsys):
    profile = tmp_path / "profile.md"
    weights = [(asset.key, 0) for asset in DEFAULT_ASSETS]
    weights[-1] = ("cash", 100)
    _write_profile(profile, weights)
    monkeypatch.setattr(
        sys,
        "argv",
        ["portfolio_engine", "--mode", "scenario-analysis", "--profile", str(profile), "--json"],
    )

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["scenario_analysis"]["base_case"]["expected_return"] == "1.8%"


def test_explicit_weights_override_profile(tmp_path, monkeypatch, capsys):
    profile = tmp_path / "profile.md"
    _write_profile(profile, [(asset.key, weight * 100) for asset, weight in zip(DEFAULT_ASSETS, DEFAULT_WEIGHTS)])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "portfolio_engine",
            "--mode",
            "scenario-analysis",
            "--profile",
            str(profile),
            "--weights",
            "100,0,0,0,0,0,0",
            "--json",
        ],
    )

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["scenario_analysis"]["base_case"]["expected_return"] == "11.0%"


def test_risk_parity_equalizes_risk_contributions():
    volatilities = np.array([asset.volatility for asset in DEFAULT_ASSETS])
    covariance = np.diag(volatilities) @ DEFAULT_CORR_MATRIX @ np.diag(volatilities)

    weights = compute_risk_parity(covariance)
    contributions = weights * (covariance @ weights)

    assert np.ptp(contributions) / np.mean(contributions) < 1e-5


def test_valuation_headwind_includes_market_specific_scenarios():
    result = valuation_headwind_analysis(DEFAULT_WEIGHTS)
    names = {scenario["scenario"] for scenario in result["scenarios"]}
    assert names == {"美股估值回归", "A股估值回归", "A股与美股同时回归"}
    assert all(scenario["portfolio_impact_num"] < 0 for scenario in result["scenarios"])


def test_help_does_not_treat_percent_as_format_placeholder(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["portfolio_engine", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    assert "--risk-free" in capsys.readouterr().out


def test_json_output_writes_requested_file(tmp_path, monkeypatch, capsys):
    output = tmp_path / "portfolio.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "portfolio_engine",
            "--mode",
            "scenario-analysis",
            "--json",
            "--output",
            str(output),
        ],
    )

    main()

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert "scenario_analysis" in payload
    assert "valuation_headwind" in payload
    assert str(output) in capsys.readouterr().out
