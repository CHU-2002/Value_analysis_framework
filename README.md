# Value Analysis Framework（价值分析框架）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-brightgreen.svg)](tests/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

一句话：这是一个分析 A 股、港股、美股公司的工具。**Python 负责取数据和算数字，AI Agent 负责读年报和写判断**，最后给你一份公司研究报告。

## 它到底能做什么

- **取数**：从 Tushare、yfinance 拉财报、分红、股东、质押、无风险利率等数据。
- **一次拉全**：按自选股清单把所需数据一次拉进统一原始仓（不过期、可整体拷走），之后**断网**也能从仓重建数据包。
- **读年报**：下载年报 PDF，自动切出管理层讨论、公司治理、重要事项等章节。
- **做分析**：内置两大流程——价值分析（含通用估值子模块）与组合配置。
- **定买卖计划**：对确定长期持有的公司，输出四档买入价格/资金比例、单日 30% 上限、极端高估卖出价及当前执行指令；固定财报期价值基准，不随每日股价漂移。
- **出报告**：结果先存成统一格式的文件，再生成 Markdown 或 HTML 报告。

## 它是怎么工作的

1. **Python 先把所有数字算好**并存成文件，同样的输入永远得到同样的结果。
2. **AI 只负责阅读和判断**，不参与计算，也不会自己改数字。
3. **每个判断都能追到出处**，AI 拿到的材料有长度上限，不会把整份年报一次性塞进去。
4. **要么全用新结果，要么整体退回旧报告**，不会新旧混着用。

更细的设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 快速开始

### 环境要求

- Python 3.10 或更高（CI 默认只核验 3.12；3.10 用 Actions 的 `workflow_dispatch` 按需核）
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
| `/download-report {code}` | 搜索并下载定期报告 PDF（年报默认近 3 年；支持中报/一季报/三季报与"最新一期"） | 无 |
| `/business-analysis {code}` | 从 6 个角度分析公司（AI 主要工作在这里） | 无 |
| `/update-analysis {code}` | 定期报告增量更新：拉最新期次、更新已有结论、另出独立「经营变化报告」 | 先跑 `/business-analysis` |
| `/value-analysis {code}` | 价值分析：现金流折现、收购视角、可执行分批买入与卖出计划 | 先跑 `/business-analysis` |
| `/valuation {code}` | 价值分析子模块 · 通用估值：DCF、DDM、可比公司、Graham（可单独调用） | 先跑 `/business-analysis` |
| `/portfolio-strategy {profile}` | 组合配置：画像 → 宏观 → 配置 → 选标的 → 风险 | 无 |

示例：

```text
/business-analysis 600887
/update-analysis 600887 2026H1
/value-analysis 600887
/portfolio-strategy 30岁 50万人民币 风险中高 长期
```

### 定期报告增量更新

公司发新一期财报后，不必重跑完整分析：

```bash
# 1) 判断是否需要更新、更新哪一级
.venv/bin/python scripts/analysis_status.py --company-dir output/600887_伊利 --ticker 600887 --json

# 2) 新建 run、拉取最新期次、重跑模块与 period_delta、更新结论并出变化报告
#    完整步骤见 shared/qualitative/coordinator_update.md 与 /update-analysis
```

- `/update-analysis` 的增量 run 与 `scripts/runs.py adopt` 接管的基线 run 是**不可变 run**，落在 `output/{code}_{company}/runs/{run_id}/`，输入是 run 私有快照；行情刷新与新报告不会污染历史 run。（基线 `/business-analysis` 仍写扁平布局，可用 `runs.py adopt` 转成 run。）
- `history.jsonl` 是追加式台账，`latest.json` 是当前生效指针，`record.json` 是给人看的分析记录卡。
- 变化报告（`change_report_{period}.md`）是独立交付物：只说明最近一段时间经营状况发生了怎样的改变，以及上一版结论是否改变。
- 增量更新后 `value_computed.json` / `buy_sell_basis.json` 仍属旧财报期，`analysis_status` 会返回 `stale:downstream_stale`（退出码 1）；重跑 `/value-analysis` 与 `/buy-sell-plan` 后用 `scripts/runs.py downstream --company-dir "{company_dir}" --fresh all` 清除标记，状态才回到 `up_to_date`。买卖计划不会自动改写。

### 当前价值报告（REQ-010）

