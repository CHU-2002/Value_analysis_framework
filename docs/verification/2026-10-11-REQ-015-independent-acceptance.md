---
batch: 2026-10-11-REQ-015-independent-acceptance
date: 2026-10-11
reviewer: independent-agent
independence: independent
requirements: REQ-015
base: 55fdc4ac885b646b91a4b882af8d139f17d42b98
full-suite: "1957 passed, 3 skipped，覆盖率 76.28%"
---

# 独立验收报告：REQ-015 公司研究工作区

## 独立验收声明

我没有参与 REQ-015 的实现，也没有修改产品代码或需求状态。本次验收对象是 `affbd6361c7f82186b6aa2ba3928bf8cc8da6e45`。对比基线使用本地缓存的 `origin/main` `55fdc4ac885b646b91a4b882af8d139f17d42b98`；本轮没有刷新远端引用。

## 全量测试

```bash
COVERAGE_FILE=/tmp/req015-independent-acceptance-escalated.coverage make verify
```

- 结果：**1957 passed, 3 skipped**；3 个跳过项是未配置 `TUSHARE_TOKEN` 的真实 API 测试。
- 覆盖率：**76.28%**（门槛 74%）。追溯 **18 passed**；scope **48 个文件 / 1960 个用例**，在预算内；批量回归门禁通过。
- 默认沙箱第一次运行时，13 个只绑定本地 HTTP 测试服务器的用例被环境以 `EPERM` 拒绝；同一套测试经允许本地 loopback 绑定的运行通过。成功结果以上述专用 `/tmp` coverage 文件命令为准。
- 基线：`55fdc4ac885b646b91a4b882af8d139f17d42b98`（缓存的 `origin/main`，未重新 fetch）。

## 逐条验收

### REQ-015 公司研究工作区

- [x] **AC-1**：真实浏览器检查了伊利与第二家公司上下文、A→B 切换、刷新、前进/后退、B 的图表/报告/数据绑定，以及无报告、无数据的下一步入口；公司概览、图表和报告均能打开既有买卖计划确认流程。买卖计划依赖服务端凭据与冻结估值检查、明确确认，不自动提交交易订单。
- [x] **AC-2**：真实图表含 OHLC K 线、成交量、PE(TTM)/PB/PS(TTM) 与历史百分位、三项五年 P25/P75 参考线、周期/范围同步、光标读数及缩放/平移/重置；数值表和导出核对当前公司与口径。合成边界样本测试了严格历史排名、未来样本不回写既有点、停牌/缺数和周期聚合。
- [x] **AC-3**：生成前可选择 agent/模型并预检，提交任务保存所选参数和本次 CLI 回报的实际模型。T24 的新鲜度门禁在预检及服务端提交处共同阻止过期价值分析；T25 保留 `workspace-write` 沙箱并为明确确认的 Codex 分析打开网络配置；T26 的提示差异与 argv 断言覆盖 `WEBUI_ARCHIVE_ROOT` 兜底及禁止 HOME 回退（这次成功实跑同时设置了两个 archive 变量，未单独验证 WebUI-only 兜底）。
- [x] **AC-4**：正文前可选择类型和版本，价值分析是默认类型且排在首位；真实浏览器通过目录跳转、查找及无匹配、版本比较、刷新后保持版本、下载所选版本、源材料分层和非 Markdown 打开等检查。
- [x] **AC-5**：研究采集计划显示公司/期次/档位/目标分母、预计请求与复用目标；完成页单独统计本批请求、缓存复用、新增成功、无数据、失败及未完成，并给出数据时点、影响和离线后续入口。真实采集批次完成 **8/8**，其中实际请求 **1**、缓存复用 **7**、新增成功 **1**、合法空响应/失败/未完成均为 **0**。
- [x] **AC-6**：真实浏览器脚本分别在 **1440×900**、**1024×768** 运行，每个视口 66 项、合计 **132 项**，`failures=[]`。仅键盘路径、焦点、读数、公司切换、无数据/报告状态及无整页横向溢出均通过。已目视检查 [1024-valuation-bands.png](../../output/.live_req015_acceptance/1024-valuation-bands.png)：PE(TTM) 与 PB 的倍数和 P25/P75 图例清晰可读；同脚本也通过 PS(TTM) 对应读数和阈值检查。
- [x] **AC-7**：真实浏览器走查使用两家公司及有/无数据、有/无报告、历史版本和保留的部分成功产物；真实采集完成一次；真实 Codex agent 报告生成成功一次。成功运行满足 wrapper 退出码 0、实际模型审计匹配、所需报告存在且 `missing_reports=[]`。环境、命令、运行产物与早先未产出结果的处理见「实跑记录」。截图与 observations 保存在隔离验收目录。
- [x] **AC-8**：主导航与公司页签层级明确，链接可点击且选中状态与键盘切换、前进/后退一致；真实走查未发现孤立分组标题或既有页面能力丢失。

