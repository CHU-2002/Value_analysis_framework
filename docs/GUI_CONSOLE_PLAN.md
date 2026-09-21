# 图形化控制台设计（REQ-009）

> 本文写**怎么做**。要什么、做到什么程度以 [`docs/requirements/REQ-009-local-gui-console.md`](requirements/REQ-009-local-gui-console.md)
> 为准；两者不互相复制。开发流程见 [`docs/DEVELOPMENT.md`](DEVELOPMENT.md)，测试策略见 [`docs/TESTING.md`](TESTING.md)。

## 1. 一句话方案

**用 Python 标准库起一个只监听本机的小服务，服务一个手写的单页页面；页面上的按键调用既有脚本，
图表与报告都由服务从已经落盘的 `output/` 产物里读出来。**

不引入任何第三方依赖：不装 Web 框架、不装图表库、不装前端构建链（Node/npm）。

## 2. 为什么零依赖（取舍，写给不看代码的人）

| 方案 | 装什么 | 对本仓库的代价 | 结论 |
|------|--------|----------------|------|
| **标准库 `http.server` + 手写单页**（本设计） | 什么都不用装 | CI 与 scope 预算不变；断网可用；页面朴素 | **采用** |
| Flask/FastAPI + Vue/React + ECharts | 新增 Python 包 + 一整套 Node 工具链 | `requirements*.txt` 要改，CI 装包变慢，出错面从 1 处变 3 处 | 不采用 |
| Streamlit / Gradio | 几十个第三方包 | 界面是黑盒，几乎无法按仓库要求做全 mock 单元测试，直接冲击覆盖率与 scope 门禁 | 不采用 |

关键约束（实测 2026-09-21）：CI 只装 `requirements-test.txt`；测试 scope 预算为
**≤ 40 个测试文件、≤ 1600 条用例**，当前 33/40、1522/1600。任何新依赖或大批用例都会顶破门禁。

图表不引第三方库的代价与做法：用浏览器原生 **Canvas** 画折线/柱状，鼠标悬停显示数值由十几行
原生 JS 完成。够用，但**不做**缩放、十字光标、K 线皮肤——这些写进 REQ-009 的「不包含」。

## 3. 架构

```
浏览器（单页：按钮区 + 图表区 + 报告区 + 迭代时间线）
   │  fetch JSON / HTML
   ▼
本地 HTTP 服务  scripts/webui/server.py   ← 只监听 127.0.0.1
   ├── 读视图（只读 output/）：views.py  → 公司 / 产物 / 图表序列 / run 时间线 / 报告渲染
   └── 命令执行器：commands.py + jobs.py → subprocess（无 shell、白名单、并发上限）
                                              │
                                              ▼
                              既有脚本：scripts/*.py、python -m scripts.results.*
```

三条硬规矩：

1. **服务是入口不是副本**：按键只负责拼参数并调用既有脚本，业务逻辑一行都不在 `webui` 里重写。
2. **只读优先**：视图只读 `output/`，不改任何产物；写操作一律通过按键跑既有脚本完成。
3. **不经过 shell**：`subprocess.Popen(argv, shell=False)`，参数以列表传递。

## 4. 文件布局

```
scripts/webui/
├── __init__.py          # 版本常量
├── __main__.py          # python -m scripts.webui 入口（argparse：--host/--port/--no-browser）
├── server.py            # ThreadingHTTPServer + 路由 + 静态资源
├── commands.py          # 命令白名单（声明式数据：按键名、脚本、参数定义）
├── jobs.py              # 任务注册表：启动、状态、日志环形缓冲、取消、并发上限
├── views.py             # 只读视图：公司 / 产物索引 / 图表解析 / run 时间线
├── markdown_render.py   # 报告 Markdown → HTML（转义原始 HTML）
└── static/
    ├── index.html       # 单页骨架
    ├── app.js           # 交互与 Canvas 绘图（原生，无框架）
    └── style.css
```

