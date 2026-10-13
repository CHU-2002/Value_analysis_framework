---
batch: 2026-10-12-REQ-016
date: 2026-10-12
reviewer: independent-acceptance-agent
independence: independent
requirements: REQ-016, REQ-016.1, REQ-016.2, REQ-016.3, REQ-016.4
base: 0587992f813e41a495f929622dc83882a99bec69
full-suite: "1980 passed, 3 skipped，覆盖率 76.43%；make verify 退出0"
---

# REQ-016 独立验收报告（原目录AC-3未通过）

## 独立验收声明

本 agent 未参与产品实现，仅依据 AGENTS.md、需求原文、代码、测试与独立操作核验。未修产品代码，未改 AC、门禁或需求状态。界面操作及证据工具由验收者独立编写；实现者提供的服务地址只作为访问入口，其声明不作通过证据。

初始检查提交为 `356365d9a8b707d7311835ebb6af4cb52452005e`，当时 `scripts/datalayer/pull.py`、`scripts/webui/plugins/research_data.py`、`docs/TEST_SCOPE.md` 存在主线集成改动；最终独立全量门禁覆盖0587992实现（执行树b6f06da仅另含运行记录文档），1980 passed/3既有skip、76.43%；仅验收报告/证据由本人修改，产品、测试与需求状态未改。终态已独立只读复核；原固定目录的普通股边界发现E4，AC-3未通过。0587992的代码修正不改写原快照或原批次。

## 全量测试

```bash
.venv/bin/python -m pytest tests/test_data_directory.py tests/test_watchlist_ui.py tests/test_console_data_page.py -q
COVERAGE_FILE="$PWD/output/.live_req016_acceptance/cdr-final.coverage" make verify
```

独立定向运行退出 0：数据/目录/自选股 `59 passed in 17.43s`；报告/视图 `72 passed in 2.95s`；暂停/目录/报告修复复验 `62 passed in 0.77s`。最终独立 `make verify` 退出0：**1980 passed、3 skipped，覆盖率76.43%**（门槛74%）；需求追溯18 passed，scope 49文件/1983用例（≤52/2000），lint及回归门禁通过。专属COVERAGE_FILE仅隔离证据输出，不改变门槛或测试；最新独立完整日志见 [verify-cdr-final.log](../run-records/REQ-016-independent/verify-cdr-final.log)（1980 passed、3 skipped，111.63秒），追溯18 passed，scope49/1983、回归门禁通过；先前b1dd417/6bed4c7日志作为历史保留。既有3项integration skip为全mock基线，不是本次为通过新增skip。

## 逐条验收

### REQ-016 A股全市场数据采集

- [x] **AC-1**：真实浏览器分别进入伊利与五粮液数据页，范围选择器恰有“已维护公司”“全部A股”。同一断言在旧版两公司各失败、改后均通过。其余档位、期次控件仍存在。
- [x] **AC-2**：隔离清单仅伊利启用、五粮液停用，伊利有磁盘报告。分别从当前伊利/五粮液生成计划，均严格只含 `600887.SH`，计划摘要相等；公司清单详情可核对数量。定向测试进一步检验无关当前公司和空启用清单拒绝路径。
- [ ] **AC-3**：原固定快照确为Tushare目录、5573标的（沪2320/深2904/北349）及三市场confirmed，但独立终态核对发现689009.SH九号公司CDR被真实源标注科创板而纳入，违反本条A股普通股边界。此前mock中的market=CDR过滤通过不足以证明真实目录边界，纠正先前PASS。0587992已补689代码段规则、独立9项目录测试通过；旧目录/批次仍保留该标的，需新的真实目录及固定计划范围复验后才可关闭E4。
- [x] **AC-4**：浏览器看到目录时点、目标1家、档位/期次、中文数据组、8目标、新增/复用数量，以及配额/权限/耗时的“未知”与原因。计划查询只读本地仓，未创建批次；测试用 DataAccess 外部调用拒绝断言保证计划不发数据请求。
- [x] **AC-5**：取消原生联网确认后批次仍为空，显式确认方创建批次。E1已修复并独立复验：真实两进程、mock数据源的外部worker完成首目标后按GUI共享pause停下；重启仅请求其余2目标，原快照/inputs保持不变。原版active=[]却成功应答的缺陷证据与新版5检查0失败均保留。 E3已独立关闭：冻结6bed同受控长预取场景中current缺失、原生GUI前测退出1；b1dd417后测显示全部A股/真实财报组，退出0。另日线后测同时核对全部A股/行情与估值/20260101交易日期，和实际mock provider请求相等，2检查0失败。
- [x] **AC-6**：审阅 explain_batch、中文分组表与定向失败路径：按公司/组输出 ok、empty、no_permission、rate_limited、error、unattempted/stale，逐目标有获取时点与后续动作；部分失败保持 partial，未完成数量明确。最终真实批次分布已在AC-7终态章节独立复算。
- [ ] **AC-7**：实跑执行及终态留证已完成，但全验收仍pending。owner明确确认的原固定批次已done，44584/44584目标均经正式serves_target验证可服务；explain_batch仍done，ok44441/empty143、无权限/错误/限频/覆盖不足/未完成均0，实际批次请求34324、历史复用5440。可复制命令/环境/退出0/目录时点/结果分布及发现的问题均记录；原确认目录仍包含CDR，不能把该终态批次宣称为符合普通股范围的完整通过；须完成修正范围的真实目录/计划复验，AC-3/AC-7保留未通过，REQ-016整体不通过。

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

