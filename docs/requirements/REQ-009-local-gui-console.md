---
id: REQ-009
title: 本地图形化控制台（按键执行 + 股票图表 + 报告与迭代记录浏览）
status: accepted
priority: P1
owner: CHU-2002
created: 2026-09-21
updated: 2026-09-21
issue: TBD
design: docs/GUI_CONSOLE_PLAN.md
milestone: TBD
pr: TBD
depends-on: REQ-003, REQ-004
supersedes: TBD
---

# REQ-009 本地图形化控制台（按键执行 + 股票图表 + 报告与迭代记录浏览）

## 背景与问题

框架现在**只有命令行**：`scripts/` 下 20 多个脚本、14 个用户可见入口、run-store 的 6 个子命令，
全部要靠记参数手敲。实际使用中的痛点（来自当前仓库状态，不是主观判断）：

- **入口分散**：README 的「怎么用」列了 slash command、「直接跑 Python 脚本」又列了 11 条命令，
  另有 `scripts/results/` 的 4 步管线；没有一处能一眼看全「这个项目能干什么」。
- **看图要自己动手**：`data_pack_market.md` 里已经算好了 §11 十年周线行情（11 年年度高低/年末收盘/周均量）、
  §12 关键财务指标（9 个期次的 ROE/毛利率/净利率/资产负债率…）、§3~§5 三大报表，
  但都是 Markdown 表格，**没有图形**，趋势要靠逐格读数字。
- **产出散落**：`output/600887_伊利/` 下有 31 个文件（报告、数据包、PDF、synthesis、contexts）。
  报告是 Markdown，要另外转 HTML 才能好好读；`scripts/report_to_html.py` 目前覆盖率 0%、也没人点得到。
- **迭代记录看不懂**：REQ-003 的 run-store 已经落盘 `history.jsonl`（追加式迭代台账）、
  `latest.json`（当前生效指针）、`runs/{run_id}/run.json`、`record.json`，但**只能 `cat`**；
  「这次增量更新相对上一次改了什么结论」（`conclusions_changed`）散在 JSON 里，看不出时间线。

也就是说：**数据、图表、报告、迭代记录都已经落盘了，缺的只是一个能点、能看的面板。**

## 目标

- 让「这个项目能做什么」变成一屏可见、可点的按键，不必记参数。
- 让已落盘的行情与财务数据变成图，趋势一眼看得出。
- 让产出报告和 run-store 迭代台账能在同一个面板里读完，不用来回 `cat` 与转 HTML。

## 验收标准

- **AC-1**：一条命令启动本地面板：`make gui`（等价 `.venv/bin/python -m scripts.webui`）启动后打印可点击的本地 URL；
  服务**只监听环回地址**（默认 `127.0.0.1:8765`，端口可配），显式要求绑定非环回地址时被拒绝并给出可读理由；
  默认端口被占用时以非 0 退出码报错，不静默改端口。
- **AC-2**：所有既有用户可见入口以按键形式提供，且**复用既有实现**（面板不另写一套业务逻辑）。
  被覆盖的入口清单（以 README「怎么用」与「直接跑 Python 脚本」为准）：
  `tushare_collector.py`、`discover_report.py`、`download_report.py`、`pdf_preprocessor.py`、
  `value_analysis_engine.py`、`buy_sell_plan.py`、`buy_sell_engine.py`、`valuation_engine.py`、
  `portfolio_engine.py`、`screener_core.py`、`report_to_html.py`、`md_to_mobile_html.py`、
  `analysis_status.py`、`runs.py` 的 `new`/`resolve`/`finish`/`adopt`/`export`/`downstream`、
  `scripts.results` 管线的 `prepare`/`reconcile_results`/`synthesis`/`resolve_qualitative`。
  点击按键后显示：实际执行的命令行、运行中状态、退出码与 stdout/stderr 尾部。
- **AC-3**：提供必要的股票图表——基于**本地已落盘产物**至少绘制 3 类图：
  (a) 年度股价走势（最高/最低/年末收盘，来自 `data_pack_market.md` §11「十年周线行情」）；
  (b) 关键财务指标多期趋势（ROE/毛利率/净利率/资产负债率，来自 §12）；
  (c) 营收与归母净利润按期次对比（来自 §3 合并利润表）。
  图表数据全部来自本地文件，**不联网**；鼠标悬停能读到具体数值。
- **AC-4**：报告与产出可读——能按公司列出报告与产物（定性分析报告、变化报告、`value_computed.md`、
  买卖计划、数据包、PDF 等），点击后在面板内渲染阅读（标题/列表/表格正常显示），
  并能在公司、期次与 run 之间切换。
- **AC-5**：迭代记录可读——面板展示 run-store 迭代台账：按时间**倒序**列出 `history.jsonl` 的每次 run
  （run_id、kind、创建时间、supersedes、期次、框架指纹、结论变化），标出 `latest.json` 指向的当前生效 run，
  并对变化型 run（`report-update`）展示它相对被取代 run 的结论变化（`conclusions_changed`）；
  `history.jsonl` 缺失的公司显示空时间线而不是错误页。