`latest.json` 只指向分析 run；价值报告另有一个指针 `value_report.json`，它记录来源 run、财报期与报告摘要，指向 `value_reports/{run_id}/{sha12}/report.md` 这份不可变副本。

```bash
# 读取「当前价值分析报告」的唯一入口；退出码 0 最新 / 1 已过期 / 3 不可用
.venv/bin/python scripts/value_publication.py read --company-dir output/600887_伊利 --json

# /value-analysis 收尾：校验产物 → 冻结历史副本 → 原子更新指针
.venv/bin/python scripts/value_publication.py publish --company-dir output/600887_伊利

# 按 run 追溯历史版本（同一 run 重算会另存一份，旧版本字节不变）
.venv/bin/python scripts/value_publication.py resolve --company-dir output/600887_伊利 --run-id <run_id>
```

- 发布前会解析到**最新成功且可消费**的分析 run；产物不完整（报告缺失/占位、`value_computed.json` 不可解析）时只写 `value_reports/failures.jsonl`，指针与前一份报告**一个字节都不改**。
- 失败但没有产物可校验时用 `scripts/value_publication.py fail --company-dir ... --reason ...` 登记本次失败。
- `analysis_status.py` 的输出带上 `latest_successful_run` 与 `value` 两个块：最新分析财报期晚于价值基准时 `value.state=stale`。

### 本地控制台采集归档（REQ-009.4）

采集只会在显式执行时访问数据源。原始响应默认长期存到仓库外的 `~/turtle_archive/`，可用
`WEBUI_ARCHIVE_ROOT` 或 `python -m scripts.webui --archive-root <目录>` 更改；浏览控制台不会触发采集。

```bash
# 查看本地控制台
make gui

# 日常低配额档案；标的和期次可重复传入
make gui-collect ARGS='--profile frugal --ticker 600887.SH --period 20260630 --yes'

# bulk 会先显示请求数并要求确认；确认后显式加 --yes
make gui-collect ARGS='--profile bulk --ticker 600887.SH --period 20260630 --yes --tier-label 租用账号'
```

采集前需在 `.env` 配置 `TUSHARE_TOKEN` 或导出同名环境变量。中断后用原 `--batch-id` 恢复；
已有成功存档默认跳过，只有显式 `--force` 才覆盖。

### 统一原始数据仓与一次拉全（REQ-011）

远程原始响应现在落进**仓库之外**的唯一原始仓：`~/turtle_archive/store.db`（SQLite 单文件）加一份
append-only 的 `manifest.jsonl`。它按「标的 × 数据集 × 期次 × 参数」唯一，**不过期、不主动删、可整体拷走**；
删除只有一个显式动作（`wipe`）且要二次确认。旧的文件缓存 `output/.collector_cache/` 已**停写**（只读保留），
既有条目可用 `make data-import-legacy` 一次性幂等导入。

```bash
# 自选股清单：决定「要维护哪些公司」，拉取目标由它推导
make data-universe ARGS='add --ticker 600887.SH --name 伊利股份'

# 一次动作全量拉取：先给调用量预估，显式确认后才发请求
make data-pull ARGS='--profile bulk --yes'

# 只补缺口：不动已完成的部分
make data-pull ARGS='--only-gaps --yes'

# 从仓离线重建产物（不联网、不花钱）
make data-rebuild ARGS='--ticker 600887.SH'

# 缺口与完备度 / 仓的规模与 schema 自检 / 导入旧存档与旧缓存
make data-gaps ARGS='--ticker 600887.SH'
make data-check
make data-import-legacy
```

这些目标都转发到 `python -m scripts.datalayer …`（子命令 `universe` / `pull` / `rebuild` / `gaps` /
`import-legacy` / `export` / `check` / `wipe`），退出码 `0` 成功、`2` 用法或前置错误（含未配 token、未确认）、
`4` 仓不可用、`130` 中断。取数只有一个收口点：`scripts/datalayer/access.py` 的 `DataAccess`，
`scripts/tushare_collector.py` 的 `_safe_call` 只剩一行转调，重试、限流与 VIP 路由都搬进了 `DataAccess`；
重建是**纯离线**动作，产物 `output/<公司>/data_pack_market.md` 的小节与表头契约不变。

