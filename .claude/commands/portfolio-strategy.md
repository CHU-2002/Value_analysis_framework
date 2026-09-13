# /portfolio-strategy — 综合资产配置策略 v2.0

## Usage
```
/portfolio-strategy [user profile description]

Examples:
  /portfolio-strategy 我20岁，有50万人民币，能接受高风险，想长期投资
  /portfolio-strategy 年龄30，资产100万，风险中等，有港股通账户，已持有茅台和腾讯
  /portfolio-strategy --full 65岁退休人士，200万资产，保守型，需要稳定现金流
```

## Description
运行完整的综合资产配置策略 pipeline（v2.0）。基于用户画像（年龄、资产规模、风险承受能力、投资期限、流动性需求、已有持仓），输出包含以下内容的完整资产配置方案：

将 `$ARGUMENTS` 作为用户画像和模式参数传给 coordinator。若为空，先询问总资产和年龄；不要使用示例画像代替用户输入。

读取 `strategies/portfolio/coordinator.md` 并执行完整流程。`profile.md` 必须包含 `portfolio_schema.md` 定义的七键 `组合引擎基准权重` 表；Python 预计算使用 `portfolio_engine.py --mode full --profile ...`。

1. **用户画像评估** — 风险等级、桶策略拆分
2. **宏观+估值周期评估** — 结合当前经济周期 + 各市场历史估值分位的配置建议
3. **战略资产配置** — 7 核心类（A股精选 / 港股精选 / 美股精选 / 中债 / 美债 / 黄金 / 现金）+ 2 条件类的集中权重
4. **战术标的选择** — 精选个股为主，ETF 仅为过渡。总标的数 8-12 支。
5. **组合风险检查** — 集中度接受、因子/行业检查、回撤情景、估值逆风情景
6. **实施路线图** — 估值感知建仓（高位→现金为主）、再平衡规则、监控KPI

> **v2.0 核心理念**：巴菲特式集中投资。不追求资产类别数量，精选 5-8 个你真正理解的标的。估值高位时不为了填表格而建仓。

## Options
- `--full` : 全量重建（默认模式，首次使用或年度检视时用）
- `--incremental` : 增量更新（保留现有配置，仅更新市场数据和持仓复查）

## Prerequisites
- 建议先对你关注的个股运行 `/business-analysis` 和 `/value-analysis`（需要更细的估值时加跑 `/valuation`）
- 如未分析，策略报告会标注"待分析"，并给出分析优先级
- 扫描每个带有效 `run_manifest.json` 或可验证 ticker 目录名的公司目录时运行 `.venv/bin/python -m scripts.results.resolve_qualitative --output-dir "{company_output_dir}" --ticker "{ticker}" --output "{company_output_dir}/qualitative_input.json"`；退出码 3 表示该标的定性结果不可用并应标记“待分析”，退出码 2 表示命令参数错误；仅在 resolver 选择 `source=legacy` 时解析 `qualitative_report.md`

## Output Location
`output/portfolio_{timestamp}/`