#### REQ-015.1 标准 K 线与估值联动时间轴

- [x] **AC-1.1**：真实 K 线、量柱、键盘联动读数通过；MA5/10/20/60 在当前蜡烛数量上计算且短历史不补值，由 `test_research_ma_uses_current_candle_count_and_never_fills_short_history` 核对。
- [x] **AC-1.2**：真实浏览器逐一选择日/周/月/季/年，验证实际范围、放大、平移与重置；`test_research_periods_are_natural_ohlc_with_no_holiday_zeros` 用 2015–2026 跨年/跨季度样本逐档核对自然周期 OHLC 和量；`test_research_calendar_separates_suspension_and_missing_and_empty_range` 区分休市、停牌、缺行情与空范围。实跑历史表超过 1000 个日点。
- [x] **AC-1.3**：PE(TTM)、PB、PS(TTM) 与其百分位、五年滚动阈值和同日期读数分别通过真实 UI 检查；期末取值、TTM 来源、PB 定义、缺值/阈值样本规则由研究时间轴测试核对。PB 按数据源的最新已披露净资产口径，符合 owner 已确认的需求定义。
- [x] **AC-1.4**：等值、非正值、缺值、严格小于排名、未来数据不影响此前排名、窗口边界与阈值样本由 `test_research_daily_ranking_ties_invalid_values_and_future_invariance` 和 `test_research_ttm_price_sales_and_rolling_five_year_valuation_bands` 核对；更换周期和复权不改变日样本排名。
- [x] **AC-1.5**：不复权/前复权/后复权的价格数据、成交量保持不变、指标仍使用来源口径及缺少复权依据时的拒绝由 `test_research_adjustment_and_range_do_not_change_daily_valuation_ranks` 与 `test_research_missing_ohlc_factors_and_invalid_controls_have_explicit_feedback` 核对。
- [x] **AC-1.6**：年度/半年/单季财务视图和导出元数据通过两个视口的真实浏览器检查；`test_research_income_converts_cumulative_quarters_and_keeps_decade_history` 使用十年以上年度行并验证累计季度转单季、半年与缺失期语义，`test_financial_pack_cumulative_quarters_are_not_mislabeled_as_single_quarters` 验证财务包的累计期次不被误标为单季。
- [x] **AC-1.7**：两视口实跑导出 CSV、TSV 与图片，并核对公司、周期、范围、口径、指标、数据时点和来源元信息；缺少 OHLC、复权因子或无数据范围均显示原因与数据页更新入口。导出离线且保留当前选择。

#### REQ-015.2 可选择 agent 与模型的报告生成