`make gui-collect ARGS=…` 仍然保留，参数与退出码语义不变，但内部已改为薄转调数据层
（落盘从旧存档目录换成统一原始仓）。仓根与 `make data-*` 走**同一套解析**：`TURTLE_ARCHIVE_ROOT`
优先，其次 `WEBUI_ARCHIVE_ROOT`（控制台的 `--archive-root` 与它同源），默认 `~/turtle_archive`；
`make data-*` 另可用 `--store` 显式指定——两条命令行不会各指一个仓。
联网边界不变：**不引入任何定时 / 自动拉取，联网只能由显式动作触发**。

### 本地控制台 · 按键与视图（REQ-009.1 / REQ-009.2 / REQ-012）

数据获取页与工作台提供「添加公司」「从既有产物导入」「移除公司」三个自选股清单动作。
添加支持完整代码、裸代码或公司别名；导入扫描已接入的公司产物，显示新增与跳过家数及原因，
重复导入不会重复添加。移除只停止跟踪，保留全部公司产物。三个动作均离线，不消耗数据源配额。

`make gui` 打开后，**默认落在「工作台」**（不是某个业务面板）：左侧导航按用途分组
（工作台 / 数据 / 公司 / 任务 / 分析，共 9 页 23 个面板），一屏看全这个项目能做什么：

| 页面 | 你能做什么 |
|------|------------|
| 工作台 | 每行一家公司：显示名 / 数据截止期次与拉取时间 / 最近一次分析 / 状态与待办；下面是待办汇总与直达动作。空库给引导而不是空表 |
| 数据获取 | 自选股清单与完备度、缺口下钻（按标的/期次/数据集）、存储概览，以及**「拉取」与「离线重建」两个并排的动作**。数据经 `datalayer` 的读接口读取 |
| 公司列表（全部） | 列出 `output/` 下的公司目录与最近一次 run / 财报期；点公司名进入它的图表页 |
| 图表 | 年度股价走势、关键财务指标趋势、营收与归母净利润——数据全部来自本地 `data_pack_market.md`，断网也能看；**按口径分组**（年度/半年/单季，默认年度）并可切换，图上标明单位与口径，支持导出 PNG 与复制 TSV |
| 报告 | 按类型列出产物（报告 / 数据包 / PDF / 买卖计划…），点开在面板内阅读；Markdown **先转义再渲染**，报告里的原始 HTML 不生效 |
| 迭代记录 | 按时间**倒序**列出每次 run，标出当前生效的 run，并展示变化型 run 相对被取代 run 的结论变化 |
| 任务 | 队列与历史任务：进度、取消、重试、折叠日志、失败原因与产出链接；「现在能做的事」按当前公司列出可用动作，禁用时给人话理由 |
| 采集存档 | 严格只读：批次进度、完备度与缺口清单；真正的采集只由显式命令触发（`REQ-009.4` 的兼容视图，**保留**） |
| 生成报告 | `REQ-013` 的一键页（让 agent CLI 跑分析，消耗模型额度） |

三条 `REQ-012` 的主线：

- **当前公司是全局上下文**：右上角选一次，图表 / 报告 / 迭代记录自动带上；写进 URL（ticker），
  刷新与前进后退一致；**没选公司是正常空状态**（「先选一家公司」），不是报错。
  全站只有一个显示名（`600887 伊利股份`），目录名与 ticker 只作技术标识出现在详情里。
- **动作层**：面向「事」而不是命令——「更新这家公司的分析」「拉取全部数据」「只补缺口」
  「从原始仓重建数据包」。参数由**服务端**从上下文解析成绝对路径与规范标识，界面不出现路径与
  CLI 开关；会联网/花配额的动作必须二次确认（取消不发任何请求）；「更新分析」中间**交接**给
  agent（给可复制的命令与路径，任务等待、不占并发），用户确认后服务端校验该步声明的产物才继续。
- **任务中心**：一个动作 = 一个任务，进度是「已完成步数 / 总步数」；并发满了**先排队**
  （`max_queued_jobs`，默认 20，`0` 等于不排队即拒）；离开页面任务继续跑；
  失败给「发生了什么 + 怎么办」且**原始日志不删**，失败可重试。

