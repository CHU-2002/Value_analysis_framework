---
batch: 2026-10-12-REQ-016
date: 2026-10-12
reviewer: independent-acceptance-agent
independence: independent
requirements: REQ-016, REQ-016.1, REQ-016.2, REQ-016.3, REQ-016.4
base: b1dd417b63934e73bf02fe3cff579036c83a4789
full-suite: "1980 passed, 3 skipped，覆盖率 76.43%；make verify 退出0"
---

# REQ-016 独立验收报告（进行中，不能用于全量收口）

## 独立验收声明

本 agent 未参与产品实现，仅依据 AGENTS.md、需求原文、代码、测试与独立操作核验。未修产品代码，未改 AC、门禁或需求状态。界面操作及证据工具由验收者独立编写；实现者提供的服务地址只作为访问入口，其声明不作通过证据。

初始检查提交为 `356365d9a8b707d7311835ebb6af4cb52452005e`，当时 `scripts/datalayer/pull.py`、`scripts/webui/plugins/research_data.py`、`docs/TEST_SCOPE.md` 存在主线集成改动；最终独立全量门禁在稳定提交 `b1dd417b63934e73bf02fe3cff579036c83a4789` 执行；仅验收报告/证据为未提交文件，产品、测试与需求文件干净。报告当前诚实保留AC-7未完成项。

## 全量测试

```bash
.venv/bin/python -m pytest tests/test_data_directory.py tests/test_watchlist_ui.py tests/test_console_data_page.py -q
COVERAGE_FILE="$PWD/output/.live_req016_acceptance/final.coverage" make verify
```

独立定向运行退出 0：数据/目录/自选股 `59 passed in 17.43s`；报告/视图 `72 passed in 2.95s`；暂停/目录/报告修复复验 `62 passed in 0.77s`。最终独立 `make verify` 退出0：**1980 passed、3 skipped，覆盖率76.43%**（门槛74%）；需求追溯18 passed，scope 49文件/1983用例（≤52/2000），lint及回归门禁通过。专属COVERAGE_FILE仅隔离证据输出，不改变门槛或测试；最终完整日志见 [verify-final.log](../run-records/REQ-016-independent/verify-final.log)（1980 passed、3 skipped，138.65秒）；此前6bed4c7的76.42%历史日志仍保留为verify.log。既有3项integration skip为全mock基线，不是本次为通过新增skip。

## 逐条验收

### REQ-016 A股全市场数据采集

- [x] **AC-1**：真实浏览器分别进入伊利与五粮液数据页，范围选择器恰有“已维护公司”“全部A股”。同一断言在旧版两公司各失败、改后均通过。其余档位、期次控件仍存在。
- [x] **AC-2**：隔离清单仅伊利启用、五粮液停用，伊利有磁盘报告。分别从当前伊利/五粮液生成计划，均严格只含 `600887.SH`，计划摘要相等；公司清单详情可核对数量。定向测试进一步检验无关当前公司和空启用清单拒绝路径。
- [x] **AC-3**：独立读取真实 SQLite 快照确认来源 `Tushare stock_basic`、获取时点、5573 标的，沪2320/深2904/北349均confirmed、gaps为空。过滤代码及反向用例验证 ST 保留、B股/ETF/CDR/退市排除、未知字段或覆盖缺口不静默通过。计划与批次固定该快照元数据。
- [x] **AC-4**：浏览器看到目录时点、目标1家、档位/期次、中文数据组、8目标、新增/复用数量，以及配额/权限/耗时的“未知”与原因。计划查询只读本地仓，未创建批次；测试用 DataAccess 外部调用拒绝断言保证计划不发数据请求。
- [x] **AC-5**：取消原生联网确认后批次仍为空，显式确认方创建批次。E1已修复并独立复验：真实两进程、mock数据源的外部worker完成首目标后按GUI共享pause停下；重启仅请求其余2目标，原快照/inputs保持不变。原版active=[]却成功应答的缺陷证据与新版5检查0失败均保留。 E3已独立关闭：冻结6bed同受控长预取场景中current缺失、原生GUI前测退出1；b1dd417后测显示全部A股/真实财报组，退出0。另日线后测同时核对全部A股/行情与估值/20260101交易日期，和实际mock provider请求相等，2检查0失败。
- [x] **AC-6**：审阅 explain_batch、中文分组表与定向失败路径：按公司/组输出 ok、empty、no_permission、rate_limited、error、unattempted/stale，逐目标有获取时点与后续动作；部分失败保持 partial，未完成数量明确。真实全市场批次最终结果仍须在 AC-7 记录。
- [ ] **AC-7**：只读核对了真实全市场目录与预估计划，5573 公司、44584 目标、新增44584、复用0。owner已明确确认启动的真实批次尚未完成，不能用运行中的进度或小批次替代本条。

