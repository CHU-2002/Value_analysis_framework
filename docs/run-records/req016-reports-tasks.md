# REQ-016.1 / REQ-016.2 报告实现与实跑任务

日期：2026-10-12。实现者自测记录，不代替独立验收；未修改验收标准、需求状态或台账。

| 任务 | 状态 | 问题与处理 | 证据 |
|---|---|---|---|
| T1 普通列表混入内部文件 | 已修复 | `report_type` 改为稳定白名单，目录解析器版本升为 2；报告页两处列表一致过滤。技术索引仍保留，磁盘不删除。 | 分类组合测试；catalog-before/after |
| T2 变化报告主线技术化、重大解释缺失 | 呈现已实现 | 通用读者摘要渲染器先展示同比方向、幅度、期间及重大事项，再保留详细原报告；事项逐项区分来源状态、日期、涉及期间、金额、影响和限制。缺事项输入时明确来源缺口。 | reader-before/after；原始来源核对及两份读者报告 |
| T3 事项识别依赖 | 待 REQ-017 | 本次只消费已核验事项，不实现完整报告的自动识别。示例输入由实现者回查原文件生成，不是 REQ-017 自动发现或独立盲测证据。不能据此声称 REQ-017 完成。 | `req016-reader-inputs/`；REQ-017 原有判据 |
| T4 旧伊利 context 缺确定性同比 | 已明确缺口 | 旧真实 context 四个指标为 null、summary 包含流水线文字。渲染器不从旧 summary 或模型记忆填数字；空指标明确“未核实”。示例同比另按原报告第3页核对。 | 缺指标回归测试；原报告第3页 |
| T5 本地环回权限限制 | 已复核 | 沙箱启动服务与 socket 测试报 Operation not permitted。实跑使用允许本地环回的环境，写入隔离 output 沙箱；不动真实 HOME 存档。 | GUI observations；控制台测试复跑 |
| T6 缓存测试版本写死 | 已修复 | catalog 版本升级暴露既有测试把升级目标写死为 2。改成从原版本加 1，仍要求两类解析均重新执行且版本准确。 | `test_report_reader_caches_derivations_and_invalidates_fact_sources` |
| T7 scope 汇总 | 交主线 | 新增 4 条测试，未增删测试文件；主线按分工统一再生 TEST_SCOPE 并完成全量门禁。此分支未改门禁/阈值/登记表。 | 全量首跑覆盖率 75.66% |

## 实跑记录

环境：macOS、Python 3.12.13、真实 Microsoft Edge 无头浏览器；本地服务 127.0.0.1:8786；全部产物写入本 worktree 的 `output/.live_req016_reports/`。本次没有使用 token、没有采集公司数据、没有写真实存档。报告素材从现有本地产物复制到沙箱；原始事实另按下方官方来源核对。

服务启动：

```bash
WEBUI_OUTPUT_ROOT="$PWD/output/.live_req016_reports" ../.venv/bin/python -m scripts.webui --port 8786 --archive-root "$PWD/output/.live_req016_reports/archive" --cache-dir "$PWD/output/.live_req016_reports/cache" --no-browser
```

分类前后使用相同页面动作，前测真实抓住 `latest.json` / `record.json`；后测 failures=[]，且两文件仍在磁盘。观察和截图分别为 [前测](req016-report-evidence/catalog-before.json)、[前截图](req016-report-evidence/catalog-before.png)、[后测](req016-report-evidence/catalog-after.json)、[后截图](req016-report-evidence/catalog-after.png)。前测退出码 1；后测退出码 0。可复制后测命令：

```bash
../.venv/bin/python scripts/gui_report_catalog_walkthrough.py --out output/.live_req016_reports/catalog-check --forbid latest.json --forbid record.json
```

主线数据范围前测补充：[观察](req016-report-evidence/data-before.json)、[截图](req016-report-evidence/data-before.png)。旧选择器显示“当前公司 / 启用自选股”；该 worktree 的数据页未参与修改，供主线做前后对照。