主线真实批次 `3acb1a5d48544d6ba3703e68a7004313` 证据位于 `output/.live_req016_delivery`，本验收者只读 SQLite核对目录与计划。目录UTC `2026-10-12T15:17:19.023752+00:00` 对应北京时间2026-10-12 23:17。计划快照内容SHA256 `bbc8736398aaf763b49f7981d5880a04c3c79a23b304714d66d4546c55006d74`。其终态现已独立复核，见下文；此前进行中记录作为历史证据保留。

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

## 全市场终态实跑与只读独立核对

原确认批次`3acb1a5d48544d6ba3703e68a7004313`于2026-10-13 17:55:01北京时间（SQLite heartbeat_at=`2026-10-13T09:55:01.357167+00:00`）终态done。本人于18:27北京时间完成SQLite mode=ro/query_only=ON核对：计划digest和目录内容SHA256重新计算一致，batch.targets与原plan.targets逐项相等，目标ticker集合与原快照相等；每公司8目标，batch_target无遗漏或额外键。正式serves_target逐44584目标复算全部通过，explain_batch复算仍done、未完成0。代码业务判定保持原样，只读适配连接避免DataStore初始化写meta；临时核验程序已清理，产物保留。

[终态独立观察](../run-records/REQ-016-independent/live-terminal-observations.json)、[独立核验完整输出](../run-records/REQ-016-independent/live-terminal-check.log)、[实跑运行记录](../run-records/REQ-016-live-batch.md)、[逐公司分布CSV](../run-records/REQ-016-live-company-distribution.csv)。CSV的5573行与SQLite逐目标结果全量交叉核对一致，71家公司有至少一个合法空响应。

| 数据集 | 目标 | ok | empty |
|---|---:|---:|---:|
| stock_basic | 5573 | 5573 | 0 |
| daily | 5573 | 5573 | 0 |
| income（报告类型1/6） | 11146 | 11074 | 72 |
| balancesheet（报告类型1/6） | 11146 | 11078 | 68 |
| cashflow | 5573 | 5571 | 2 |
| fina_indicator | 5573 | 5572 | 1 |
| 合计 | 44584 | 44441 | 143 |

公司目标no_permission、rate_limited、error、stale、unattempted均0。actual_requests=34324；直接逐目标新请求33704；历史存档复用5440；本批预取生成的缓存目标5440，因此explain的新增目标39144+历史复用5440=44584。请求数与目标数按各自定义记录，合法空响应不补数值。

VIP是六个逻辑查询组合（income报告类型1/6、balancesheet报告类型1/6、cashflow、fina_indicator）；独立数两份真实日志，resume-bulk.log和resume-final.log各6条永久权限拒绝，共12次VIP实际请求，已计入34324。它们回退标准单代码接口，最后公司目标无权限0；不能将优化权限失败移作公司空结果。另两次批次外诊断独立计数：income_vip权限探针1次no_permission、双代码标准income探针1次0行（未写作两个公司empty），不包含在34324中。