点按键后能看到实际执行的命令行、运行状态、退出码与 stdout/stderr 尾部；任务历史落盘，
面板重启后仍可查看。命令**不经过 shell**，参数里的 `;` `|` `&&` `$(` 与换行一律拒绝。
按键白名单共 **28 个**：`plugins/commands.py::ENTRIES` 的 25 个（既有脚本入口 + `runs` 的 6 个子命令
+ 定性管线 4 步 + `REQ-012` 新增的数据层 2 个：`datalayer_pull` / `datalayer_rebuild`）
加上 `REQ-013` 注册的 3 个 agent 动作；参数表由各脚本 `argparse` 源码扫描得出，不手抄。
`datalayer_pull` / `datalayer_rebuild` 在表里写的是源码路径 `datalayer/cli.py`（脚本自检需要它），
实际用 `python -m scripts.datalayer` 调用——`cli.py` 内部是相对导入，裸路径执行会 `ImportError`。
并发任务上限 3，超限**先排队**（队列上限 `max_queued_jobs`，默认 20；
设成 `0` 即恢复「超限直接拒」的旧语义）。token 只从环境变量 / `.env` 读取，面板不接收、不回显。
降级卡与错误提示先给人话（「这块内容暂时看不到」+ 下一步），错误码收进折叠的「技术细节」。

想确认「这些页面真的能用」，可以让脚本用真实浏览器把上面这些动作跑一遍并留证
（截图 + 观察记录落 `output/.webui_walkthrough/<UTC>/`；加 `--headed` 就能自己看着它点；
`--action` 选要走哪个动作，默认 `data.rebuild`——**离线、不联网、不花钱**）：

```bash
make gui                                                          # 一个终端
.venv/bin/python scripts/gui_walkthrough.py --base http://127.0.0.1:8765   # 另一个终端
```

### 直接跑 Python 脚本

**取数据**

```bash
.venv/bin/python scripts/tushare_collector.py --code 600887.SH
.venv/bin/python scripts/tushare_collector.py --code 00700.HK --output output/data_pack_market.md
.venv/bin/python scripts/tushare_collector.py --code 600887 --dry-run
```

**下载定期报告**

```bash
# 年报（默认近 3 年）
.venv/bin/python scripts/download_report.py --stock-code 600887 --report-type 年报 --save-dir output/600887_伊利

# 最新一期（自动判定 2026H1 / 2026Q1 / 2025FY ...）
.venv/bin/python scripts/download_report.py --stock-code 600887 --report-type auto --save-dir output/600887_伊利

# 补齐某期次之后的所有已发布期次，并写 sources_index.json（已有期次自动跳过，--force 强制重下）
.venv/bin/python scripts/download_report.py --stock-code 600887 --report-type auto --since 2026Q1 --save-dir output/600887_伊利

# 只看最新一期是什么
.venv/bin/python scripts/discover_report.py --stock-code 600887 --report-type auto
```

**解析年报 PDF**

```bash
.venv/bin/python scripts/pdf_preprocessor.py --pdf report.pdf --output output/pdf_sections.json
```

会提取这几类章节：管理层讨论（MDA）、公司治理（GOV）、重要事项（MATTERS）、受限资产（P2）、应收账款账龄（P3）、关联交易（P4）、或有负债（P6）、非经常性损益（P13）、子公司（SUB）。

**预先算好数字**

```bash
# 价值分析（只产出报告与冻结估值，默认不生成买卖计划）
.venv/bin/python scripts/value_analysis_engine.py --code 600887 --output-dir output/600887_伊利

# 买卖计划（触发式：看完报告后再决定；采集当时行情 + 离线生成，无资金参数时输出百分比）
.venv/bin/python scripts/buy_sell_plan.py --code 600887 --output-dir output/600887_伊利

# 纯离线重放已写入的行情与基准
.venv/bin/python scripts/buy_sell_engine.py --output-dir output/600887_伊利

# 通用估值
.venv/bin/python scripts/valuation_engine.py --code 600887 --output-dir output/600887_伊利

# 组合配置
.venv/bin/python scripts/portfolio_engine.py --mode full --profile output/portfolio_xxx/profile.md
```