`Makefile` 增加 `gui` 目标：`$(PYTHON) -m scripts.webui --port 8765`。
测试新增两个文件：`tests/test_webui_server.py`（REQ-009.1）、`tests/test_webui_views.py`（REQ-009.2），
均归 `unit` 层（无需登记 `TEST_LAYERS`）。

## 5. HTTP 接口

| 方法 | 路径 | 作用 | 归属 |
|------|------|------|------|
| GET | `/` | 返回单页 `index.html` | 009.1 |
| GET | `/static/<file>` | 静态资源，路径 jail 到 `static/` | 009.1 |
| GET | `/healthz` | `{status, version}` | 009.1 |
| GET | `/api/commands` | 按键清单 + 参数定义 | 009.1 |
| POST | `/api/jobs` | 提交任务 → `202 {job_id}`（不阻塞） | 009.1 |
| GET | `/api/jobs/{id}` | 状态 / 退出码 / 日志尾部 / 起止时间 | 009.1 |
| POST | `/api/jobs/{id}/cancel` | 取消（SIGTERM → 超时 SIGKILL） | 009.1 |
| GET | `/api/companies` | 公司目录列表（含最近 run、`primary_period`） | 009.2 |
| GET | `/api/companies/{dir}/artifacts` | 产物索引（按类型分组） | 009.2 |
| GET | `/api/companies/{dir}/report?id=<artifact_id>` | 渲染报告为 HTML | 009.2 |
| GET | `/api/companies/{dir}/charts` | 图表序列（`{labels, series:[{name, values}]}`） | 009.2 |
| GET | `/api/companies/{dir}/runs` | 迭代台账时间线 | 009.2 |

**报告接口不收路径**：`id` 必须是服务端自己算出来的产物索引里的键（相对路径的 sha1 前 12 位），
客户端无法指定任意文件——这是 AC-6「不提供任意路径输入」的落地方式。

## 6. 命令白名单

`commands.py` 用一张声明式表描述每个按键，`job` 启动时按表拼 `argv`。
表里的参数定义必须与脚本真实 CLI 一致，由一条测试**双向**核验（表里的 flag 能在脚本源码里找到
`add_argument("--flag"`；脚本里 `required=True` 的 flag 必须在表里）——这样表与脚本不会各改各的。

| 按键 | 实际命令 | 关键参数 |
|------|----------|----------|
| 取数（行情与财报） | `scripts/tushare_collector.py` | `--code`（必填）、`--output`、`--dry-run` |
| 发现最新一期报告 | `scripts/discover_report.py` | `--stock-code`（必填）、`--report-type` |
| 下载定期报告 | `scripts/download_report.py` | `--stock-code`（必填）、`--report-type`、`--since`、`--save-dir`、`--force` |
| 解析年报 PDF | `scripts/pdf_preprocessor.py` | `--pdf`（必填）、`--output` |
| 价值分析（预计算） | `scripts/value_analysis_engine.py` | `--code`（必填）、`--output-dir`（必填） |
| 买卖计划（采集行情） | `scripts/buy_sell_plan.py` | `--code`（必填）、`--output-dir`（必填）、`--as-of` |
| 买卖计划（离线重放） | `scripts/buy_sell_engine.py` | `--output-dir`（必填）、`--as-of` |
| 通用估值 | `scripts/valuation_engine.py` | `--code`（必填）、`--output-dir`（必填） |
| 组合配置 | `scripts/portfolio_engine.py` | `--mode`（必填，固定枚举）、`--profile`、`--weights`、`--output`、`--json` |
| 选股器 | `scripts/screener_core.py` | `--tier1-only`、`--tier2-limit`、`--min-roe`、`--max-pe`、`--min-gross-margin`、`--csv`、`--html`、`--output`、`--cache-refresh` |
| 报告转 HTML | `scripts/report_to_html.py` | `--input`（必填）、`--output`（必填）、`--standalone` |
| 报告转移动版 HTML | `scripts/md_to_mobile_html.py` | `--input`（必填）、`--output`（必填） |
| 更新判定 | `scripts/analysis_status.py` | `--company-dir`、`--ticker`、`--root`、`--all`、`--json`、`--check-upstream` |
| 迭代台账：建 run | `scripts/runs.py new` | `--company-dir`、`--ticker`、`--company`（必填）、`--kind`、`--primary-period`、`--supersedes`、`--input` |
| 迭代台账：解析 run 目录 | `scripts/runs.py resolve` | `--company-dir`（必填）、`--latest`、`--run-id` |
| 迭代台账：收尾登记 | `scripts/runs.py finish` | `--company-dir`、`--run-dir`（必填）、`--status`、`--primary-period`、`--conclusion-changed`、`--report-periods`、`--force` |
| 迭代台账：接管扁平目录 | `scripts/runs.py adopt` | `--company-dir`（必填）、`--run-id`、`--prune` |
| 迭代台账：导出摘要 | `scripts/runs.py export` | `--company-dir`（必填）、`--format` |
| 迭代台账：清除 stale | `scripts/runs.py downstream` | `--company-dir`（必填）、`--fresh` |
| 定性管线：准备 | `python -m scripts.results.prepare` | `--output-dir`、`--ticker`、`--company`（必填）、`--market`、`--run-id`、`--max-chars` |
| 定性管线：对账 | `python -m scripts.results.reconcile_results` | `--input`（可重复，必填）、`--output`（必填） |
| 定性管线：汇总 | `python -m scripts.results.synthesis` | `--input`（可重复）、`--optional-input`、`--reconciliation`、`--evidence-index`（必填）、`--max-chars`、`--output`（必填） |
| 定性管线：解析结果 | `python -m scripts.results.resolve_qualitative` | `--output-dir`、`--ticker`（必填）、`--output` |