实际响应列还存在已观察的字段缺失：income的dt_eps在11074个ok响应中未提供；balancesheet的intang_assets在11078个ok响应中未提供；cashflow的c_pay_to_staff/receiv_tax_refund各5571；fina_indicator的npl_ratio/prov_cov_ratio/cap_adequacy_ratio/core_cap_adequacy_ratio/net_int_margin/cost_income_ratio各5572。目标服务验证覆盖仓读取兼容性，字段列与数值仍按实际响应保留缺失；未据此宣称所有请求字段齐全，也未推断这些字段的正确别名或行业适用性。

真实环境仍为macOS、Python3.12.13、项目.venv、Tushare token只从既有.env/环境读取；原始仓/缓存和产物写output/.live_req016_delivery，真实HOME存档未作写入目标。CLI终态日志显示done，按该命令正常返回路径退出0；运行记录的原生执行结果也记录0。未重新执行resume或修改终态存档。

```bash
.venv/bin/python scripts/market_acquisition.py plan \
  --store output/.live_req016_delivery/archive \
  --plan output/.live_req016_delivery/plan-frugal.json \
  --scope all_a --tier frugal --periods 20260630
# 当时owner复核44584请求预估和未知项并明确确认；后续同ID续跑：
.venv/bin/python scripts/market_acquisition.py resume \
  --store output/.live_req016_delivery/archive \
  --plan output/.live_req016_delivery/plan-frugal.json \
  --batch-id 3acb1a5d48544d6ba3703e68a7004313 --yes \
  > output/.live_req016_delivery/resume-final.log 2>&1
# 本验收仅作只读复算；下列命令不会重新采集或写meta：
.venv/bin/python - <<'PYCODE'
from pathlib import Path
import sqlite3, sys
sys.path.insert(0,'scripts')
from datalayer.store import DataStore
from webui.plugins.research_data import explain_batch
class ReadOnlyStore(DataStore):
    def _connect(self):
        if self._conn is None:
            self._conn=sqlite3.connect(self.db_path.resolve().as_uri()+'?mode=ro',uri=True)
            self._conn.row_factory=sqlite3.Row
            self._conn.execute('PRAGMA query_only=ON')
        return self._conn
s=ReadOnlyStore('output/.live_req016_delivery/archive')
b=s.load_batch('3acb1a5d48544d6ba3703e68a7004313')
print('serves_target',sum(s.serves_target(t) for t in b['targets']),len(b['targets']))
r=explain_batch(s,b)
print({k:r[k] for k in ['status','total','counts','actual_requests','reused','uncompleted','failures']})
PYCODE
```

## E4 原目录普通股范围未通过

只读原快照发现689009.SH九号公司，metadata.market=科创板、list_status=L、币种CNY，原批次因此包含该证券8目标。其2026H1原始披露摘要明确公司存托凭证简况及境内存托凭证持有人均适用，上市的是CDR。[公司原始半年报摘要（法定信披）](https://paper.cnstock.com/html/2026-08/11/content_2253825.htm)、[上交所公司页面（区分A/B/CDR）](https://www.sse.com.cn/star/market/stocklist/info/company/index.shtml?COMPANY_CODE=689009)。[原始目录与受影响目标观察](../run-records/REQ-016-independent/cdr-directory-gap.json)。

独立审阅0587992：directory增加SSE689代码段排除，不再依赖market字符串；原有测试补真实科创板标注的九号CDR，独立9 passed。修复不删除历史记录、不改原snapshot_id/target集合/AC；原固定快照仍不是纯普通A股范围，AC-3保持FAIL。需要修正代码后新的真实目录及计划范围证据，不能用mock通过或原批次done替代这个边界复验。


## 结论

原确认批次实跑执行与终态复算已完成；AC-7记录事实充分，但因确认范围含CDR，整条AC-7仍pending。REQ-016.1～.4原有逐AC结论保持通过，E1/E2/E3已独立复验。父AC-3及AC-7因原确认快照纳入CDR而未通过，REQ-016整体不通过、不得推进verified；代码修复已审阅并通过独立目录测试，真实目录范围复验尚未完成。需求状态及验收标准均未由本验收者修改。
