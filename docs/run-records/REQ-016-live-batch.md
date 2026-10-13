# REQ-016 沪深北 A 股与境内上市 CDR 真实采集记录 · 2026-10-13

## 结果

用户复核离线计划并明确确认后，原批次 `3acb1a5d48544d6ba3703e68a7004313` 于 2026-10-13 17:55:01（北京时间；SQLite `heartbeat_at=2026-10-13T09:55:01.357167+00:00`）进入 `done`。固定计划的 44,584 个目标全部有终态；读取路径复核结果为 `ok=44,441`、合法空响应 `empty=143`、错误/无权限/限频/过期覆盖/未完成均为 0。

目录快照 ID `a107a05c417d4762bb7145d74260f442`，来源 Tushare `stock_basic`，采集时间 2026-10-12 23:17:19（北京时间），共 5,573 家：上交所 2,320、深交所 2,904、北交所 349；三市场 coverage 均为 confirmed，目录缺口和排除均为 0。目录内容 SHA256：`bbc8736398aaf763b49f7981d5880a04c3c79a23b304714d66d4546c55006d74`。

计划范围是 `all_a`、基础档 `frugal`、财报期次 `20260630`，每日行情区间 `20251012`–`20261012`。计划固定目录快照并生成 44,584 个目标：公司基本信息 5,573、行情与估值 5,573、财报 33,438。离线计划估算新增请求 44,584、复用 0；计划时配额、接口权限和耗时未知。

## 真实目标及调用分布

| 数据集 | 目标数 | ok | empty |
|---|---:|---:|---:|
| `stock_basic` | 5,573 | 5,573 | 0 |
| `daily` | 5,573 | 5,573 | 0 |
| `income`（报告类型 1/6） | 11,146 | 11,074 | 72 |
| `balancesheet`（报告类型 1/6） | 11,146 | 11,078 | 68 |
| `cashflow` | 5,573 | 5,571 | 2 |
| `fina_indicator` | 5,573 | 5,572 | 1 |
| **合计** | **44,584** | **44,441** | **143** |

每家公司均有 8 个目标，5,573 家全部完成；71 家至少有一个财报接口返回合法空响应。逐公司、逐数据集结果枚举见[分布 CSV](REQ-016-live-company-distribution.csv)，其中没有财务数值或凭据。

SQLite 最终 usage：实际数据源请求 34,324 次；逐目标新请求 33,704 次；历史存档复用 5,440 个目标；本批次同源数据生成的缓存目标 5,440 个（全市场行情回填到公司目标）。批次 UI 汇总复算为新增目标 39,144 + 存档复用 5,440 = 44,584。实际请求低于计划估算，因为全市场日行情按日期分页一次读取并供公司目标复用。

### Owner 范围决定后的证券类型复核

2026-10-13，需求 owner 明确选择允许目录范围不局限于 A 股普通股。实现后只读读取原固定快照的 5,573 行并按源字段与已确认的 689 代码段分类：A 股普通股 5,572 家、境内上市 CDR 1 家（689009.SH 九号公司）；沪深北目标集合及原批次的 44,584 个目标均不变。原始目录行保存的 Tushare `market=科创板` 与交易所/代码字段仍保留，CDR 类别由代码段规则正确识别；这项事后分类不是当时计划界面展示过的分布。后续计划界面现分别显示普通股/CDR 数量，原批次文件与请求记录没有改写。

全市场金融预取对 VIP 整期接口执行了两轮尝试：每轮各请求 `income` 两种报告类型、`balancesheet` 两种报告类型、`cashflow` 和 `fina_indicator` 六个组合，共 12 个数据源调用；均实际返回 `no_permission`。实现没有将这些权限错误记作公司空数据，而是按原计划回退至已授权的逐公司标准接口。最终公司目标 `no_permission=0`。最终还没有 `rate_limited`、`error`、stale 或 uncompleted 目标；运行期间的等待限频状态是请求间隔控制，未形成限频失败。

批次外的诊断请求另计：一次 `income_vip` 权限探针返回 `no_permission`；一次把两家公司代码放进标准 `income` 请求的探针返回 0 行，未当作两家公司空结果，也未当作有效的多公司接口。两次合计 2 个诊断请求，不包含在 34,324 次批次请求内。

#### 扩展范围后的离线计划预览

为让 owner 在任何新增联网批次前看到证券类型分布及请求估算，2026-10-13 23:27（北京时间）在隔离副本上生成新的离线计划预览。源是上述已固定目录快照和原批次完成后的本地存档副本；生成过程未访问数据源、未创建批次，也未更改原批次/原存档。

```bash
.venv/bin/python scripts/market_acquisition.py plan \
  --store output/.live_req016_acceptance/preview-store \
  --plan output/.live_req016_acceptance/plan-expanded-review.json \
  --scope all_a --tier frugal --periods 20260630
```