### REQ-016.1 报告列表只展示面向用户的产物

- [x] **AC-1.1**：在伊利及五粮液隔离素材中添加七种内部/未知文件，真实报告页全部隐藏，用户报告及source.md仍能浏览；同素材旧版本356365d前测七种全部被抓住，退出1，改后两公司均退出0、failures=[]。
- [x] **AC-1.2**：白名单按支持文件名/目录分类，未知unknown.bin隐藏；原始技术索引与所有文件仍存在。独立相关测试72 passed包含分类与缓存版本失效回归；列表查询没有写入/删除路径。

### REQ-016.2 变化报告先讲清经营变化与关键归因

- [x] **AC-2.1**：伊利读者报告先给累计口径2026H1/2025H1、收入增长4.13%和归母利润下降20.02%，再讲重大解释；正文主线不使用流水线术语。指标缺失分支明确未核实，五粮液样例没有凭空补当前期同比。
- [x] **AC-2.2**：两份读者报告在正文前段“重要事项与主要解释”说明日期/期间、事项、为什么重要、报表金额、影响及未核实边界，商誉与会计更正/历史处罚均有日常中文解释。
- [x] **AC-2.3**：独立核对官方原报告，伊利读者报告显著说明澳优商誉与存货减值是资产减值增加重要原因、商誉费用含义及税前/归母贡献未核实边界；E2已补“存货跌价损失及合同履约成本减值损失”907,520,836.34/315,591,928.82本期/上期合并口径，不擅自拆分。重新独立生成产物并同断言浏览器复验，从缺该金额exit1变为0失败exit0。
- [x] **AC-2.4**：独立打开五粮液2026/011更正公告及证监会2011/17原决定，核对更正涉及2025一至三季度、收入核算、合并利润/资产项目而现金流列示不受影响，以及历史披露违法四项/60万元处罚；产物分别标日期与认定状态，无串期或更正即造假定性。
- [x] **AC-2.5**：读者产物逐事项给官方原文件链接、日期、页/表定位，标公司解释/监管确认事实与未核实边界；输入schema要求公司/期次绑定、枚举认定状态及来源定位，缺指标/事项不补事实，真实来源经独立回查；新版字段级field_statuses区分已披露事实/公司解释/分析推断/未核实，不再以一个事件级标签覆盖所有金额与解释。
- [x] **AC-2.6**：摘要位于正文开头，事项后即有点击来源及定位；“证据限制与后续动作”在后，body参数保留原有详细变化/证据/缺口；缺事项时明确需补原报告附注/公告/监管文件。缺失/损坏输入分支及正文先后顺序测试通过。

### REQ-016.3 在界面维护自选股启用状态与公司信息

- [x] **AC-3.1**：真实鼠标/键盘在数据页编辑伊利为“验收伊利”并切换基础档位，执行停用、启用；停用后两条清单仍存在且公司不参与启用目标集合。旧版同一probe能抓住三个缺失入口。
- [x] **AC-3.2**：刷新数据页显示新简称、基础档位；公司图表页显示“清单：已停用 · 基础必需数据”，重新启用后显示已启用。定向测试核对清单、公司索引、公司解析、计划一致；新增文案约束测试通过。
- [x] **AC-3.3**：浏览器实际执行编辑、停用、启用、移除后，唯一原始记录与操作前完全相等、公司报告和记录文件逐字节相等。停用卡明确“仍保留在清单”，移除卡明确“不再保留该条目”，移除有单独二次确认。

### REQ-016.4 全 A 股目录快照与批次追踪