- **AC-6**：安全边界——面板**不提供任意命令或任意路径输入**：命令走白名单 + 结构化参数表单且**不经过 shell**；
  文件访问限制在 `output/` 之下，拒绝 `..`、绝对路径与越界符号链接；页面上不回显 `.env` 内容或 token。
- **AC-7**：无新增第三方依赖且全 mock 可测——实现只用 Python 标准库与仓库既有依赖
  （不改 `requirements.txt` / `requirements-test.txt`）；测试不联网、不依赖真实 `output/`、不 import 新包；
  新增测试规模在 `docs/TEST_SCOPE.md` 的 scope 预算内
  （2 个测试文件：`tests/test_webui_server.py`、`tests/test_webui_views.py`，合计 ≤ 40 条用例），
  确需上调时按 `docs/TESTING.md` §5 写明理由并经需求 owner 批准，不得为让门禁变绿而放宽判定。
- **AC-8**（实跑）：在真实机器上实跑——`make gui` 启动后用浏览器打开面板，对 `output/600887_伊利`
  依次点开报告、3 类图表与 2 个 run 的迭代时间线，并点一个按键跑一条真实命令（含退出码与日志回显）；
  报告里记录命令、环境（Python/浏览器/端口）与观察到的界面现象。

## 子需求

### REQ-009.1 控制台骨架与按键执行器

- 状态：`accepted`
- 目标：一条命令起面板，既有入口全部变成可点的按键，点下去就能跑并看到结果。
- 验收标准：
  - **AC-1.1**：`python -m scripts.webui --port 0` 启动后 `GET /healthz` 返回 200 且带框架版本；
    服务只绑定 `127.0.0.1`（请求绑定非环回地址被拒绝并给出可读理由）；默认端口被占用时非 0 退出。
  - **AC-1.2**：`GET /api/commands` 返回按键清单，覆盖 AC-2 列出的全部入口（测试逐项断言），
    每条含结构化参数定义（名称、是否必填、默认值、取值约束），参数定义取自各脚本真实 CLI。
  - **AC-1.3**：`POST /api/jobs` 提交任务后**立即**返回任务 id（HTTP 不阻塞到命令结束）；
    `GET /api/jobs/{id}` 返回 running/finished/failed、退出码、stdout/stderr 尾部与起止时间；
    未知命令或非法参数返回 4xx 且**不启动任何子进程**。
  - **AC-1.4**：白名单外的命令、未声明的参数、含 `;` `|` `&&` `$(` 等 shell 元字符的取值一律被拒绝
    （命令不以 `shell=True` 执行）；并发任务数有上限，超限返回 429 而不是无限起进程。
- 追溯：`tests/test_webui_server.py`；PR #TBD

### REQ-009.2 报告浏览、图表与迭代台账视图

- 状态：`accepted`
- 目标：不跑命令也能看懂已有产出——读报告、看图表、看迭代记录。
- 验收标准：
  - **AC-2.1**：`GET /api/companies` 返回 `output/` 下全部公司目录及其最近一次 run 与 `primary_period`；
    `output/` 不存在时返回空列表而不是 500。
  - **AC-2.2**：`GET /api/companies/{dir}/artifacts` 按类型分组返回产物（相对路径、大小、修改时间）；
    `..`、绝对路径、指向 `output/` 之外的符号链接一律 4xx 拒绝。
  - **AC-2.3**：Markdown 报告在面板内渲染为 HTML，且报告里的原始 HTML/脚本被**转义**（`<script>` 不生效），
    用一条注入用例断言。
  - **AC-2.4**：`GET /api/companies/{dir}/charts` 从本地 `data_pack_market.md` 解析出至少 3 组序列
    （年度行情、关键财务指标、营收/归母净利），返回前端可直接绘制的 `{labels, series:[{name, values}]}`；
    文件缺失或表格格式变化时返回带原因的错误响应而不是未捕获异常。
  - **AC-2.5**：`GET /api/companies/{dir}/runs` 按时间倒序返回 `history.jsonl` 的 run 列表并标出
    `latest.json` 指向的当前 run；变化型 run 给出 `supersedes` 与 `conclusions_changed`；
    `history.jsonl` 缺失时返回空时间线。
- 追溯：`tests/test_webui_views.py`；PR #TBD

## 范围

**包含**

- 本地单页面板（`scripts/webui/`）+ 启动入口（`make gui` / `python -m scripts.webui`）
- 按键执行器：命令白名单、结构化参数表单、异步任务状态与日志尾部
- 3 类股票图表（年度行情 / 关键财务指标 / 营收与归母净利），数据来自本地 `data_pack_market.md`
- 报告与产物浏览（Markdown 渲染、公司与期次/run 切换）
- run-store 迭代台账时间线视图（history / latest / supersedes / conclusions_changed）
- 无新增第三方依赖的自动化测试与 README / 设计文档更新