命令退出码 **0**。预览固定快照 `a107a05c417d4762bb7145d74260f442`，目录共 5,573 家（A 股普通股 5,572、境内上市 CDR 1）；生成 44,584 个逻辑目标（公司基本信息 5,573、行情与估值 5,573、财报 33,438），预计新增请求 5,573、复用 39,011。新增请求全部是 2025-10-13 至 2026-10-13 的日线窗口；其余复用结果包括财报和公司基本信息。配额、接口权限与总耗时仍未知。预览文件及独立 SQLite 副本均在 `output/.live_req016_acceptance/`，原批次计划及 archive 未修改。

生成预览时尚未获得联网执行确认，因此当时没有启动批次。随后 owner 明确回复“确认执行这个新计划”，遂按原固定计划和沙箱启动新批次；旧批次 `3acb1a5d48544d6ba3703e68a7004313` 及原 archive 未修改。

#### 新范围全市场批次终态实跑

执行前展示并确认的固定计划为 `output/.live_req016_acceptance/plan-expanded-review.json`：范围 `all_a`、基础档 `frugal`、财报期 `20260630`，日线窗口 2025-10-13 至 2026-10-13；目录快照 `a107a05c417d4762bb7145d74260f442`、计划 digest `b61a795df3434a267ea236beccd5921f35a5a50506bab95a2ebef0b8cb200b6d`。冻结快照分类为 A 股普通股 5,572 家、境内上市 CDR 1 家；沪深北覆盖分别为 2,320、2,904、349，目录 gaps/exclusions 均为空。计划共 5,573 家、44,584 个逻辑目标，估算新增请求 5,573、复用 39,011，未知项包含账号配额、接口权限和耗时。这里的证券类型分布由计划对冻结的源 metadata 分类得出；快照的原始公司行未回写 `instrument_type`。

owner 确认后执行新批次 `7ff4e499c8d145959872e13331217fcb`，隔离仓为 `output/.live_req016_acceptance/preview-store`，日志为 `output/.live_req016_acceptance/expanded-run.log`。首轮受限网络遇到连接错误并暂停；日志没有 DNS/name-resolution 证据，因此不将其进一步归因为 DNS 故障。后续在获批网络环境中以相同计划、store 和 batch ID 续跑。最终持久状态为 `done`，创建于 2026-10-13 23:35:43、结束于 2026-10-14 00:49:19（北京时间）；最后一次 resume 会话退出码 **0**。各次暂停/恢复日志都保留，最终退出码不以早期中断码替代。

可复现最终恢复命令（续跑既有已确认批次，不创建新批次）：

```bash
.venv/bin/python scripts/market_acquisition.py resume \
  --store output/.live_req016_acceptance/preview-store \
  --plan output/.live_req016_acceptance/plan-expanded-review.json \
  --batch-id 7ff4e499c8d145959872e13331217fcb --yes \
  >> output/.live_req016_acceptance/expanded-run.log 2>&1
```

独立验收者以 SQLite `mode=ro`、`PRAGMA query_only=ON` 重算计划 digest、计划与批次目标键集合，并运行正式 `explain_batch`/`serves_target`。44,584 个目标键与计划逐项相等且唯一，`batch_target` 无遗漏或额外键；计划和批次均绑定上述快照与证券类型计数。正式读取路径复核为 `done`、44,584/44,584 有效覆盖。

| 数据集 | A 股普通股目标 | 普通股 ok | 普通股 empty | CDR 目标 | CDR ok | CDR empty | 合计目标 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `stock_basic` | 5,572 | 5,572 | 0 | 1 | 1 | 0 | 5,573 |
| `daily` | 5,572 | 5,572 | 0 | 1 | 1 | 0 | 5,573 |
| `income`（报告类型 1/6） | 11,144 | 11,073 | 71 | 2 | 1 | 1 | 11,146 |
| `balancesheet`（报告类型 1/6） | 11,144 | 11,077 | 67 | 2 | 1 | 1 | 11,146 |
| `cashflow` | 5,572 | 5,570 | 2 | 1 | 1 | 0 | 5,573 |
| `fina_indicator` | 5,572 | 5,571 | 1 | 1 | 1 | 0 | 5,573 |
| **合计** | **44,576** | **44,435** | **141** | **8** | **6** | **2** | **44,584** |

全批次 `ok=44,441`、合法 `empty=143`；`no_permission`、`rate_limited`、`error`、`stale`、`unattempted` 均为 0，gaps 为空。CDR 的 8 个目标全部完成，其中 6 个 `ok`、2 个财报目标为合法空响应。普通股有 141 个合法空响应。空响应表示来源对该项无行，不推断证券缺失或权限失败。