- [x] **AC-2.1**：生成配置跟随所选公司，动作限定为三个既有选项，agent 与模型目录可选；模型切换、安装状态和配置来源的服务端契约由 `tests/test_agent_action.py` 覆盖。
- [x] **AC-2.2**：只读预检不提交任务；默认模型未知和 agent/model 不兼容均明确处理，服务端拒绝非法组合。T24 在 `new_report`、`framework_changed`、`schema_changed`、`inputs_changed`、`run_failed` 时阻止价值分析，仅 `downstream_stale` 可继续；预检和绕过预检后的直接提交均有测试。
- [x] **AC-2.3**：确认后模型只传给本次 CLI，实际模型与请求模型均落审计；Codex 与 Claude 假 CLI 合同测试验证各自收到所选模型。真实报告运行的 Codex 审计为 `gpt-6-luna`，无静默换模或自动重跑。Codex `workspace-write` 的网络配置通过 T25 结构化 argv 与成功联网实跑核对。
- [x] **AC-2.4**：任务中心保留任务进展、离开后可恢复并可直达本次产物；新输出按本次执行起止时间绑定，旧文件不冒充新结果；退出码 0 但缺少声明报告时按未产出失败关闭。真实走查也验证了已恢复的部分成功任务及其产物。

#### REQ-015.3 报告阅读与版本比较

- [x] **AC-3.1**：报告类型/版本在正文前选择，价值分析默认且置首；发布指针和迭代元数据优先于文件名/mtime，历史、过期和未知状态有说明。浏览器检查当前报告默认、无正式指针时可选历史版本。
- [x] **AC-3.2**：真实报告标题目录与锚点、文内搜索计数和上下项、无匹配状态、专注模式、重复标题焦点定位、表格局部滚动和默认折叠技术 JSON 均通过。
- [x] **AC-3.3**：真实历史版本正文比较显示相同公司、类型和数据版本，并不新增投资结论；没有可比版本时显示正常状态。
- [x] **AC-3.4**：选中版本下载与刷新保持、源材料分层、PDF/JSON 等非 Markdown 的打开/下载和安全路径边界均由浏览器检查及 `test_reader_material_and_download_bind_selected_version_and_keep_jail` 核对。

#### REQ-015.4 采集过程与结果解释

- [x] **AC-4.1**：预检明示公司范围、期次、档位、中文业务组、请求估算和缓存复用；确认前不发出远端请求，服务端使用预检计划并拒绝被更改的计划。
- [x] **AC-4.2**：任务页记录排队、采集与完成进度、公司/业务组、已完成目标、实际请求与复用数；历史在刷新/重启后可见，暂停/部分成功分别给出恢复入口。真实采集运行可见完整进度并在终态保留记录。
- [x] **AC-4.3**：真实 8/8 批次显示新增 1、复用 7、无数据 0、失败 0、未完成 0，并逐目标展示数据时点、分析影响和离线后续；完成采集没有声称报告已自动更新。
- [x] **AC-4.4**：界面明确显示同一计划的 7/8 目标分母、剩余 1、估算新增请求 1、已完成目标 7 和独立存档记录数；合法空响应、无权限、限频、未尝试、过期等分别解释，未尝试目标计入分母。计划分母和真实结果在截图中互相可核对。

#### REQ-015.5 公司研究导航与页面布局

- [x] **AC-5.1**：工作台/公司/任务一级入口和概览/行情与估值/报告/数据公司页签均真实点击、键盘到达并切换内容；分组紧邻对应入口。
- [x] **AC-5.2**：公司名、代码和切换入口贯穿页签；切换公司、刷新、前进/后退恢复对应页面和报告版本，空选择给出搜索/选择引导。
- [x] **AC-5.3**：概览到图表/报告/数据及报告到生成配置均为近处操作；确认前配置和预检仍在，任务结果按提交时公司直达本次版本。
- [x] **AC-5.4**：数据页批次/原始存档、报告页迭代记录、高级工具既有命令入口均可到达；两个视口侧栏与页签无需整页横向滚动，长公司名可辨识，键盘折叠/展开正常。

## 实跑记录