伊利正文前后使用同样的关键解释检查。原报告前测：failures=[未找到：澳优, 未找到：1,546,544,866.49]，退出 1。新读者报告：failures=[]，退出 0。见 [前观察](req016-report-evidence/reader-before.json)、[前截图](req016-report-evidence/reader-before.png)、[后观察](req016-report-evidence/reader-after.json)、[后截图](req016-report-evidence/reader-after.png)。

```bash
../.venv/bin/python -m scripts.results.report_presentation --context docs/run-records/req016-reader-inputs/yili-context.json --events docs/run-records/req016-reader-inputs/yili-events.json --output output/.live_req016_reports/600887_伊利/change_report_reader_2026H1.md
../.venv/bin/python scripts/gui_report_catalog_walkthrough.py --out output/.live_req016_reports/reader-check --artifact 202b29b8363b --require 澳优 --require 商誉减值 --require 1,546,544,866.49 --require 2,455,875,173.02 --require 少数股东
../.venv/bin/python -m scripts.results.report_presentation --context docs/run-records/req016-reader-inputs/wuliangye-context.json --events docs/run-records/req016-reader-inputs/wuliangye-events.json --output output/.live_req016_reports/000858_五粮液/change_report_reader_2026H1.md
../.venv/bin/python scripts/gui_report_catalog_walkthrough.py --out output/.live_req016_reports/wuliangye-check --company 000858_五粮液 --artifact 202b29b8363b --require 2025 --require 2011 --require 监管确认事实 --require 现金流量表 --require 4,623,850,715.13
```

五粮液 GUI 检查退出 0，failures=[]；[观察](req016-report-evidence/wuliangye-after.json)、[截图](req016-report-evidence/wuliangye-after.png)。最终自测样例：[伊利读者报告](req016-report-evidence/yili-reader.md)、[五粮液读者报告](req016-report-evidence/wuliangye-reader.md)。五粮液示例没有补造当前期同比；其经营指标来源未提供时明确未核实。

## 原始来源核对

- [伊利2026半年度报告](https://static.cninfo.com.cn/finalpage/2026-08-27/1225511409.PDF)：第3页主要会计数据列示收入同比 +4.13%、归母净利润同比 -20.02%；印刷第164页附注七（73）列示资产减值损失与商誉减值，并解释澳优商誉与存货减值增加。本输入保留损失正负口径，解释税前减值不等同于归母利润下降额。
- [五粮液2026/第011号更正公告](https://static.cninfo.com.cn/finalpage/2026-04-30/1225273122.PDF)：第1页为原因与涉及期间，第3—4页为2025H1更正表。更正与收入确认核算调整有关，现金流量表列示不受影响；报告只按公司披露描述，不给造假定性。
- [证监会〔2011〕17号决定](https://www.csrc.gov.cn/csrc/c101928/c1043253/content.shtml)：逐条核对违法事实一至四、处罚第一项、2011-04-29落款；年报录入差错与未及时更正并非“业绩快报利润总额有误”。报告将这份历史决定和2025年更正分开，不串期或补写复议执行状态。

伊利原始 PDF 首次读取超时，重试成功；没有用搜索摘要代替原文件核对。三份事实来源已回查；模型发现完整性与未点名公司盲测仍由 REQ-017 处理。

## 开发自检

受影响视图、变化报告及更新文档合同测试：105 passed。允许本地环回的环境复跑控制台框架与服务器测试：49 passed。首轮 `make verify PYTHON=../.venv/bin/python`：1944 passed / 15 failed / 5 skipped，覆盖率 75.66%。15 失败中 13 项为 sandbox socket 权限，1 项为主线统一更新前的 scope，1 项为已修复的缓存版本测试。没有降低门禁、删断言或添加 skip；主线收口时仍须完整 `make verify` 绿。