请求/复用按各自口径记录：批次 SQLite usage `actual_requests=634`、`archive_hits=44,584`、`current_batch_cache=5,573`，因此正式解释器统计历史存档复用 `39,011` 个目标、本批预取产生的新目标 `5,573` 个；目标级 `new_requests=4` 是另一计数器，不与网络调用总数相加。日线预取子报告记录实际请求 609 次、预取目标 5,573、历史命中 39,011、回退 0、失败列表为空，数据源接口为 `daily`。首轮受限网络连接错误及恢复期间的多个暂停记录包含在完整运行历史；日志无法把每个早期失败请求映射到唯一 ticker/target_key，因此不宣称某个指定失败目标的单独恢复，只报告独立复核确认全部计划目标最终经正式存储服务检查为 `ok` 或合法 `empty`。

此新批次没有重试 VIP 整期财报接口（预取接口只有 `daily`）；其财报目标来自已存在记录复用，批次 `no_permission=0`。原批次先前观察到的 VIP 权限拒绝仍如上节记录，不能与本批次请求数混算。实际成功财报记录仍有来源字段缺列：`income.dt_eps` 11,074/11,074 缺失；`balancesheet.intang_assets` 11,078/11,078 缺失；`cashflow.c_pay_to_staff` 和 `receiv_tax_refund` 各 5,571/5,571 缺失；`fina_indicator` 的 `npl_ratio`、`prov_cov_ratio`、`cap_adequacy_ratio`、`core_cap_adequacy_ratio`、`net_int_margin`、`cost_income_ratio` 各 5,572/5,572 缺失。`ok` 不等于请求字段全部有值。

### 字段覆盖限制

独立终态核对确认来源字段缺列，不能解释为业务字段已采齐。原始快照纳入 689009.SH（九号公司）并计入其 8 个目标；独立核对原始公告及交易所代码资料后确认它是 CDR。2026-10-13 owner 决定允许目录包含并单独展示境内上市 CDR；批次和快照保持原样，修复后的分类不回写到固定快照或目标集合。

## 可复现命令与环境

仓库：macOS 本机，Python 3.12.13，项目 `.venv`；真实请求使用本机配置的数据源 token，token 只从既有 `.env`/环境读取，未写入日志或产物。采集与 SQLite 存档都位于 `output/.live_req016_delivery/`，未写入 `~/turtle_archive` 或用户 HOME 的既有产物。

生成固定离线计划的命令：

```bash
.venv/bin/python scripts/market_acquisition.py plan \
  --store output/.live_req016_delivery/archive \
  --plan output/.live_req016_delivery/plan-frugal.json \
  --scope all_a --tier frugal --periods 20260630
```

该计划于 2026-10-12 23:17（北京时间）绑定快照 ID `a107a05c417d4762bb7145d74260f442`，digest 为 `313446ddaa61c84e3e58e82653b0978a4eee448434bdca1bd9da4a2583419b17`。用户随后明确确认本批次联网执行。恢复同一批次的最终命令为：

```bash
.venv/bin/python scripts/market_acquisition.py resume \
  --store output/.live_req016_delivery/archive \
  --plan output/.live_req016_delivery/plan-frugal.json \
  --batch-id 3acb1a5d48544d6ba3703e68a7004313 --yes \
  > output/.live_req016_delivery/resume-final.log 2>&1
```

最终命令退出码 **0**；真实输出日志为 `output/.live_req016_delivery/resume-final.log`。批次于 2026-10-12 23:24:47（北京时间）创建；曾在安全暂停后用同一 ID 与固定计划续跑，最终状态、结束时间和目标数均直接从 SQLite 只读核对。恢复命令进程开始时刻未单独记入日志，不推算精确总耗时。

结束日志的关键输出：

```text
{"batch_id": "3acb1a5d48544d6ba3703e68a7004313", "status": "done", "total": 44584,
 "actual_requests": 34324, "reused": 5440,
 "counts": {"ok": 44441, "empty": 143}}
```

SQLite `batch_target` 共 44,584 行、5,573 个公司代码；每个目标均为 `ok` 或 `empty`。`explain_batch` 按正式读取路径验证后仍为 `done`，结果计数为 `ok=44,441`、`empty=143`，无 stale 或未完成目标。

## 已登记的运行发现

计划时无法预先得知 VIP 权限，真实预取确认当前账号没有六个整期 VIP 接口权限。这六个权限拒绝已计入真实请求数，并依照原范围回退逐公司标准接口；最终所有 44,584 个逻辑目标均有合法终态。标准双代码接口诊断也不支持安全地把一次空响应解释为两个公司结果，因此未用于采集。相关处理与两次额外探针已登记在 REQ-016 T9。
