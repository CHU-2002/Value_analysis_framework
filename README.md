# Value Analysis Framework（价值分析框架）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-brightgreen.svg)](tests/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

一句话：这是一个分析 A 股、港股、美股公司的工具。**Python 负责取数据和算数字，AI Agent 负责读年报和写判断**，最后给你一份公司研究报告。

## 它到底能做什么

- **取数**：从 Tushare、yfinance 拉财报、分红、股东、质押、无风险利率等数据。
- **读年报**：下载年报 PDF，自动切出管理层讨论、公司治理、重要事项等章节。
- **做分析**：内置四套流程——龟龟策略、价值分析、通用估值、组合配置。
- **出报告**：结果先存成统一格式的文件，再生成 Markdown 或 HTML 报告。

## 它是怎么工作的

1. **Python 先把所有数字算好**并存成文件，同样的输入永远得到同样的结果。
2. **AI 只负责阅读和判断**，不参与计算，也不会自己改数字。
3. **每个判断都能追到出处**，AI 拿到的材料有长度上限，不会把整份年报一次性塞进去。
4. **要么全用新结果，要么整体退回旧报告**，不会新旧混着用。

更细的设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 快速开始

### 环境要求

- Python 3.10 或更高
- [Tushare Pro](https://tushare.pro/) 账号和 Token（取 A 股数据时需要）
- [Claude Code](https://claude.com/claude-code) 或 [OpenCode](https://opencode.ai)（用 slash command 时需要）

### 安装

```bash
git clone https://github.com/CHU-2002/Value_analysis_framework.git
cd Value_analysis_framework

# 一键搞定：建虚拟环境、装依赖、检查 Token、跑测试
bash init.sh
```

想更新依赖时：

```bash
git pull
bash init.sh --force-install
```

### 配置 Tushare Token

```bash
cp .env.sample .env
# 打开 .env，把 TUSHARE_TOKEN=your_token_here 换成你自己的
```

也可以直接用环境变量：

```bash
export TUSHARE_TOKEN='your_token_here'
```

> `.env` 已被 `.gitignore` 忽略，请不要把 Token 提交到仓库。

### 验证是否装好

```bash
.venv/bin/python -m pytest -q
```

## 怎么用

### 在 Claude Code / OpenCode 里用 slash command

在对话框里输入下面这些命令即可，`{code}` 换成股票代码：

| 命令 | 作用 | 需要先做什么 |
|------|------|--------------|
| `/download-report {code}` | 搜索并下载最近一年的年报 PDF | 无 |
| `/business-analysis {code}` | 从 6 个角度分析公司（AI 主要工作在这里） | 无 |
| `/turtle-analysis {code}` | 龟龟策略：算穿透回报率和估值 | 先跑 `/business-analysis` |
| `/value-analysis {code}` | 价值分析：现金流折现、收购视角 | 先跑 `/business-analysis` |
| `/valuation {code}` | 通用估值：DCF、DDM、可比公司、Graham | 先跑 `/business-analysis` |
| `/portfolio-strategy {profile}` | 组合配置：画像 → 宏观 → 配置 → 选标的 → 风险 | 无 |

示例：

```text
/business-analysis 600887
/value-analysis 600887
/portfolio-strategy 30岁 50万人民币 风险中高 长期
```

### 直接跑 Python 脚本

**取数据**

```bash
.venv/bin/python scripts/tushare_collector.py --code 600887.SH
.venv/bin/python scripts/tushare_collector.py --code 00700.HK --output output/data_pack_market.md
.venv/bin/python scripts/tushare_collector.py --code 600887 --dry-run
```

**解析年报 PDF**

```bash
.venv/bin/python scripts/pdf_preprocessor.py --pdf report.pdf --output output/pdf_sections.json
```

会提取这几类章节：管理层讨论（MDA）、公司治理（GOV）、重要事项（MATTERS）、受限资产（P2）、应收账款账龄（P3）、关联交易（P4）、或有负债（P6）、非经常性损益（P13）、子公司（SUB）。

**预先算好数字**

```bash
# 价值分析
.venv/bin/python scripts/value_analysis_engine.py --code 600887 --output-dir output/600887_伊利

# 通用估值
.venv/bin/python scripts/valuation_engine.py --code 600887 --output-dir output/600887_伊利

# 组合配置
.venv/bin/python scripts/portfolio_engine.py --mode full --profile output/portfolio_xxx/profile.md
```

**选股与导出报告**

```bash
.venv/bin/python scripts/screener_core.py --tier1-only
.venv/bin/python scripts/report_to_html.py --input report.md --output report.html --standalone
.venv/bin/python scripts/md_to_mobile_html.py --input report.md --output mobile.html
```

## 结构化定性结果管线（`scripts/results/`）

这是本框架和一般 prompt 集合最大的不同：不会把整份年报丢给一个 AI 一次性读完，而是拆成几步。

1. **准备（prepare）**：扫描已经取好的数据、年报章节和 PDF，建立证据索引，记录每个文件的 SHA-256，并给每个模块切出一份长度受控的材料。
2. **分模块分析**：每个 AI 只读自己那份材料，输出统一的 `result.json`（给机器看）和 `report.md`（给人看）。固定模块有 `business_moat`、`environment`、`governance`、`mda_quality`，按需启用 `holding_structure`。
3. **对账（reconcile_results）**：检查各模块的时间口径、参数有没有冲突，交叉核对治理和护城河。
4. **汇总（synthesis）**：由一个汇总 AI 重新写最终结论。
5. **交付（resolve_qualitative）**：下游的 Turtle / Value / Valuation / Portfolio 统一从这里取结果。

```bash
.venv/bin/python -m scripts.results.prepare --output-dir output/600887_伊利 --ticker 600887 --company 伊利
.venv/bin/python -m scripts.results.reconcile_results --input ... --output output/600887_伊利/synthesis/reconciliation.json
.venv/bin/python -m scripts.results.synthesis --input ... --output output/600887_伊利/synthesis/context.json
.venv/bin/python -m scripts.results.resolve_qualitative --output-dir output/600887_伊利 --ticker 600887
```

几条硬规矩：

- 优先使用完整、同一次运行、同一家公司、输入没变过的结果。
- 有 `run_manifest.json` 时绝不退回旧 Markdown。
- 只有没有 manifest 的旧目录，才允许整体退回 `qualitative_report.md`。
- `source=unavailable` 时命令以退出码 `3` 结束（`2` 表示参数写错了）。

## 项目结构

```
Value_analysis_framework/
├── .claude/                      # Claude Code 的 slash commands 和 skills
├── .opencode/commands/           # OpenCode 的 slash commands
├── .github/                      # CI、PR/Issue 模板、CODEOWNERS、管理员配置
├── docs/                         # 架构与开发文档
├── notebooks/                    # 选股 Jupyter notebooks
├── prompts/                      # v1 遗留提示词（只读）
├── ciguttprepare/                # 烟蒂策略提示词草稿
├── scripts/
│   ├── results/                  # 结构化结果管线
│   ├── tushare_modules/          # Tushare 模块化实现
│   ├── tushare_collector.py      # 取数入口
│   ├── pdf_preprocessor.py       # 年报章节提取
│   ├── value_analysis_engine.py  # 价值分析预计算
│   ├── valuation_engine.py       # 通用估值预计算
│   ├── portfolio_engine.py       # 组合预计算
│   ├── screener_core.py          # 两级选股器
│   └── report_to_html.py         # Markdown 转 HTML
├── shared/qualitative/           # 共享定性模块
├── strategies/                   # turtle / value / valuation / portfolio
├── tests/                        # pytest 测试和 mock 数据
├── output/                       # 运行输出（已 gitignore）
├── init.sh                       # 环境初始化脚本
└── requirements.txt
```

## 测试

```bash
# 全部测试
.venv/bin/python -m pytest -q

# 只跑一个文件
.venv/bin/python -m pytest tests/test_results_pipeline.py -v

# 失败就停
.venv/bin/python -m pytest -x -q

# 看覆盖率
.venv/bin/python -m pytest --cov=scripts --cov-report=term-missing
```

所有测试都用 mock 数据，**不需要 Tushare Token**。

## 参与贡献

`main` 分支受保护，**所有改动都必须通过 Pull Request 合入，不能直接 push**。

- 提交 PR 后会自动跑 CI（测试、编译检查、PR 标题规范）。
- 合并前至少需要 1 个 review 通过。
- 管理员在 `.github/admins.yml` 里配置，改名即生效；管理员可直接合并 PR。
- 详细的开发流程、分支命名和提交规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 提交信息请遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)。
- 参与讨论请遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- 发现安全问题请按 [SECURITY.md](SECURITY.md) 私下报告，不要开公开 Issue。

## 版本与变更

- 当前版本历史见 [CHANGELOG.md](CHANGELOG.md)
- 上游龟龟框架 v1 → v3 的演进见 [CHANGELOG_V2.md](CHANGELOG_V2.md)

## 许可证与致谢

本项目采用 **MIT** 许可证，详见 [LICENSE](LICENSE)。

本项目是基于开源项目 [terancejiang/Turtle_investment_framework](https://github.com/terancejiang/Turtle_investment_framework)（作者 ying.j，MIT 许可）的衍生作品，在原龟龟投资框架的基础上扩展了价值分析、组合策略和结构化定性结果管线。

- 上游项目：<https://github.com/terancejiang/Turtle_investment_framework>
- 上游作者：ying.j &lt;erl4780@dingtalk.com&gt;
- 上游许可证：MIT

根据 MIT 许可要求，原始版权声明与许可声明已保留在 [LICENSE](LICENSE) 中，衍生关系说明见 [NOTICE](NOTICE)。

> **免责声明**：本项目仅用于研究与学习，不构成任何投资建议。市场有风险，决策需谨慎。
