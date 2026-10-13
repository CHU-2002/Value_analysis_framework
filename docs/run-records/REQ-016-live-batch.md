# REQ-016 全 A 股真实采集记录 · 2026-10-13

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

全市场金融预取对 VIP 整期接口发起 6 次调用，分别为 `income` 两种报告类型、`balancesheet` 两种报告类型、`cashflow` 和 `fina_indicator`；均实际返回 `no_permission`。实现没有将这些权限错误记作公司空数据，而是按原计划回退至已授权的逐公司标准接口。最终公司目标 `no_permission=0`。最终还没有 `rate_limited`、`error`、stale 或 uncompleted 目标；运行期间的等待限频状态是请求间隔控制，未形成限频失败。

批次外的诊断请求另计：一次 `income_vip` 权限探针返回 `no_permission`；一次把两家公司代码放进标准 `income` 请求的探针返回 0 行，未当作两家公司空结果，也未当作有效的多公司接口。两次合计 2 个诊断请求，不包含在 34,324 次批次请求内。

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