- [x] **AC-4.1**：SQLite 独立 directory_snapshot 表保存 source/fetched_at/snapshot_id/content_sha256 与逐标的 ticker/name/exchange/list_status；与 universe 启用状态完全分离，additive迁移及重开仓用例通过。
- [x] **AC-4.2**：真实计划与运行批次均固定 `a107a05c417d4762bb7145d74260f442`，目标44584对应公司5573，批次目标集合持久化；make_plan使用指定id、恢复用原batch.targets，不读取新latest替换目标。暂停元数据保留集成改动仍须最终门禁覆盖。
- [x] **AC-4.3**：刷新INSERT新id，旧快照查询可复原；测试实证第二快照新增/减少/名称变化、历史保留、缺失字段与交易所覆盖未知、token错误信息脱敏。compare明确目录减少不能单独认定退市。

## 实跑记录

环境：本机 macOS、项目 `.venv` Python、真实无头浏览器 CDP。所有本验收仓、缓存、公司报告与浏览器profile写入 `output/.live_req016_acceptance`；真实 HOME 存档未作为写入目标。界面实跑没有发起真实公司数据采集，不读取或打印 token。

证据源脚本与选取截图保存于 [`../run-records/REQ-016-independent/`](../run-records/REQ-016-independent/)。命令从仓库根执行；本机旧版服务8786来自隔离实现worktree，改后服务8787为当前主线，两者均显式沙箱仓。脚本仅使用GUI原生点击与键盘执行配置动作，读DOM/只读API核验结果，不调用产品stub。

```bash
WEBUI_OUTPUT_ROOT="$PWD/output/.live_req016_acceptance/companies" \
WEBUI_ARCHIVE_ROOT="$PWD/output/.live_req016_acceptance/archive" \
.venv/bin/python -m scripts.webui --port 8787 \
  --cache-dir output/.live_req016_acceptance/cache --no-browser

.venv/bin/python docs/run-records/REQ-016-independent/gui_check.py
.venv/bin/python docs/run-records/REQ-016-independent/gui_probe.py \
  --base http://127.0.0.1:8786 --out output/.live_req016_acceptance/gui-before
.venv/bin/python docs/run-records/REQ-016-independent/gui_probe.py \
  --base http://127.0.0.1:8787 --out output/.live_req016_acceptance/gui-probe-after
.venv/bin/python docs/run-records/REQ-016-independent/gui_plan.py
```

真实输出：

```text
改前 probe：exit 1，checks=8，failures=[600887.SH scope exactly maintained/all-A,600887.SH enable GUI entry,600887.SH disable GUI entry,600887.SH edit GUI entry,000858.SZ scope exactly maintained/all-A,000858.SZ enable GUI entry,000858.SZ disable GUI entry,000858.SZ edit GUI entry]
改后 probe：exit 0，checks=8，failures=[]
改后维护：exit 0，checks=12，failures=[]
改后计划/取消：exit 0，checks=4，failures=[]
```

补充修复实跑：

```bash
REQ016_IPC_ROOT="$(mktemp -d output/.live_req016_acceptance/pause-recheck.XXXXXX)" \
.venv/bin/python docs/run-records/REQ-016-independent/external_pause_after.py
.venv/bin/python -m scripts.results.report_presentation \
  --context docs/run-records/req016-reader-inputs/yili-context.json \
  --events docs/run-records/req016-reader-inputs/yili-events.json \
  --output output/.live_req016_acceptance/companies/600887_伊利/change_report_reader_2026H1.md
.venv/bin/python scripts/gui_report_catalog_walkthrough.py \
  --base http://127.0.0.1:8788 --company 600887_伊利 --artifact 202b29b8363b \
  --out output/.live_req016_acceptance/reader-inventory-after \
  --require 澳优 --require 907,520,836.34 --require 1,546,544,866.49 --require 少数股东
```

`external_pause_after.py`为受控跨进程验证，数据提供方使用mock，不算AC-7真实数据采集。8788使用相同沙箱配置、独立cache-final运行集成报告版；报告素材与新renderer由验收者自行生成，来源三份原文件已独立回查。

截图：[改前数据页](../run-records/REQ-016-independent/data-before.png)、[改后停用公司页](../run-records/REQ-016-independent/disabled-company-page.png)、[计划取消后](../run-records/REQ-016-independent/plan-after-cancel.png)。完整断言和DOM观察在四份 `*-observations.json` 中。