安全规则（AC-1.4）：白名单外的命令、表里没声明的参数、取值里出现 `;` `|` `&&` `$(` 或换行一律 4xx 拒绝，
**不启动子进程**；并发任务上限 3，超出返回 429。

## 7. 图表数据来源

全部来自本地 `output/{公司目录}/data_pack_market.md`（run-store 布局下取当前 run 的
`inputs/data_pack_market.md`，经 `runs.py resolve` 的同一套解析规则定位），**不联网**：

| 图 | 来源小节 | 解析规则 |
|----|----------|----------|
| 年度股价走势（折线：最高/最低/年末收盘） | `## 11. 十年周线行情` → `### 年度行情汇总` | 表格行 `| 年度 | 最高 | 最低 | 年末收盘 | 周均成交量 |`，按年度升序 |
| 关键财务指标多期趋势（折线） | `## 12. 关键财务指标` | 表头为期次；取 `ROE (%)`、`毛利率 (%)`、`净利率 (%)`、`资产负债率 (%)` 四行 |
| 营收与归母净利润（柱状） | `## 3. 合并利润表` | 取 `营业收入` 与 `归母净利润` 两行，按期次排列 |

Markdown 表格解析用一个**空格容忍**的通用小函数（按 `|` 切分、去空白、跳过 `---` 分隔行），
不引入 Markdown 解析库。文件缺失、小节缺失、表格列数不符时返回带原因的错误响应（AC-2.4），
并在响应里带上「期望的小节名」，方便数据包格式变化时定位。

## 8. 迭代台账读取

数据来源（REQ-003 的契约，只读）：

| 文件 | 用途 |
|------|------|
| `history.jsonl` | 每次 run 一行：`run_id` / `kind` / `created_at` / `supersedes` / `subject` / `report_periods` / `primary_period` / `framework` / `status` / `conclusions_changed` |
| `latest.json` | 当前生效 run 指针（`run_id`、`primary_period`、`published_at`、`artifacts`） |
| `record.json` | 给人看的分析记录卡（含 `downstream.stale`） |
| `runs/{run_id}/run.json` | 单次 run 的元数据（`kind`、`subject`、`supersedes`、`framework`） |

