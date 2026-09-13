#!/usr/bin/env python3
"""
组合策略预计算引擎 — Portfolio Pre-compute Engine (v2.0)

提供：
1. 有效前沿（Efficient Frontier）计算
2. 风险平价（Risk Parity）权重
3. 相关性矩阵
4. 回撤情景分析
5. 组合预期收益/波动率计算
6. 估值逆风情景分析（v2.0 新增）

用法：
  python3 scripts/portfolio_engine.py --mode efficient-frontier
      --profile output/portfolio_xxx/profile.md
      --output output/portfolio_xxx/efficient_frontier.md

  python3 scripts/portfolio_engine.py --mode scenario-analysis
      --weights 35,12,15,8,5,3,22
      --output output/portfolio_xxx/scenario.md

v2.0 变更：资产类别从 10 类精简为 7 核心类
"""

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

# ──────────────────────────────────────────────────────────────
# 资产类别定义与历史参数
# ──────────────────────────────────────────────────────────────


@dataclass
class AssetClass:
    """单个资产类别的定义"""

    key: str
    name_cn: str
    name_en: str
    expected_return: float  # 预期年化收益率 (小数)
    volatility: float  # 预期年化波动率 (小数)
    category: str  # equity / fixed_income / alternative / cash
    subcategory: str  # china / us / hk / global / gold / digital / cash


# 默认资产类别列表（v2.0 — 7 核心类）
DEFAULT_ASSETS: List[AssetClass] = [
    AssetClass("china_a", "A股精选", "China A-Shares Select", 0.11, 0.25, "equity", "china"),
    AssetClass("hk", "港股精选", "HK Stocks Select", 0.10, 0.25, "equity", "hk"),
    AssetClass("us", "美股精选", "US Stocks Select", 0.09, 0.18, "equity", "us"),
    AssetClass("china_bond", "中国固收", "China Bonds", 0.025, 0.02, "fixed_income", "china"),
    AssetClass("us_bond", "美国固收", "US Bonds", 0.04, 0.03, "fixed_income", "us"),
    AssetClass("gold", "黄金", "Gold", 0.03, 0.15, "alternative", "gold"),
    AssetClass("cash", "现金", "Cash", 0.018, 0.005, "cash", "cash"),
]

DEFAULT_WEIGHTS = np.array([35, 12, 15, 8, 5, 3, 22], dtype=float) / 100.0

# 默认相关性矩阵 (7x7)
# 行/列顺序: china_a, hk, us, china_bond, us_bond, gold, cash
DEFAULT_CORR_MATRIX = np.array([
    # china_a   hk      us    cn_bond  us_bond  gold   cash
    [1.00, 0.55, 0.30, -0.10, -0.05, 0.05, 0.00],  # china_a
    [0.55, 1.00, 0.45, -0.05, 0.00, 0.10, 0.00],  # hk
    [0.30, 0.45, 1.00, -0.15, -0.10, 0.00, 0.00],  # us
    [-0.10, -0.05, -0.15, 1.00, 0.30, 0.20, 0.10],  # cn_bond
    [-0.05, 0.00, -0.10, 0.30, 1.00, 0.15, 0.05],  # us_bond
    [0.05, 0.10, 0.00, 0.20, 0.15, 1.00, 0.05],  # gold
    [0.00, 0.00, 0.00, 0.10, 0.05, 0.05, 1.00],  # cash
])


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────


def annualize_return(daily_returns: np.ndarray, trading_days: int = 252) -> float:
    """年化收益率"""
    return float(np.mean(daily_returns) * trading_days)


def annualize_volatility(daily_returns: np.ndarray, trading_days: int = 252) -> float:
    """年化波动率"""
    return float(np.std(daily_returns, ddof=1) * math.sqrt(trading_days))


def sharpe_ratio(annual_return: float, annual_vol: float, risk_free: float = 0.025) -> float:
    """夏普比率"""
    if annual_vol < 0.0001:
        return 0.0
    return (annual_return - risk_free) / annual_vol