主线真实批次 `3acb1a5d48544d6ba3703e68a7004313` 证据位于 `output/.live_req016_delivery`，本验收者只读 SQLite核对目录与计划。目录UTC `2026-10-12T15:17:19.023752+00:00` 对应北京时间2026-10-12 23:17。计划快照内容SHA256 `bbc8736398aaf763b49f7981d5880a04c3c79a23b304714d66d4546c55006d74`。批次尚在运行；完整命令、退出码、请求/复用与最终分布待结束后独立核对。

## 未通过 / 存疑项

E1（已修复、独立复验通过）：外部 CLI 同仓批次暂停误报。CollectionRunner.pause只查内存active并set本进程Event，查不到仍返回成功“暂停已请求”；GUI却给所有running批次画暂停按钮。隔离仓记录具有真实存活owner PID的running批次，POST暂停，返回ok且内存active为空、磁盘status仍running；受控模拟证据见 `external-pause-observations.json`。这份模拟只证明无pause信号路径，不能把执行中瞬时status当成暂停完成失败；代码中不存在跨进程标记/IPC才是关键。主线登记REQ-016 T5，改为仓内共享暂停请求，旧版不支持的worker明确报错。独立复验脚本external_pause_after.py启动真实两个进程，mock数据源避免消耗配额：首进程完成stock_basic后paused（1/3），第二进程只请求daily/income后done（3/3），snapshot/inputs unchanged，exit0、failures=[]。

E2（已修复、独立复验通过）：原伊利读者产物没有“存货跌价损失及合同履约成本减值损失”金额；官方2026H1报告印刷第164页该合并报表行本期损失907,520,836.34元，上期315,591,928.82元。该行包含合同履约成本，不能写作澳优单体或纯存货金额。主线补准确报表行名、口径、定位及字段级事实/解释标签；独立运行renderer后native reader相同关键断言exit0、failures=[]。前测inventory-before和后测inventory-after观察/截图均保存，未改验收标准。

E3（已修复、独立复验通过）：真实批次预取阶段残留旧公司/数据组。原始SQLite观察见[bulk-progress-before.json](../run-records/REQ-016-independent/bulk-progress-before.json)。冻结6bed4c7代码、使用真实服务/worker和受控mock长预取重现：running/collecting_bulk时current=null，原生GUI同判据前测退出1，failures=[bulk current company/group displayed truthfully]。真实批次后来已暂停时无current属于正常现象，未将该截图作为缺陷证据。

b1dd417修复后，同financial fixture和同GUI判据退出0，failures=[]，界面显示“当前公司：全部A股；当前业务组：财报/公司基本信息”；实际provider请求是income_vip、period20260630。日线后测显示“当前公司：全部A股；当前业务组：行情与估值；当前交易日期：20260101”，与provider请求trade_date20260101逐项相符，2检查0失败。当前请求字段经正式DataAccess.current_request传递，等待限频回调仍保留上下文。

证据：[受控前测观察](../run-records/REQ-016-independent/bulk-controlled-before.json)、[前截图](../run-records/REQ-016-independent/bulk-controlled-before.png)、[同场景后测观察](../run-records/REQ-016-independent/bulk-controlled-after.json)、[后截图](../run-records/REQ-016-independent/bulk-controlled-after.png)、[日线观察](../run-records/REQ-016-independent/daily-controlled-after.json)、[日线截图](../run-records/REQ-016-independent/daily-controlled-after.png)、[真实请求与GUI对照](../run-records/REQ-016-independent/daily-request-vs-gui.json)。这些是真实浏览器走查、mock数据提供方，不能替代AC-7真实采集。