视图规则：按 `created_at` **倒序**渲染时间线；`latest.json` 的 `run_id` 标为「当前生效」；
`kind == "report-update"` 的 run 展开 `conclusions_changed` 与它 `supersedes` 的那次 run；
`record.json` 的 `downstream.stale` 以醒目状态显示（下游估值/买卖计划已过期）。
`history.jsonl` 缺失或某行 JSON 损坏：跳过坏行并在响应里带 `warnings`，**不返回 500**（AC-2.5）。

## 9. 安全设计

| 风险 | 措施 |
|------|------|
| 远程访问 | 默认且仅允许绑定 `127.0.0.1`；显式传非环回地址直接拒绝并说明理由 |
| 命令注入 | 白名单 + 结构化参数 + `shell=False`；参数值拒绝 shell 元字符 |
| 路径穿越 | 所有文件访问经 `_safe_join(OUTPUT_ROOT, rel)`：`resolve()` 后必须仍在 `output/` 子树内，符号链接同样按真实路径判定；报告接口只认服务端产物索引的 `id` |
| token 泄露 | 不回显环境变量；日志环形缓冲写入前用 `TUSHARE_TOKEN` 的实际值做脱敏替换；页面只显示命令名与参数，不显示 env |
| 资源耗尽 | 并发任务上限 3；日志只保留尾部 200 行并截断单行长度；命令超时可配（默认不限，由用户点取消） |
| 误操作 | 视图区只读；写操作（下载/覆盖/`--force`/`adopt --prune`）在按键上标注影响范围，需二次确认 |

## 10. 测试计划（含预算）

| 文件 | 覆盖 | 用例预算 | 手法 |
|------|------|----------|------|
| `tests/test_webui_server.py` | REQ-009.1 AC-1.1…AC-1.4 | ≤ 22 | 起真实 `ThreadingHTTPServer` 绑 `127.0.0.1:0`（随机端口，不占固定端口），用 `urllib.request` 打接口；子进程一律用 `sys.executable -c` 的假命令，不跑真实脚本、不联网 |
| `tests/test_webui_views.py` | REQ-009.2 AC-2.1…AC-2.5 | ≤ 18 | 用 `tmp_path` 造一个假 `output/`（公司目录 + `data_pack_market.md` + `history.jsonl` + `latest.json`），断言解析结果与错误路径；XSS 用例断言 `<script>` 被转义 |

预算核算：当前 1522/1600，余量 78；上述合计 ≤ 40 条，落在余量内，**不需要上调上限**（对应 AC-7）。
若实现中发现确实不够，按 `docs/TESTING.md` §5 写明理由、经 owner 批准后上调并同步 `scripts/test_scope.py`
与 `docs/TEST_SCOPE.md`，不得为让门禁变绿而放宽判定。

测试必须遵守的既有纪律：不联网、不写仓库 `output/`、不 `time.sleep`、不依赖当前时间与随机顺序。

## 11. 交付阶段

| 阶段 | 内容 | 需求编号 | 验收 |
|------|------|----------|------|
| 一 | 服务骨架、按键执行器、日志与取消 | REQ-009.1 | 独立验收报告（AC-1.1…AC-1.4） |
| 二 | 报告浏览、3 类图表、迭代台账时间线 | REQ-009.2 | 独立验收报告（AC-2.1…AC-2.5） |
| 收口 | 文档（README/CHANGELOG）、`make gui`、实跑 | REQ-009 | 逐条核对 AC-1…AC-8，含「实跑记录」 |

父需求 `REQ-009` 只有在 `REQ-009.1`、`REQ-009.2` 都 `verified` 之后才能推进（门禁强制）。

## 12. 开放问题

- 是否需要把图表导出为图片（本期不做；若要做，用 Canvas `toDataURL` 即可，不引新依赖）。
- 是否把面板扩成「门禁/CI 状态看板」（属 REQ-006 领域，若做另开子需求，不塞进 REQ-009）。
- 端口冲突时是否自动换端口：暂定**不自动换**（AC-1.1 要求显式报错），避免用户以为服务跑在了别的端口。