**日期与环境。** 2026-10-11；本机真实 Edge 浏览器，沙箱控制台 `http://127.0.0.1:8767`；所有输出写入 `output/.live_req015_acceptance/` 或 `output/.live_req015_t24_workspace/`，未写入 `~/turtle_archive` 或真实 HOME 既有产物。真实采集使用已配置的 Tushare 凭据（本报告、日志和产物不含凭据）；报告生成复用 Codex 本机登录态。实跑账户只验证 Codex；Claude 使用假 CLI 合同验证，不冒充真实实跑。

**浏览器走查命令。**

```bash
.venv/bin/python scripts/gui_research_walkthrough.py --base http://127.0.0.1:8767 --out output/.live_req015_acceptance
```

退出码 **0**；1440×900、1024×768 各 **66** 项，合计 **132** 项，真实输出 `failures=[]`。截图包括两种视口下的导航、图表操作、估值参考线、报告阅读、买卖计划入口和数据页；逐项结果见 `output/.live_req015_acceptance/observations.json`。另见 `output/.live_req015_acceptance/real-collection-observations.json` 及 `real-collection-result.png`。

**真实采集。** 在同一沙箱控制台确认公司 `600887.SH` 的研究采集计划后执行，归档到隔离 archive。终态显示 **8/8 成功**，实际请求 **1**、缓存复用 **7**、新增成功 **1**、失败/未完成/合法空响应 **0**；请求来源和结果批次只写入 `.live_req015_acceptance` 沙箱。

**唯一计作成功的真实 agent 生成。** UI 预检配置为 `600941.SH`、`agent_business_analysis`、Codex、`gpt-6-luna` 后经显式确认提交。可复制的受控 wrapper 命令为：

```bash
.venv/bin/python scripts/agent_action.py --action business-analysis --output-root "$PWD/output/.live_req015_t24_workspace/output" --require-report --ticker 600941.SH --backend codex --model gpt-6-luna
```

运行环境中的 `WEBUI_OUTPUT_ROOT` 指向上述隔离 output；`WEBUI_ARCHIVE_ROOT` 与 `TURTLE_ARCHIVE_ROOT` 均指向 `$PWD/output/.live_req015_t24_workspace/archive`，`WEBUI_CACHE_DIR` 指向该沙箱 cache。Codex 通过 CLI 使用 `--sandbox workspace-write`、`--config sandbox_workspace_write.network_access=true` 和本次 `--model gpt-6-luna`；没有修改 CLI 全局配置。持久 job `job-8d9dcec760e9` 记录 `finished` / exit **0**，时间为 `2026-10-11T08:04:04Z` 至 `08:28:54Z`；独立审计的 `requested_agent=codex`、`requested_model=gpt-6-luna`、`actual_model=gpt-6-luna`、`exit_code=0`、`missing_reports=[]`。本次新产物位于 `output/.live_req015_t24_workspace/output/600941_中国移动/`：`data_pack_report.md`、`qualitative_report.md`、`synthesis/result.json`、`run_manifest.json` 及 5 份模块报告均存在、非空，且被 job 审计列为本次产物。此次实跑验证 T25 网络配置下的成功报告生成和隔离存档使用；T26 的 WebUI-only 兜底仅由代码差异与 argv 回归断言复核，未由这次实跑直接验证。

T23 的两次价值分析尝试因分析框架 `framework_changed` 按命令契约停止，退出 0 不代表产出成功；其审计均没有所需报告，故未计为通过。T24 将相同的新鲜度规则提前放入只读预检和服务端提交门禁；T25 处理 Codex workspace-write 网络访问；T26 处理隔离 WebUI 存档变量。只将上述 `job-8d9dcec760e9` 的七个实际报告和匹配模型审计计作 AC-7 的成功生成证据。

## 未通过 / 存疑项

无。基线引用使用本地缓存的 `origin/main`，本轮未刷新远端引用。

## 结论

**通过。** 本报告验证 REQ-015 的父 AC-1 至 AC-8，并逐项检查五个子需求的 AC；全量门禁和真实运行证据满足验收要求。
