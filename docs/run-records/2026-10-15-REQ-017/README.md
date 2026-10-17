# REQ-017 开发实跑记录

日期：2026-10-15（Asia/Shanghai）。全部写入 `output/.live_req017/` 或独立评估者 `output/.live_req017_eval/`，未改用户真实存档；使用本机 Codex 已有 ChatGPT 登录，未读取或输出 token。PDF 解析使用项目 `.venv` 的 pdfplumber，模型 CLI 版本0.157.0。

## 官方材料

- 伊利2026半年度完整报告219页：<https://static.cninfo.com.cn/finalpage/2026-08-27/1225511409.PDF>。
- 五粮液2026/第011号更正公告6页：<https://static.cninfo.com.cn/finalpage/2026-04-30/1225273122.PDF>。
- 证监会〔2011〕17号决定：<https://www.csrc.gov.cn/csrc/c101928/c1043253/content.shtml>。直接HTTP下载403，由浏览工具读取官方全文并保存正文到 `examples/decision.txt`，保留段落及原始URL；正文落款2011-04-29，不能用未核实的网页发布日期冒充决定日期。
- 材料/提示词哈希、页码及范围限制见各运行 `materials.json` / `report_events.json`。本次示例声明未获取独立可比期PDF及全部临时公告；五粮液示例仅给定更正公告和历史决定，不能声称通读其完整年度财报。

## 先失败、后修复

默认 `gpt-6.1-sol`、目录中的 `gpt-5.5` 均被当前CLI登录方式拒绝（`model is not supported when using Codex with a ChatGPT account`），且默认WebSocket连接连续超时后才退HTTPS。首次两个示例均退出1、未发布报告。已登记REQ-017 T4，使用既有已知模型不兼容预检，补显式单次HTTPS传输选项，不改全局配置。

本机模型缓存列出 `gpt-6-luna`；最小调用该模型成功返回结构化“未核实”审计结果，单次官方HTTPS调用退出0、返回OK。随后仅对本次实跑显式传模型和传输方式。

## 可复制命令

```bash
.venv/bin/python -m scripts.results.financial_events \
  --materials output/.live_req017/examples/yili.json \
  --output-dir output/.live_req017/yili-r2 \
  --workers 3 --model gpt-6-luna --transport https
.venv/bin/python -m scripts.results.financial_events \
  --materials output/.live_req017/examples/wuliangye.json \
  --output-dir output/.live_req017/wuliangye-r2 \
  --model gpt-6-luna --transport https
```

上面的清单格式见 `shared/qualitative/agents/financial_events.md`；相对原件路径以清单所在目录解析。模型默认不自动替换，传输选项仅限ChatGPT登录并指向同一官方服务。未取得原文/未核实的归因必须具体列出，不能为了验收隐藏。

## 研发门禁

- 初次默认沙箱：13个WebUI HTTP测试因禁止监听端口失败，1967 passed / 3 skipped；不是代码失败，不放宽断言。
- 允许本地监听后完整 `make verify`：1992 passed / 3 skipped，76.51%，追溯/scope/回归门禁通过。运行期间又合并4例同合同重复用例并增加官方夹具1例，最终稳定版门禁另附。
- 新增离线事项测试覆盖全量阅读/跨页分包、开放类型、原件缺失、公司/期间不匹配、引用造假、数字及归属审核失败、法律定性及否定语境、静默遗漏、可审阅排除、真实调用参数与错误脱敏、报告接入、失败重跑防旧产物、两个官方示例快照。测试不调用网络/真实模型。

## 最终观察

最终真实流程、退出码、事项数量与独立证据见本文末尾的2026-10-18收口记录。独立盲样及AC逐条判断以[独立验收报告](../../verification/2026-10-18-REQ-017-independent-acceptance.md)为准。

## 2026-10-17 继续交付

上轮伊利 `yili-r3` 与两个盲样 `blind_*_run3` 在完整阅读后遭后续模型调用失败，未发布；独立评估者同时报告账号用量限制。不能用已产生候选替代完整验收。登记T11，稳定实现e64f449加入严格断点：材料/分包/提示/模型/管线指纹一致，且每次请求与响应摘要匹配，才允许`--resume`；所有内容和引用检查仍执行，旧无指纹目录不自动迁移。

本次相同公开原件新跑：

