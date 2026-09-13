# Value Analysis Framework (价值分析框架)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-brightgreen.svg)](tests/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

AI 辅助的 A 股 / 港股 / 美股基本面分析框架。**Python 负责确定性数据采集与计算，LLM Agent 负责定性判断与综合写作**，两者通过结构化结果协议衔接。

本项目是基于开源项目 [terancejiang/Turtle_investment_framework](https://github.com/terancejiang/Turtle_investment_framework) 的衍生作品，在其龟龟策略基础上扩展了**价值分析（Value Analysis）**、**组合配置（Portfolio）**与**结构化定性结果管线（evidence-first pipeline）**。来源与许可详见 [许可证与致谢](#许可证与致谢)。

---

## 目录

- [核心能力](#核心能力)
- [架构概览](#架构概览)
- [快速开始](#快速开始)
- [使用方法](#使用方法)
- [结构化定性结果管线](#结构化定性结果管线)
- [项目结构](#项目结构)
- [测试](#测试)
- [贡献](#贡献)
- [版本与变更](#版本与变更)
- [许可证与致谢](#许可证与致谢)

---

## 核心能力

| 能力 | 说明 |
|------|------|
| **多策略分析** | 龟龟策略、巴芒段价值分析、通用估值、组合配置四套独立工作流 |
| **证据优先的定性分析** | 先建立可定位的 `evidence/index.json`，再按模块生成有预算的上下文，避免整份年报进入单一 context |
| **结构化结果协议** | 统一 `investment.result` v1.0，模块输出 `result.json`（机器消费）+ `report.md`（人工审阅） |
| **确定性 / 判断分离** | 所有数字由 Python 预计算，LLM 只做解释与判断，不重算 |
| **数据血缘与可复现** | run manifest 记录输入与产物的 SHA-256，解析器校验同源、同主体、同输入 |
| **多市场支持** | A 股、港股、美股（含 `BRK.B` 类别股），报表币种与单位自动适配 |
| **组合引擎** | 7 类资产权重、有效前沿、风险平价、相关性矩阵、回撤与估值逆风情景 |
| **报告输出** | Markdown 报告 + 可选的桌面 / 移动端 HTML |
| **离线可测** | 完整 pytest 套件全部基于 mock 数据，无需 Tushare Token 即可运行 |

## 架构概览

```
                    ┌──────────────────────────────┐
   用户输入 ───────▶│  Slash Command / Python 脚本  │
                    └──────────────┬───────────────┘
                                   │
             ┌─────────────────────┼─────────────────────┐
             ▼                     ▼                     ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │ 数据采集 (Python) │  │ 年报解析 (Python) │  │ 定性分析 (LLM)   │
   │ Tushare + yfinance│  │ pdfplumber       │  │ evidence-first   │
   └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
            └────────────┬────────┴─────────────────────┘
                         ▼
            ┌────────────────────────────┐
            │  结构化结果管线 scripts/results │
            │  prepare → modules → reconcile │
            │  → synthesis → resolve         │
            └───────────────┬────────────┘
                            ▼
            ┌────────────────────────────┐
            │  下游策略消费                 │
            │  Turtle / Value / Valuation  │
            │  / Portfolio                 │
            └────────────────────────────┘
```

设计原则：

1. **确定性计算与 LLM 判断分离** —— Python 产出可复现的数字，LLM 负责叙事与取舍。
2. **证据可追溯** —— 重要判断必须引用 `evidence_id`，证据索引在分析前固定。
3. **上下文有预算** —— 每个模块与最终汇总都有硬字符上限，并记录裁剪状态。
4. **整组原子回退** —— 结构化结果集不完整时整体回退到旧 Markdown，不混用参数。

更详细的模块划分与调用链见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 快速开始

### 环境要求

- Python >= 3.10
- [Tushare Pro](https://tushare.pro/) 账号及 API Token（A 股数据）
- 可选：`pdfplumber`（年报 PDF 解析）
- 运行环境：[Claude Code](https://claude.com/claude-code) 或 [OpenCode](https://opencode.ai)（slash command 工作流）

### 安装

```bash
git clone https://github.com/CHU-2002/Value_analysis_framework.git
cd Value_analysis_framework

# 一键初始化：创建 .venv、安装依赖、检查 Token、运行测试
bash init.sh
```

`init.sh` 会依次完成：

1. 查找系统中 Python >= 3.10 并创建 `.venv`
2. 安装 `requirements.txt` 依赖
3. 检查 `TUSHARE_TOKEN`
4. 运行测试验证环境

更新依赖：

```bash
git pull
bash init.sh --force-install
```

### 配置 Tushare Token

```bash
cp .env.sample .env
# 编辑 .env，填入：
# TUSHARE_TOKEN=your_token_here
```

或使用环境变量：

```bash
export TUSHARE_TOKEN='your_token_here'
```

> `.env` 已被 `.gitignore` 忽略，请勿提交任何 token。

### 验证

```bash
.venv/bin/python -m pytest -q
```

## 使用方法

### Slash Commands

在 Claude Code / OpenCode 中通过 slash command 驱动完整工作流：

| 命令 | 作用 | 前置条件 |
|------|------|----------|
| `/download-report {code}` | 搜索并下载最近年报 PDF | — |
| `/business-analysis {code}` | 6 维度定性分析（evidence-first 分层 Agent） | — |
| `/turtle-analysis {code}` | 龟龟策略：穿透回报率 + 估值 | `/business-analysis` |
| `/value-analysis {code}` | 巴芒段价值分析：现金流折现 + 收购视角 `EE` | `/business-analysis` |
| `/valuation {code}` | 通用估值：DCF / DDM / 可比 / Graham | `/business-analysis` |
| `/portfolio-strategy {profile}` | 组合策略：画像 → 宏观 → 战略配置 → 选标的 → 风险 | — |

示例：

```text
/business-analysis 600887
/value-analysis 600887
/portfolio-strategy 30岁 50万人民币 风险中高 长期
```

### 命令行脚本

**数据采集（A 股 / 港股 / 美股）**

```bash
.venv/bin/python scripts/tushare_collector.py --code 600887.SH
.venv/bin/python scripts/tushare_collector.py --code 00700.HK --output output/data_pack_market.md
.venv/bin/python scripts/tushare_collector.py --code 600887 --dry-run
```

输出 Markdown 覆盖 §1–§17 共 17 个数据段（基本信息、三大报表、分红、周线、财务指标、风险、无风险利率、回购、质押、衍生指标等）。

**年报解析**

```bash
.venv/bin/python scripts/pdf_preprocessor.py --pdf report.pdf --output output/pdf_sections.json
```

提取 9 个目标章节：

| 缩写 | 章节 |
|------|------|
| MDA | 管理层讨论与分析 |
| GOV | 公司治理 |
| MATTERS | 重要事项（收购重组、诉讼、担保、关联交易） |
| P2 | 所有权/使用权受限资产 |
| P3 | 应收账款账龄 |
| P4 | 关联方交易 |
| P6 | 或有负债 / 诉讼 / 担保 |
| P13 | 非经常性损益 |
| SUB | 主要控股参股公司 / 子公司 |

**确定性预计算**

```bash
# 价值分析：估值锚、Owner Earnings、情景、EE
.venv/bin/python scripts/value_analysis_engine.py --code 600887 --output-dir output/600887_伊利

# 通用估值：分类 + WACC + 各估值方法 + 敏感性表
.venv/bin/python scripts/valuation_engine.py --code 600887 --output-dir output/600887_伊利

# 组合引擎：有效前沿 / 风险平价 / 情景 / 相关性
.venv/bin/python scripts/portfolio_engine.py --mode full --profile output/portfolio_xxx/profile.md
```

**选股与报告输出**

```bash
.venv/bin/python scripts/screener_core.py --tier1-only
.venv/bin/python scripts/report_to_html.py --input report.md --output report.html --standalone
.venv/bin/python scripts/md_to_mobile_html.py --input report.md --output mobile.html
```

## 结构化定性结果管线

`scripts/results/` 是本框架区别于普通 prompt 集合的核心。定性分析不再把整份年报塞进一个 Agent，而是：

1. **`prepare`** —— 扫描 `data_pack_market.md`、`pdf_sections.json`、年报 PDF 与附注，建立证据索引，记录输入与产物的 SHA-256，并为每个模块生成有字符预算的 `contexts/{module}.json`。
2. **模块 Agent** —— 每个 Agent 只读取自己的 context，输出符合 `investment.result` v1.0 的 `modules/{module}/result.json` 与 `report.md`。核心模块：`business_moat`、`environment`、`governance`、`mda_quality`；条件模块：`holding_structure`。
3. **`reconcile_results`** —— 跨模块检查时间口径、参数冲突、治理与护城河的交叉影响，输出 `synthesis/reconciliation.json`。
4. **`synthesis`** —— 构建最终汇总上下文，由 Final Synthesis Agent 重新撰写叙事并输出 `synthesis/result.json`。
5. **`resolve_qualitative`** —— 下游消费者（Turtle / Value / Valuation / Portfolio）统一调用，校验完整 run 后输出 `qualitative_input.json`。

```bash
.venv/bin/python -m scripts.results.prepare --output-dir output/600887_伊利 --ticker 600887 --company 伊利
.venv/bin/python -m scripts.results.reconcile_results --input ... --output output/600887_伊利/synthesis/reconciliation.json
.venv/bin/python -m scripts.results.synthesis --input ... --output output/600887_伊利/synthesis/context.json
.venv/bin/python -m scripts.results.resolve_qualitative --output-dir output/600887_伊利 --ticker 600887
```

解析器保证：

- 优先使用完整、同 run、同主体、输入未变更的结构化结果集；
- 有 `run_manifest.json` 时绝不回退 Markdown；
- 无 manifest 的旧目录才允许原子回退到 `qualitative_report.md`；
- `source=unavailable` 时 CLI 以退出码 `3` 结束（`2` 表示命令参数错误）。

## 项目结构

```
Value_analysis_framework/
├── .claude/
│   ├── commands/                 # Claude Code slash commands
│   └── skills/                   # Claude Code skills
├── .opencode/commands/           # OpenCode slash commands
├── .github/                      # CI、PR 与 Issue 模板
├── docs/                         # 架构与开发文档
├── notebooks/                    # 选股器 Jupyter notebooks
├── prompts/                      # v1 遗留提示词（只读）
├── ciguttprepare/                # 烟蒂策略提示词草稿
├── scripts/
│   ├── results/                  # 结构化结果管线（schema/manifest/evidence/context/...）
│   ├── tushare_modules/          # Tushare 模块化实现（字段、财务报表、衍生指标、yfinance 回退）
│   ├── tushare_collector.py      # 数据采集门面
│   ├── pdf_preprocessor.py       # 年报章节提取
│   ├── discover_report.py        # 年报 PDF 链接发现（CNINFO 优先）
│   ├── download_report.py        # 年报 PDF 下载
│   ├── value_analysis_engine.py  # 价值分析预计算
│   ├── valuation_engine.py       # 通用估值预计算
│   ├── portfolio_engine.py       # 组合预计算
│   ├── screener_core.py          # 两级选股器
│   ├── split_data_pack.py        # 数据预分发 + D6 触发检查
│   └── report_to_html.py         # Markdown → HTML
├── shared/qualitative/           # 共享定性模块（agents / references / templates）
├── strategies/
│   ├── turtle/                   # 龟龟策略
│   ├── value/                    # 价值分析
│   ├── valuation/                # 通用估值
│   └── portfolio/                # 组合配置
├── tests/                        # pytest 测试套件 + mock 数据
├── output/                       # 运行输出（gitignored）
├── init.sh                       # 环境初始化
├── requirements.txt
├── LICENSE
├── NOTICE
├── README.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── SECURITY.md
└── CHANGELOG.md
```

## 测试

```bash
# 全量测试
.venv/bin/python -m pytest -q

# 单个文件
.venv/bin/python -m pytest tests/test_results_pipeline.py -v

# 失败即停
.venv/bin/python -m pytest -x -q

# 覆盖率
.venv/bin/python -m pytest --cov=scripts --cov-report=term-missing
```

测试覆盖数据采集、衍生指标、PDF 预处理、结构化结果管线、命令与 schema 合同、组合引擎数值以及各策略消费者。所有测试均使用 mock 数据，**无需 Tushare Token**。

## 贡献

欢迎提交 Issue 与 Pull Request。**本仓库的 `main` 分支受保护：所有改动必须通过 PR 合入。**

- 开发流程、分支命名与提交规范见 [CONTRIBUTING.md](CONTRIBUTING.md)
- 提交 PR 时请使用自动加载的 [PR 模板](.github/PULL_REQUEST_TEMPLATE.md)
- 请遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- 安全问题请按 [SECURITY.md](SECURITY.md) 私下报告

提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat(results): add evidence index integrity check
fix(portfolio): correct risk-parity convergence
docs(readme): rewrite project overview
test(value): cover owner-earnings edge cases
```

## 版本与变更

- 当前版本历史见 [CHANGELOG.md](CHANGELOG.md)
- 上游龟龟框架 v1 → v3 的架构演进见 [CHANGELOG_V2.md](CHANGELOG_V2.md)

## 许可证与致谢

本项目采用 **MIT** 许可证，详见 [LICENSE](LICENSE)。

本项目是基于开源项目 [terancejiang/Turtle_investment_framework](https://github.com/terancejiang/Turtle_investment_framework)（作者 ying.j，MIT 许可）的衍生作品，在原龟龟投资框架的基础上扩展了价值分析框架、组合策略以及结构化定性结果管线。

- 上游项目：<https://github.com/terancejiang/Turtle_investment_framework>
- 上游作者：ying.j &lt;erl4780@dingtalk.com&gt;
- 上游许可证：MIT

根据 MIT 许可要求，原始版权声明与许可声明已保留于 [LICENSE](LICENSE)，衍生关系说明见 [NOTICE](NOTICE)。

> **免责声明**：本项目仅用于研究与教育目的，不构成任何投资建议。市场有风险，决策需谨慎。