**不包含**

- 远程访问、多人使用、鉴权与用户体系（只绑定环回地址，属安全边界而非待办）
- 在面板里修改产物或编辑报告（本期只读优先；写操作仍走既有脚本与流程）
- 新增数据源、新指标算法，或改动 `data_pack_market.md` / run-store / 报告的任何既有格式契约
- 实时行情推送、WebSocket、K 线缩放与专业图表皮肤
- 移动端适配、主题定制、前端构建链（Node/npm）
- 交易下单或任何券商接口

## 约束与依赖

- **零新增依赖**：只用 Python 标准库与仓库既有依赖；CI 只装 `requirements-test.txt`，
  新增第三方包会同时拖慢 CI 并可能顶破 scope 预算（实测 2026-09-21：测试文件 33/40、用例 1522/1600）。
- **面板是入口不是副本**：按键必须调用既有脚本/模块；业务逻辑不得在面板里重写。
  命令清单若与 README 漂移，以 README 为准（AC-2 逐项断言）。
- **全 mock、不联网**：测试不依赖真实 `output/`，产物一律写 `tmp_path`；新增测试文件归 `unit` 层
  （`tests/conftest.py` 的 `TEST_LAYERS` 无需登记）。
- **不改既有契约**：run-store 的 `run.json` / `history.jsonl` / `latest.json` / `record.json` 与
  报告命名（`qualitative_report.md`、`change_report_{period}.md`）由 REQ-003/REQ-004 固定，本需求只读。
- **依赖**：REQ-003（迭代台账）、REQ-004（变化报告）已 `verified`，其产物是本需求的数据来源。

## 手工自测清单

| 编号 | 对应 AC | 手工验证步骤 | 预期观察结果 |
|------|---------|--------------|--------------|
| MT-1 | AC-1 | `make gui`，浏览器打开终端打印的 URL，改一次 `--port` 再启动 | 页面正常出现；终端有访问日志；`--port` 生效；非环回绑定被拒绝 |
| MT-2 | AC-2 | 在面板上点 3 个不同按键（如「发现最新一期报告」「更新判定」「runs export」） | 回显的命令行与 README 里的写法一致；能看到运行状态、退出码与日志尾部 |
| MT-3 | AC-3 | 打开 600887 的图表页，**断网**后刷新 | 至少 3 类图渲染出来，鼠标悬停显示数值，断网不影响 |
| MT-4 | AC-4/AC-5 | 打开报告页与迭代记录页，切一次期次/run | 报告标题/表格渲染正常；时间线显示 2 个 run（baseline → report-update），当前 run 有标记，变化型 run 显示结论变化 |
| MT-5 | AC-6 | 手工请求 `/api/companies/..%2f..%2f.env/artifacts`，并在页面里找 token | 返回 4xx；页面上看不到任何 token 或 `.env` 内容 |
| MT-6 | AC-8 | 按 AC-8 步骤完整实跑一遍 | 见「实跑记录」小节 |

## 实跑记录

**待实跑**（AC-8）。收口前在此登记：日期 / 环境（Python 版本、浏览器、端口）/ 命令 / 观察到的界面现象 /
本次发现的问题与去处（子需求编号或 REQ-006 任务）。

## 追溯

| 项 | 内容 |
|----|------|
| 设计文档 | `docs/GUI_CONSOLE_PLAN.md` |
| 实现 PR | TBD |
| 测试 | `tests/test_webui_server.py`、`tests/test_webui_views.py` |
| 文档更新 | `README.md`（面板一节）、`Makefile`（`make gui`）、`CHANGELOG.md` |

## 备注

- **受理记录**：2026-09-21 由使用者确认受理（图形界面引入用户可见的新能力，按 `README.md` §6 占新编号）；
  同日接受拆分 `REQ-009.1` / `REQ-009.2` 两个可独立验收的切片。
- **技术栈决策**：使用者在三选一中选定「只用 Python 标准库 `http.server` + 手写单页前端，零新增依赖」，
  理由是 CI 只装 `requirements-test.txt` 且 scope 预算只剩 78 条用例，引入 Flask/前端框架或
  Streamlit 都会同时顶破依赖与测试门禁。设计与取舍见 `docs/GUI_CONSOLE_PLAN.md`。
- **用例预算**：开工实测 1522/1600（余量 78）。使用者已表示「若确实不够，可按流程申请上调」；
  实现时优先把新增用例压在预算内，需要上调时在 `scripts/test_scope.py` 与 `docs/TEST_SCOPE.md`
  写明理由并经 owner 批准（对应 AC-7）。
- **Issue 未开**：本地 `gh` 未登录（keyring 登录超时），Issue 编号 TBD；按 DoR 应在编码前补开并回填台账。
- **开放问题**：图表是否需要保存为图片导出（本期不做）；是否把面板做成 `git` 状态/门禁状态的展示入口
  （属 REQ-006 领域，若做另开子需求而不是塞进本需求）。