```bash
.venv/bin/python -m scripts.results.financial_events --materials output/.live_req017/examples/yili.json --output-dir output/.live_req017/yili-r4 --workers 3 --model gpt-6-sol --transport https
.venv/bin/python -m scripts.results.financial_events --materials output/.live_req017/examples/wuliangye.json --output-dir output/.live_req017/wuliangye-r5 --workers 2 --model gpt-6-sol --transport https
# 仅同一新目录、同一指纹的连接中断恢复才加 --resume
```

五粮液r5：退出0，7页，2个事项全部核验通过，0待核实/排除。更正一次通过；历史处罚初次引文漏一个“月”被本地/模型拒绝，原文repair后第二次全量audit通过。没有直接改审核结果。独立评估者分别核对原件及AC-5/7/8/9，完整产物与审核链由其独立归档。

CI首轮失败为既有并发历史测试运行中状态由running变finished，导致比较跨时刻快照；登记REQ-012 M6，先等待既有终态历史判据，再保留原12线程及全部断言。独立评估确认前置条件公平，生命周期/重启覆盖保留。修复提交0017ad4。e64f449本地完整门禁1990 passed/3 skipped，scope50文件/1993用例；最终收口检查仍以后续稳定版和CI为准。

最新CI在0017ad4（PR129）已绿：[ci-success](https://github.com/CHU-2002/Value_analysis_framework/actions/runs/38939690509/job/117105196257)。这仅证明门①，尚不表示AC-8独立盲样已验收，收口状态仍为in-progress。


### T13独立误拒复现与新基线

独立评估者用虚构公司确认：原文同时给出本期与比较期时，`candidate.period=2026H1/2025H1`不应仅因manifest主期标2026H1被拒。真实伊利r4完成5条核验、11条降级、13条排除，其中部分为同类程序误拒，不能一概当材料不足。70331a2改为主期锚点匹配，逐项期间/数字仍原文审核；补原件身份、报表结构和编制基础页，以提供已有口径证据。AC不变。

新运行显式从相同旧运行迁移完全相同请求的真实响应；材料/分包/完整提示/模型配置必须一致，每次stage+input+schema和响应摘要均需匹配，旧管线及来源写入新缓存和report_events。所有本地检查重做，新增上下文导致audit请求不同则必须新推理；原`--resume`全管线指纹约束没有放宽。

```bash
.venv/bin/python -m scripts.results.financial_events --materials output/.live_req017/examples/yili.json --output-dir output/.live_req017/yili-r5 --reuse-responses-from output/.live_req017/yili-r4 --workers 3 --model gpt-6-sol --transport https
.venv/bin/python -m scripts.results.financial_events --materials output/.live_req017/examples/wuliangye.json --output-dir output/.live_req017/wuliangye-r6 --reuse-responses-from output/.live_req017/wuliangye-r5 --workers 2 --model gpt-6-sol --transport https
```

五粮液r6在新实现退出0，2事项/0待核实排除，7页，全部相同模型请求命中来源明确的真实缓存。伊利5个读包与综合命中，变更的原件审核重新调用。最终AC仍由独立评估核对，不因缓存命中而免审。

### Owner模型纠正与调用量复核（2026-10-17）

owner要求后续仅`gpt-6.1-sol`、`medium`。旧目录把6.1标unavailable导致研发改用sol6；逐包阅读、逐候选audit/repair和多版重跑放大调用量。已停止旧流程，进程清单确认无旧模型任务；新响应至少248份、donor复制79份，不能当精确调用/计费数（独立评估另发现早期无指纹响应）。详见model-policy-review.json。

最小调用默认传输超时且开始自动重试，已中断；同模型同medium的官方HTTPS调用关闭request/stream retries后退出0、输出OK。更新本机兼容目录（不代表其他账号授权），没有更换全局模型或凭据。CLI提示该型号缺少缓存metadata、使用fallback metadata；实际请求型号仍是6.1，不是模型回退。管线显式medium入指纹，HTTPS禁自动重试；先单份伊利完整实跑检验新通俗标准，再开展其他样例。

```bash
.venv/bin/python -m scripts.results.financial_events --materials output/.live_req017/examples/yili.json --output-dir output/.live_req017/yili-sol61-medium --workers 2 --model gpt-6.1-sol --transport https
```

新型号/配置不能命中旧sol6响应，旧通过数不能冒充本次验收。51个相关离线用例通过；最终全量结果另附。

最终模型配置版本f9504ad本地`make verify`退出0：1993 passed / 3 skipped、覆盖率76.76%，追溯18 passed、scope50文件/1996用例、回归绿；完整输出development-verify-f9504ad.log。三个skip为既有TUSHARE_TOKEN集成测试，未新增skip。PR仍草稿，独立盲样与新模型官方实例尚待验收。


### Owner额度与项目默认模型切换（2026-10-17）

Owner说明额度用完，当前会话切为`gpt-6-luna`，并要求项目内同步。已停止伊利`yili-sol61-medium`：失败状态下保存5/5读包（219页）、review（19项入选、22项排除）及14条候选审核链，没有最终报告；之后没有发出真实模型请求。历史产物及工具调用保存于隔离output目录。

项目默认已切为`gpt-6-luna`/`medium`，配置位于`config/agent_models.json`。Codex研究控制台、`agent_action.py`的报告动作及REQ-017模型子流程均显式传该模型/强度，除非用户在单次动作明确选其他模型。用户HOME全局配置未修改。原先6.1探针和被中断实跑只作为历史记录，不表示当前项目仍使用6.1。


项目模型默认切到`gpt-6-luna`/`medium`后，首次全量门禁发现测试假CLI在`AGENT_CLI`设为Claude脚本时，被过早按默认Codex协议处理。已修正为先识别本次实际CLI再选对应项目模型；该修复在5c9bebd。修复后`make verify`退出0：1993 passed / 3 skipped，覆盖率76.75%，追溯18 passed、scope50文件/1996用例、回归门禁通过。完整输出见development-verify-luna-default.log。未启动真实模型调用。

### 2026-10-18 收口实跑与验收

额度刷新后继续执行四份官方/盲样材料，模型均为项目配置`gpt-6-luna`、`medium`，通过ChatGPT官方HTTPS传输；环境为macOS、Python 3.12.13、项目`.venv`、Codex CLI 0.157.0。所有输出位于`output/.live_req017/`或`output/.live_req017_eval/`，未修改`~/turtle_archive`，token未进入日志或产物。

伊利最终运行退出0：219页，6项已复核，18项待核实/排除。五粮液最终运行退出0：7页，完整覆盖更正公告数字与独立历史监管决定；具体处置数量及每项独立结论见验收报告。盲样A退出0：328页，17项已复核、30项待核实/排除。盲样B修复后退出0：301页，4项已复核、22项待核实/排除；独立评估确认冻结的B1越南产能/美国双反政策/固定资产减值、B2资产减值边界和B3经营现金流公司解释均按原文呈现，引用可回查。待核实状态保留，不将其当作已独立证明的外部经济因果。

盲样B首轮完整运行虽完成，但独立评估发现B1和B3内容遗漏，未通过AC-8。修复后的首轮综合已找回对应披露，但一条候选repair响应改变了原候选ID，程序按安全约束中止并将运行标记failed、不生成报告。随后将repair schema限制为原ID，并加强review阶段对全篇阅读notes的原因追踪和原页引用要求；final4完整完成，独立复核通过。首轮失败、final3身份变化与修复后final4均保留在[独立验收证据包](../../verification/req017-independent-package/2026-10-18-blind-B-final4-evidence.zip)中，不能把失败轮次改写成通过。

final4实际命令（从final3迁移时只复用请求、schema和响应摘要完全相同的已存模型响应；12条迁移来源均由独立评估复核，所有语义/引用校验重新执行）：

```bash
.venv/bin/python -m scripts.results.financial_events --materials output/.live_req017_eval/blind_b_materials.json --output-dir output/.live_req017_eval/blind_b_luna_closure_final4 --workers 2 --model gpt-6-luna --transport https --reuse-responses-from output/.live_req017_eval/blind_b_luna_closure_final3
```

观察：退出码0；stdout为`已复核事项 4；待核实/排除 22；已阅读页 301；输出 output/.live_req017_eval/blind_b_luna_closure_final4`。最终`run_state=complete`，result digest为`54fb5bf718a5888370d905731120c13404dfefd831e1e55b79936453ebf88156`，可见重大事项报告SHA256为`bf5e4b90ca42a744a9899a74758da47069ae83c336e8e1bb86294c171610d714`。独立评估核验301页原件与冻结SHA相同、108条可见引用连续匹配、135份保存响应摘要匹配。

本次研发`make verify`退出0：1,997 passed / 3 skipped，覆盖率76.80%，需求追溯18 passed，scope 50个测试文件/2000用例、回归门禁通过。三个skip仍是未设置`TUSHARE_TOKEN`的既有集成测试。完整独立AC-1…9判断、四份命令/观察、final3失败记录与证据清单见[2026-10-18独立验收报告](../../verification/2026-10-18-REQ-017-independent-acceptance.md)。报告所述独立内容复核不替代本地门禁或PR CI。