def portfolio_return(weights: np.ndarray, expected_returns: np.ndarray) -> float:
    """组合预期收益"""
    return float(np.dot(weights, expected_returns))


def portfolio_volatility(weights: np.ndarray, cov_matrix: np.ndarray) -> float:
    """组合预期波动率"""
    return float(math.sqrt(np.dot(weights.T, np.dot(cov_matrix, weights))))


def portfolio_sharpe(weights: np.ndarray, expected_returns: np.ndarray, cov_matrix: np.ndarray, rf: float = 0.025) -> float:
    """组合夏普比率"""
    ret = portfolio_return(weights, expected_returns)
    vol = portfolio_volatility(weights, cov_matrix)
    return sharpe_ratio(ret, vol, rf)


# ──────────────────────────────────────────────────────────────
# 有效前沿
# ──────────────────────────────────────────────────────────────


def compute_efficient_frontier(
    expected_returns: np.ndarray,
    cov_matrix: np.ndarray,
    n_portfolios: int = 10000,
    rf: float = 0.025,
) -> Dict:
    """
    使用蒙特卡洛模拟生成有效前沿。

    返回:
      {
        "portfolios": [(ret, vol, sharpe, weights), ...],
        "max_sharpe": {"ret": x, "vol": x, "sharpe": x, "weights": [...]},
        "min_vol": {"ret": x, "vol": x, "sharpe": x, "weights": [...]},
        "frontier_points": [(ret, vol), ...],  # 有效前沿上的点
      }
    """
    n = len(expected_returns)
    results = []

    for _ in range(n_portfolios):
        w = np.random.random(n)
        w = w / w.sum()
        ret = portfolio_return(w, expected_returns)
        vol = portfolio_volatility(w, cov_matrix)
        sr = sharpe_ratio(ret, vol, rf)
        results.append((ret, vol, sr, w))

    # 按波动率排序
    results.sort(key=lambda x: x[1])

    # 提取有效前沿（每个波动率水平下收益最高的组合）
    frontier = []
    max_ret_seen = -float("inf")
    for ret, vol, sr, w in results:
        if ret > max_ret_seen:
            max_ret_seen = ret
            frontier.append((ret, vol, sr, w))

    # 最大夏普比率组合
    max_sr = max(results, key=lambda x: x[2])

    # 最小波动率组合
    min_vol = min(results, key=lambda x: x[1])

    return {
        "portfolios": [(r[0], r[1], r[2]) for r in results[:100]],  # 前100个用于展示
        "max_sharpe": {
            "return": round(max_sr[0] * 100, 2),
            "volatility": round(max_sr[1] * 100, 2),
            "sharpe": round(max_sr[2], 2),
            "weights": [round(w * 100, 1) for w in max_sr[3]],
        },
        "min_vol": {
            "return": round(min_vol[0] * 100, 2),
            "volatility": round(min_vol[1] * 100, 2),
            "sharpe": round(min_vol[2], 2),
            "weights": [round(w * 100, 1) for w in min_vol[3]],
        },
        "frontier_points": [(round(r[0] * 100, 2), round(r[1] * 100, 2)) for r in frontier[:: max(1, len(frontier) // 20)]],
    }


# ──────────────────────────────────────────────────────────────
# 风险平价
# ──────────────────────────────────────────────────────────────


def compute_risk_parity(cov_matrix: np.ndarray, max_iter: int = 1000, tol: float = 1e-8) -> np.ndarray:
    """
    风险平价权重：每类资产对组合的边际风险贡献相等。

    使用循环坐标下降求解等风险预算组合。
    """
    n = cov_matrix.shape[0]
    if cov_matrix.shape != (n, n) or np.any(np.diag(cov_matrix) <= 0):
        raise ValueError("协方差矩阵必须是方阵且对角线为正")

    budgets = np.ones(n) / n
    x = np.ones(n)
    for _ in range(max_iter):
        previous = x.copy()
        for i in range(n):
            variance = cov_matrix[i, i]
            cross_term = float(np.dot(cov_matrix[i], x) - variance * x[i])
            discriminant = cross_term ** 2 + 4 * variance * budgets[i]
            x[i] = (-cross_term + math.sqrt(discriminant)) / (2 * variance)
        if np.max(np.abs(x - previous)) < tol:
            break

    return x / x.sum()


# ──────────────────────────────────────────────────────────────
# 情景分析
# ──────────────────────────────────────────────────────────────


def scenario_analysis(
    weights: np.ndarray,
    asset_names: List[str],
    expected_returns: np.ndarray,
    cov_matrix: np.ndarray,
    scenarios: Optional[List[Dict]] = None,
) -> Dict:
    """
    回撤情景分析。

    scenarios: 每个情景定义股市跌幅和各资产beta
    若无自定义，使用默认三种情景
    """
    if scenarios is None:
        scenarios = [
            {
                "name": "mild_correction",
                "label": "温和回调",
                "equity_decline": -0.10,
                "bond_return": 0.02,
                "gold_return": 0.05,
            },
            {
                "name": "bear_market",
                "label": "熊市",
                "equity_decline": -0.30,
                "bond_return": 0.05,
                "gold_return": 0.10,
            },
            {
                "name": "extreme_crisis",
                "label": "极端危机",
                "equity_decline": -0.50,
                "bond_return": 0.08,
                "gold_return": 0.20,
            },
        ]

    results = []
    for sc in scenarios:
        # 分别估计各类资产在此情景下的收益
        asset_returns = np.zeros(len(weights))
        for i in range(len(weights)):
            cat = DEFAULT_ASSETS[i].category
            subcat = DEFAULT_ASSETS[i].subcategory
            if cat == "equity":
                asset_returns[i] = sc["equity_decline"]
            elif cat == "fixed_income":
                asset_returns[i] = sc["bond_return"]
            elif subcat == "gold":
                asset_returns[i] = sc["gold_return"]
            elif cat == "cash":
                asset_returns[i] = 0.01  # 现金微正
            else:
                asset_returns[i] = sc["equity_decline"] * 0.5

        portfolio_drawdown = float(np.dot(weights, asset_returns))
        results.append(
            {
                "scenario": sc["label"],
                "equity_decline": f"{sc['equity_decline']:.0%}",
                "portfolio_drawdown": f"{portfolio_drawdown:.1%}",
                "portfolio_drawdown_num": portfolio_drawdown,
            }
        )

    # 正常情景（基准预期）
    base_return = portfolio_return(weights, expected_returns)
    base_vol = portfolio_volatility(weights, cov_matrix)

    return {
        "base_case": {
            "expected_return": f"{base_return:.1%}",
            "expected_volatility": f"{base_vol:.1%}",
            "sharpe": f"{sharpe_ratio(base_return, base_vol):.2f}",
        },
        "scenarios": results,
    }


def valuation_headwind_analysis(weights: np.ndarray) -> Dict:
    """Estimate portfolio impact from market-specific valuation mean reversion."""
    _validate_weights(weights)
    scenarios = [
        {
            "scenario": "美股估值回归",
            "shocks": {
                "china_a": -0.05, "hk": -0.10, "us": -0.30,
                "china_bond": 0.02, "us_bond": 0.03, "gold": 0.05, "cash": 0.01,
            },
        },
        {
            "scenario": "A股估值回归",
            "shocks": {
                "china_a": -0.30, "hk": -0.20, "us": -0.05,
                "china_bond": 0.03, "us_bond": 0.02, "gold": 0.05, "cash": 0.01,
            },
        },
        {
            "scenario": "A股与美股同时回归",
            "shocks": {
                "china_a": -0.30, "hk": -0.25, "us": -0.30,
                "china_bond": 0.03, "us_bond": 0.04, "gold": 0.10, "cash": 0.01,
            },
        },
    ]
    asset_keys = [asset.key for asset in DEFAULT_ASSETS]
    results = []
    for scenario in scenarios:
        shock_vector = np.array([scenario["shocks"][key] for key in asset_keys])
        impact = float(np.dot(weights, shock_vector))
        results.append(
            {
                "scenario": scenario["scenario"],
                "portfolio_impact": f"{impact:.1%}",
                "portfolio_impact_num": impact,
                "asset_shocks": {
                    key: f"{scenario['shocks'][key]:.0%}" for key in asset_keys
                },
            }
        )
    return {"scenarios": results}


# ──────────────────────────────────────────────────────────────
# 用户画像解析 (从 profile.md)
# ──────────────────────────────────────────────────────────────


def _validate_weights(weights: np.ndarray) -> np.ndarray:
    if weights.shape != (len(DEFAULT_ASSETS),):
        raise ValueError(f"需要 {len(DEFAULT_ASSETS)} 个资产权重")
    if not np.all(np.isfinite(weights)) or np.any(weights < 0):
        raise ValueError("资产权重必须是非负有限数值")
    if not np.isclose(float(weights.sum()), 1.0, atol=1e-6):
        raise ValueError(f"资产权重合计必须为 100%，当前为 {weights.sum():.2%}")
    return weights


def parse_profile_weights(profile_path: Path) -> np.ndarray:
    """从 profile.md 的键控基准权重表解析七类资产权重。"""
    if not profile_path.exists():
        raise ValueError(f"用户画像文件不存在: {profile_path}")
    try:
        content = profile_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"无法读取用户画像文件: {profile_path}: {exc}") from exc

    section = re.search(
        r"^## 组合引擎基准权重\s*$([\s\S]*?)(?=^##\s|\Z)",
        content,
        flags=re.MULTILINE,
    )
    if not section:
        raise ValueError("profile.md 缺少 '## 组合引擎基准权重' 表")

    expected_keys = [asset.key for asset in DEFAULT_ASSETS]
    parsed: dict[str, float] = {}
    for line in section.group(1).splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        key = cells[0].strip("` ")
        if key in {"asset_key", "资产键"} or not key or set(key) <= {"-", ":"}:
            continue
        value_text = cells[1].replace("**", "").strip().removesuffix("%").strip()
        if key not in expected_keys:
            if value_text and not set(value_text) <= {"-", ":"}:
                raise ValueError(f"组合引擎基准权重包含未知资产键: {key}")
            continue
        if key in parsed:
            raise ValueError(f"组合引擎基准权重包含重复资产键: {key}")
        try:
            parsed[key] = float(value_text)
        except ValueError as exc:
            raise ValueError(f"资产 {key} 的权重不是有效百分比: {cells[1]}") from exc

    missing = [key for key in expected_keys if key not in parsed]
    if missing:
        raise ValueError(f"组合引擎基准权重缺少资产键: {', '.join(missing)}")
    return _validate_weights(np.array([parsed[key] for key in expected_keys]) / 100.0)


# ──────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="组合策略预计算引擎")
    parser.add_argument("--mode", choices=["efficient-frontier", "scenario-analysis", "valuation-headwind", "risk-parity", "correlation-matrix", "full"],
                        default="full", help="计算模式")
    parser.add_argument("--profile", type=Path, help="用户画像文件路径 (profile.md)")
    parser.add_argument("--weights", type=str, help="逗号分隔的权重 (百分比，v2.0: 7 个资产)")
    parser.add_argument("--output", type=Path, help="输出文件路径")
    parser.add_argument("--risk-free", type=float, default=0.025, help="无风险利率 (默认 0.025)")
    parser.add_argument("--json", action="store_true", help="输出JSON格式")

    args = parser.parse_args()

    # 解析权重
    if args.weights:
        try:
            weights = _validate_weights(
                np.array([float(x) for x in args.weights.split(",")], dtype=float) / 100.0
            )
        except ValueError as exc:
            parser.error(str(exc))
    elif args.profile:
        try:
            weights = parse_profile_weights(args.profile)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        weights = DEFAULT_WEIGHTS.copy()

    # 构建预期收益向量
    expected_returns = np.array([a.expected_return for a in DEFAULT_ASSETS])
    volatilities = np.array([a.volatility for a in DEFAULT_ASSETS])
    cov_matrix = np.diag(volatilities) @ DEFAULT_CORR_MATRIX @ np.diag(volatilities)
    rf = args.risk_free

    # 资产名称
    asset_names = [a.name_cn for a in DEFAULT_ASSETS]
    asset_keys = [a.key for a in DEFAULT_ASSETS]

    results = {}

    # ── 有效前沿 ──
    if args.mode in ("efficient-frontier", "full"):
        ef_result = compute_efficient_frontier(expected_returns, cov_matrix, rf=rf)
        results["efficient_frontier"] = ef_result

    # ── 风险平价 ──
    if args.mode in ("risk-parity", "full"):
        rp_weights = compute_risk_parity(cov_matrix)
        rp_ret = portfolio_return(rp_weights, expected_returns)
        rp_vol = portfolio_volatility(rp_weights, cov_matrix)
        rp_sr = sharpe_ratio(rp_ret, rp_vol, rf)
        results["risk_parity"] = {
            "weights": {asset_names[i]: round(rp_weights[i] * 100, 1) for i in range(len(rp_weights))},
            "return": round(rp_ret * 100, 2),
            "volatility": round(rp_vol * 100, 2),
            "sharpe": round(rp_sr, 2),
        }

    # ── 组合基准 ──
    if args.mode in ("full",):
        base_ret = portfolio_return(weights, expected_returns)
        base_vol = portfolio_volatility(weights, cov_matrix)
        base_sr = sharpe_ratio(base_ret, base_vol, rf)
        results["benchmark"] = {
            "weights": {asset_names[i]: round(weights[i] * 100, 1) for i in range(len(weights))},
            "return": round(base_ret * 100, 2),
            "volatility": round(base_vol * 100, 2),
            "sharpe": round(base_sr, 2),
        }

    # ── 情景分析 ──
    if args.mode in ("scenario-analysis", "full"):
        sa_result = scenario_analysis(weights, asset_names, expected_returns, cov_matrix)
        results["scenario_analysis"] = sa_result

    if args.mode in ("scenario-analysis", "valuation-headwind", "full"):
        results["valuation_headwind"] = valuation_headwind_analysis(weights)

    # ── 相关性矩阵 ──
    if args.mode in ("correlation-matrix", "full"):
        results["correlation_matrix"] = {
            "assets": asset_names,
            "matrix": DEFAULT_CORR_MATRIX.tolist(),
            "avg_correlation": round(float(np.mean(DEFAULT_CORR_MATRIX[np.triu_indices_from(DEFAULT_CORR_MATRIX, k=1)])), 3),
        }

    # ── 输出 ──
    if args.json:
        output = json.dumps(results, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
            print(f"输出已写入: {args.output}")
        else:
            print(output, end="")
    else:
        output = format_markdown(results, weights, asset_names, asset_keys, rf)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
            print(f"输出已写入: {args.output}")
        else:
            print(output)


def format_markdown(results: Dict, weights: np.ndarray, asset_names: List[str], asset_keys: List[str], rf: float) -> str:
    """将计算结果格式化为 Markdown"""
    lines = ["# 组合优化预计算结果\n"]
    lines.append(f"> 无风险利率: {rf:.1%}\n")

    # ── 基准组合 ──
    if "benchmark" in results:
        bm = results["benchmark"]
        lines.append("## 基准组合\n")
        lines.append("| 资产类别 | 权重 |")
        lines.append("|---------|------|")
        for name in asset_names:
            lines.append(f"| {name} | {bm['weights'][name]}% |")
        lines.append("")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 预期年化收益 | {bm['return']}% |")
        lines.append(f"| 预期年化波动率 | {bm['volatility']}% |")
        lines.append(f"| 夏普比率 | {bm['sharpe']} |")
        lines.append("")

    # ── 最大夏普比率组合 ──
    if "efficient_frontier" in results:
        ef = results["efficient_frontier"]
        ms = ef["max_sharpe"]
        lines.append("## 最大夏普比率组合\n")
        lines.append("| 资产类别 | 权重 |")
        lines.append("|---------|------|")
        for i, name in enumerate(asset_names):
            lines.append(f"| {name} | {ms['weights'][i]}% |")
        lines.append("")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 预期年化收益 | {ms['return']}% |")
        lines.append(f"| 预期年化波动率 | {ms['volatility']}% |")
        lines.append(f"| 夏普比率 | {ms['sharpe']} |")
        lines.append("")

        mv = ef["min_vol"]
        lines.append("## 最小波动率组合\n")
        lines.append("| 资产类别 | 权重 |")
        lines.append("|---------|------|")
        for i, name in enumerate(asset_names):
            lines.append(f"| {name} | {mv['weights'][i]}% |")
        lines.append("")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 预期年化收益 | {mv['return']}% |")
        lines.append(f"| 预期年化波动率 | {mv['volatility']}% |")
        lines.append(f"| 夏普比率 | {mv['sharpe']} |")
        lines.append("")

    # ── 风险平价 ──
    if "risk_parity" in results:
        rp = results["risk_parity"]
        lines.append("## 风险平价组合\n")
        lines.append("| 资产类别 | 权重 |")
        lines.append("|---------|------|")
        for name in asset_names:
            lines.append(f"| {name} | {rp['weights'][name]}% |")
        lines.append("")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 预期年化收益 | {rp['return']}% |")
        lines.append(f"| 预期年化波动率 | {rp['volatility']}% |")
        lines.append(f"| 夏普比率 | {rp['sharpe']} |")
        lines.append("")

    # ── 相关性矩阵 ──
    if "correlation_matrix" in results:
        cm = results["correlation_matrix"]
        lines.append("## 资产相关性矩阵\n")
        lines.append(f"平均相关性: {cm['avg_correlation']}\n")
        # 简短格式
        lines.append("| | " + " | ".join(cm["assets"]) + " |")
        lines.append("|" + "|".join(["---"] * (len(cm["assets"]) + 1)) + "|")
        for i, row in enumerate(cm["matrix"]):
            vals = " | ".join([f"{v:.2f}" for v in row])
            lines.append(f"| {cm['assets'][i]} | {vals} |")
        lines.append("")

    if "valuation_headwind" in results:
        lines.append("## 估值逆风情景\n")
        lines.append("| 情景 | 组合预计影响 |")
        lines.append("|------|-------------:|")
        for scenario in results["valuation_headwind"]["scenarios"]:
            lines.append(f"| {scenario['scenario']} | {scenario['portfolio_impact']} |")
        lines.append("")

    # ── 情景分析 ──
    if "scenario_analysis" in results:
        sa = results["scenario_analysis"]
        lines.append("## 回撤情景分析\n")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 基准预期收益 | {sa['base_case']['expected_return']} |")
        lines.append(f"| 基准预期波动率 | {sa['base_case']['expected_volatility']} |")
        lines.append(f"| 基准夏普比率 | {sa['base_case']['sharpe']} |")
        lines.append("")
        lines.append("| 情景 | 股市跌幅 | 组合预计回撤 |")
        lines.append("|------|---------|------------|")
        for sc in sa["scenarios"]:
            lines.append(f"| {sc['scenario']} | {sc['equity_decline']} | {sc['portfolio_drawdown']} |")
        lines.append("")

    # ── 对比总结 ──
    if all(k in results for k in ["benchmark", "efficient_frontier", "risk_parity"]):
        lines.append("## 组合对比\n")
        lines.append("| 组合 | 预期收益 | 预期波动率 | 夏普比率 |")
        lines.append("|------|---------|-----------|---------|")
        bm = results["benchmark"]
        ms = results["efficient_frontier"]["max_sharpe"]
        rp = results["risk_parity"]
        lines.append(f"| 基准组合 | {bm['return']}% | {bm['volatility']}% | {bm['sharpe']} |")
        lines.append(f"| 最大夏普 | {ms['return']}% | {ms['volatility']}% | {ms['sharpe']} |")
        lines.append(f"| 风险平价 | {rp['return']}% | {rp['volatility']}% | {rp['sharpe']} |")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