```bash
mkdir -p output/.live_req016_acceptance/bulk-before-src
git archive 6bed4c7 | tar -x -C output/.live_req016_acceptance/bulk-before-src
# 三个服务各自运行于独立终端；fixture会在mock首个响应保持90秒，供浏览器观察
.venv/bin/python docs/run-records/REQ-016-independent/bulk_progress_fixture.py \
  --source output/.live_req016_acceptance/bulk-before-src \
  --root output/.live_req016_acceptance/bulk-controlled-before --port 8791
.venv/bin/python docs/run-records/REQ-016-independent/bulk_progress_gui.py \
  --base http://127.0.0.1:8791 --out output/.live_req016_acceptance/bulk-controlled-before/browser
.venv/bin/python docs/run-records/REQ-016-independent/bulk_progress_fixture.py \
  --source . --root output/.live_req016_acceptance/bulk-controlled-after --port 8792
.venv/bin/python docs/run-records/REQ-016-independent/bulk_progress_gui.py \
  --base http://127.0.0.1:8792 --out output/.live_req016_acceptance/bulk-controlled-after/browser
.venv/bin/python docs/run-records/REQ-016-independent/bulk_progress_fixture.py \
  --source . --root output/.live_req016_acceptance/daily-controlled-after --port 8793 --dataset daily
.venv/bin/python docs/run-records/REQ-016-independent/bulk_progress_gui.py \
  --base http://127.0.0.1:8793 --out output/.live_req016_acceptance/daily-controlled-after/browser \
  --expect-date 20260101
```


## 全市场预取独立审阅

稳定版本的预取只对已明确确认的全部A股批次启用：保留原targets和snapshot，金融数据限定end_date/report_type，daily逐日验证trade_date；按实际返回行数推进offset，只有空末页才能结束分页。重复页、字段/代码缺失、错date、末页未确认、预算不足或暂停均不把半窗口写仓；缺标的不记empty，回退原逐标的请求。stock_basic从已有目录记录分区并保留源时点/批次，新增本批预取与历史复用分别统计。

独立补充受控反例：金融接口完整分页返回错误期次/不同report_type以及缺少目标ticker时，2请求、0目标记录写入、6目标回退、false_complete=false，见bulk-negative-final.json。新版可保存1条完整原始聚合响应检查点，但没有将其冒充任一公司目标完成。全量回归同时覆盖忽略offset、响应封顶短页、缺字段/缺ticker、错daily日期、后半窗口失败/暂停/预算不足；原目标不变，不能以这些mock结果替代AC-7真实完整批次。

## 原始来源独立核对

- [伊利2026H1官方报告](https://static.cninfo.com.cn/finalpage/2026-08-27/1225511409.PDF)：印刷第3页收入/归母同比；第140页澳优商誉计提；第164页附注七73列商誉及存货/合同履约成本损失和重要原因。独立打开原PDF核对，未用搜索摘要替代。
- [五粮液更正公告2026/011](https://static.cninfo.com.cn/finalpage/2026-04-30/1225273122.PDF)：第1页原因、影响期间与现金流边界，第3—4页2025H1重述表；样例列报金额和期间对应。
- [证监会〔2011〕17号决定](https://www.csrc.gov.cn/csrc/c101928/c1043253/content.shtml)：违法事实一至四、处罚第一项、末尾落款；独立核对历史事项及处罚金额，没有挪作当前更正的监管结论。

## 当前真实批次状态与AC-7待办

2026-10-13 09:59:54北京时间（01:59:54 UTC）独立只读观察：原确认批次3acb1a5d48544d6ba3703e68a7004313仍running，1435/44584目标已完成，分布ok1435；实际请求1961，历史缓存命中94（其中本批新预取47，不等同于旧数据复用），当前为000630.SZ/income/report_type6、等待数据源限频。固定快照仍为a107a05c417d4762bb7145d74260f442。详见[live-status-pending.json](../run-records/REQ-016-independent/live-status-pending.json)。

未观察到此前Codex账户用量“2:55 AM”阻断继续存在；这是本次观察可执行的结论，不是对之后限额的保证。VIP整期接口无权限已真实回退，不能将这些优化失败当成原逐公司金融目标无权限或合法空响应。批次继续逐公司采集，尚无终态退出码，AC-7保持pending且未打勾；终态后再独立核验完整目标、实际请求/复用、结果分布及可复制运行命令。

## 结论

本轮独立验收结束：REQ-016.1～.4逐AC已通过，E1/E2/E3均有独立复验；最终全量门禁通过。父需求AC-1～6通过，AC-7仍待真实全市场批次终态，REQ-016不能推进verified。本报告不改变任何需求状态；后续另行独立复核AC-7并补充终态证据。