买卖计划是触发式的：主流程只产出报告与冻结的 `value_computed.json`，不自动生成计划。阅读报告后运行 `/buy-sell-plan {ticker}`（或 `scripts/buy_sell_plan.py`）才采集当时行情并生成 `buy_sell_market.json`、`buy_sell_plan.json` 和 `buy_sell_plan.md`，报告再原样引用。已有实际成交时使用可选 `buy_sell_state.json`，重复运行不会被当作成交；缺失关键价格/日期时明确暂停交易。基准冻结、卖出确认、硬退出和输入示例见 [买卖计划合同](docs/BUY_SELL_CONTRACT.md)。独立 `/valuation` 仍是研究估值入口，不覆盖主流程的交易计划。

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
5. **交付（resolve_qualitative）**：下游的价值分析、通用估值（价值分析子模块）、组合配置统一从这里取结果。

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
├── docs/                         # 架构、需求、测试与开发文档
│   └── requirements/             # 需求条目与台账（REQ-NNN，需求唯一权威来源）
├── notebooks/                    # 选股 Jupyter notebooks
├── prompts/                      # v1 遗留提示词（只读）
├── ciguttprepare/                # 烟蒂策略提示词草稿
├── scripts/
│   ├── datalayer/                # 统一原始数据仓：取数收口、全量拉取、离线重建、旧数据导入
│   ├── results/                  # 结构化结果管线（含 change_report）
│   ├── tushare_modules/          # Tushare 模块化实现
│   ├── tushare_collector.py      # 取数入口
│   ├── discover_report.py        # 定期报告链接发现（CNINFO 四类 + 10jqka 兜底）
│   ├── download_report.py        # 定期报告 PDF 下载（年报/中报/一季报/三季报）
│   ├── periods.py                # 期次标识（2026Q1/H1/Q3/FY）解析与推算
│   ├── version.py                # 框架版本与提示词/代码指纹
│   ├── runs.py                   # run-store：new/resolve/finish/adopt/export/downstream
│   ├── analysis_status.py        # 更新判定：最新 / 需增量 / 需全量重跑
│   ├── value_publication.py      # 当前价值报告：publish/read/resolve/fail + 指针与历史
│   ├── pdf_preprocessor.py       # 年报章节提取（按期次产出）
│   ├── value_analysis_engine.py  # 价值分析预计算
│   ├── buy_sell_plan.py          # 触发式买卖计划：采集当时行情 + 离线生成
│   ├── buy_sell_engine.py        # 离线确定性买卖计划 JSON / Markdown
│   ├── buy_sell_inputs.py        # 既有估值/行情输入适配
│   ├── market_sessions.py        # 交易所本地日期与会话收盘切点
│   ├── valuation_engine.py       # 通用估值预计算
│   ├── portfolio_engine.py       # 组合预计算
│   ├── screener_core.py          # 两级选股器
│   └── report_to_html.py         # Markdown 转 HTML
├── shared/qualitative/           # 共享定性模块（coordinator_v2 / coordinator_update）
├── strategies/                   # value（含 valuation 子模块）/ portfolio
├── tests/                        # pytest 测试和 mock 数据
├── output/                       # 运行输出（已 gitignore）
├── Makefile                      # 本地校验入口（make verify / cov / unit / trace）
├── init.sh                       # 环境初始化脚本
└── requirements.txt
```

## 测试

```bash
make verify   # 本地全部门禁：编译/空白检查 + 全量测试 + 覆盖率 + 追溯 + scope + 回归门禁
make test     # 全量测试
make unit     # 只跑快层，日常迭代用
make cov      # 覆盖率报告与门禁（≥ 74%，基线 76.8%）

# 也可以直接用 pytest
.venv/bin/python -m pytest tests/test_results_pipeline.py -v
.venv/bin/python -m pytest -x -q
```

所有测试都用 mock 数据，**不需要 Tushare Token**。测试分层、覆盖率门禁与需求追溯规则见
[docs/TESTING.md](docs/TESTING.md)。

## 参与贡献

`main` 受保护，**所有改动都必须通过 Pull Request 合入，不能直接 push**。
一个特性一条特性分支：子 PR 合进这条特性分支（CI 跑全量 + PR 里写手工自测记录），
特性做完后整支直接合进 `main` 并附一份独立验收报告；`main` 每累积 3 个特性补一次批量全量回归。

- 提交 PR 后会自动跑 CI（测试、编译检查、PR 标题规范）。
- 合并前至少需要 1 个 review 通过。
- 管理员在 `.github/admins.yml` 里配置，改名即生效；管理员可直接合并 PR。
- **要做什么、做到什么程度，以需求台账为准**：先看 [docs/requirements/](docs/requirements/README.md)，
  新功能请先登记 `REQ-NNN` 并写清可判定的验收标准。
- 开发流程与就绪/完成定义（DoR / DoD）见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)，
  测试策略见 [docs/TESTING.md](docs/TESTING.md)。
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
