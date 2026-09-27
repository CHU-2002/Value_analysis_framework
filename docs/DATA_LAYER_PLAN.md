# 数据获取与存储层设计（REQ-011）

> 本文写**怎么做**。要什么、做到什么程度以
> [`docs/requirements/REQ-011-unified-data-acquisition.md`](requirements/REQ-011-unified-data-acquisition.md) 为准；
> 两者不互相复制。开发流程见 [`docs/DEVELOPMENT.md`](DEVELOPMENT.md)，测试策略见 [`docs/TESTING.md`](TESTING.md)，
> 控制台侧的设计见 [`docs/GUI_CONSOLE_PLAN.md`](GUI_CONSOLE_PLAN.md) 与 `docs/CONSOLE_V2_PLAN.md`（REQ-012）。
> 产品侧的走查证据见 [`docs/proposals/2026-09-28-console-pm-review.md`](proposals/2026-09-28-console-pm-review.md)。

**阅读顺序**：§1 一句话方案 → §2 原则 → §3 架构 → §4 存储模型 → §5 标的宇宙 → §6 取数门面 →
§7 拉取编排 → §8 离线重建 → §9 迁移 → §10 兼容与过渡 → §11 CLI 与接口 → §12 目录布局 →
§13 安全 → §14 测试与预算 → §15 交付顺序 → §16 反模式 → §17 演进路线 → §18 开放问题。

---

## 1. 一句话方案

**把「远程响应」与「给人看的 Markdown」彻底分开：所有远程调用经过一个取数门面写进一个
SQLite 原始仓；`data_pack_market.md` 降级为「由仓离线生成的产物」，它的小节格式一个字不改。**

- 门面放在**唯一的收口点** `TushareClient._safe_call`（全部 20+ 处取数都从这里过），
  不靠每个调用点自觉——与 `core/security.py::safe_join`、前端 `api()` 是同一个教训。
- 数据集清单**从代码扫出来**（AST 扫 `_safe_call`），不手抄第二份——与
  `plugins/commands.py::scan_cli_params` 同理。
- 仓是**资产**：不过期、不主动删、可整体拷走；「新鲜度」是**策略**，不再用 TTL 表达。

## 2. 设计原则

| # | 原则 | 为什么（不这么做会怎样） |
|---|------|--------------------------|
| D1 | **一个收口点** | 远程访问只允许经过 `DataAccess`。散在各处的 `self.pro.*` / `self._safe_call` 只要漏一处，离线重建就会缺数据，而缺数据是**静默**的（章节变「数据缺失」） |
| D2 | **原始与呈现分离** | 原始响应是资产、Markdown 是报表。混在一起就会「改标题 = 改数据契约」（现状：`results/prepare.py`、`value_analysis_engine.py`、GUI `charts.py` 都在解析 Markdown 小节） |
| D3 | **清单从代码推导，不手抄** | 手抄的接口清单必然漂移。AST 扫 `_safe_call` 得到接口集合，新增取数代码会被测试立刻发现「没进拉取范围」 |
| D4 | **新鲜度是策略，存储不过期** | 现状 `output/.collector_cache/` 7 天 TTL：过期只是变慢；但一旦把「过期」和「重新花钱」混为一谈，用户就会在没意识到的情况下烧配额。存储层永不过期，何时重拉由显式参数决定 |
| D5 | **幂等** | 拉取、迁移、重建都必须可以重复执行且结果一致。断点续跑与「只补缺口」都建立在幂等上 |
| D6 | **零新增依赖** | `sqlite3` 属标准库；`pyarrow` 已在 `requirements.txt`（被 `_cached_us_daily` 使用），但**不作为本需求的必需品**——仓的规范形态是可读 JSON 行 |
| D7 | **不回退既有已验收语义** | `REQ-009.4` 的 `AC-4.1`~`AC-4.8` 已 `verified`。升级只能**加**能力，不能改掉「手动触发」「不主动删存档」「token 不入盘」这些承诺（§10 逐条对照） |
| D8 | **兼容期双写** | GUI 的采集面板今天读 `manifest.jsonl` + `batches/*.json`。仓换载体后这些文件**继续写**，直到 `REQ-012.4` 切到仓为止 |

## 3. 分层架构

```
┌────────────────────────────────────────────────────────────────────────────┐
│ 入口层（CLI / GUI）                                                         │
│   scripts/datalayer/cli.py     make data-pull / data-rebuild / data-gaps …   │
│   scripts/webui/__main__.py --collect   ← 兼容别名，语义不变（§10）          │
└───────────────────────────┬────────────────────────────────────────────────┘
                            │
┌───────────────────────────┴────────────────────────────────────────────────┐
│ 编排层 pull.py（目标枚举 / 预估 / 确认 / 进度 / 断点续跑 / 只补缺口）        │
│   universe.py  标的宇宙（ticker × 启用 × 档位）                              │
│   endpoints.py AST 扫 _safe_call → 接口清单 + 每接口的期次需求               │
│   registry.py  数据集注册表（shape / 期次语义 / 累计语义 / 档位归属）        │
│   （期次口径**不在这里**：统一用既有 scripts/periods.py，见 §4.4）           │
└───────────────────────────┬────────────────────────────────────────────────┘
                            │
┌───────────────────────────┴────────────────────────────────────────────────┐
│ 取数门面 access.py::DataAccess.call(dataset, **params)                      │
│   mode=online  : 命中仓 → 返回；未命中 → 远程拉 → 写仓 → 返回                │
│   mode=offline : 只读仓；未命中 → DataMissing（记缺口，不联网）               │
│   DataFrame ⇄ 记录体 的往返由 columns_json 的 dtype 契约保证（§6.3）          │
└───────┬────────────────────────────────────────────┬───────────────────────┘
        │ 写                                            │ 读
┌───────┴────────────────────────────────────────────┴───────────────────────┐
│ store.py   SQLite 单文件（唯一键 + 原子事务 + 索引）                          │
│            + manifest.jsonl（append-only，人读与审计；兼容期双写）             │
└────────────────────────────────────────────────────────────────────────────┘
        ▲
        │ 一次性导入（幂等）
┌───────┴────────────────────────────────────────────────────────────────────┐
│ legacy.py   ~/turtle_archive/**（REQ-009.4 的原始存档）                      │
│             output/.collector_cache/**（7 天 TTL 的旧缓存）                   │
└────────────────────────────────────────────────────────────────────────────┘
        ▲
        │ 重建（不联网）
┌───────┴────────────────────────────────────────────────────────────────────┐
│ rebuild.py  仓 → DataFrame → **既有** assembly 方法（格式代码不改）           │
│             → output/<公司>/data_pack_market.md（契约不变）                   │
└────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 在 `ARCHITECTURE.md` 分层里的位置

本设计**不开新的层**，只把「计算层」里今天混在一起的两件事拆开：

| `ARCHITECTURE.md` 的层 | 今天的实际情况 | 本设计之后 |
|------------------------|----------------|------------|
| 接口层 `.claude/commands/*`、`.opencode/commands/*` | 工作流描述（含 LLM 步骤） | **不变** |
| 策略层 `strategies/` | prompt + 参考 | 不变 |
| 共享定性层 `shared/qualitative/` | 模块提示词与模板 | 不变 |
| **计算层 `scripts/`** | **取数（联网）与装配（Markdown）耦合在 `tushare_collector.py` 里** | 拆成 `scripts/datalayer/`（取数与原始仓）+ 原地的装配（`tushare_modules/assembly.py` 不改） |
| 迭代层 `version.py` / `runs.py` / `analysis_status.py` | 指纹、run-store | 不变；`analysis_status` 的「数据是否过期」判据增加一个仓的来源（见 §8.3） |
| 结果管线 `scripts/results/` | 确定性脚手架 | 不变（`prepare` 的输入仍是 `data_pack_market.md`，见 `AC-5`） |

一句话：**`datalayer` 不是新的一层，是计算层里「买数据」那半边的正式化。**

**文档债（登记在此，实现时一并处理）**：`ARCHITECTURE.md` 的「分层结构」里**完全没有 GUI 层**
（`REQ-009` 交付了整个 `scripts/webui/` 却没有回填架构文档），现在又多了数据层。
`REQ-011.3` / `REQ-012.4` 收口时必须补一次架构文档更新（两条需求的「追溯 · 文档更新」都已列
`docs/ARCHITECTURE.md`）。

### 3.2 边界与命名：`datalayer` 与 `webui/datastore` 不是一回事

这两个名字都像「数据层」，很容易被后来者搞混，所以把边界写死：

| | `scripts/datalayer/`（本需求新增） | `scripts/webui/datastore/`（`REQ-009.3` 已有） |
|---|---|---|
| 管什么 | **买回来的**远程原始响应 | **算出来的**派生数据（图表序列、产物索引、时间线） |
| 过期 | **永不过期**（资产） | 源文件/解析器版本一变就失效 |
| 位置 | `~/turtle_archive/store.db`（仓库外） | `output/.webui_cache/`（仓库内、gitignore） |
| 谁能写 | 只有取数（`--pull`） | 任何人（派生，随时可重算） |
| 谁读 | 重建器、GUI 数据页、将来的选股 | GUI 面板 |
| 判据 | 「删了要重新花钱」 | 「删了只是变慢」 |

界线的判据只有一句（`REQ-009` 概览文档 §2.2 的落地）：**要重新花钱的进 `datalayer`，能重算的进 `webui/datastore`。**

### 3.3 导入与执行约定（贴合仓库现状，不改既有习惯）

| 事项 | 现状 | 本设计的做法 |
|------|------|--------------|
| `scripts/` 下的脚本如何 import | `tushare_collector.py` 用**扁平导入**（`from config import get_token`），依赖「直接执行脚本时 `scripts/` 在 `sys.path` 上」 | `datalayer/access.py` 需要 `from tushare_collector import TushareClient` 时，沿用 `webui/archive/adapters/tushare.py` 已有做法：临时把 `scripts/` 插入 `sys.path`、用完移除 |
| 包内相对导入 | `scripts/results/`、`scripts/tushare_modules/`、`scripts/webui/` 都是包内相对导入 | `datalayer` 同样只用包内相对导入；对外入口是 `python -m scripts.datalayer` |
| 测试如何 import | `tests/conftest.py` 把 `scripts/` 插进 `sys.path`；测试文件既可直接 import 扁平模块 | 新测试直接 `import datalayer...`（同 `results`/`webui` 的做法） |
| 测试分层 | `tests/conftest.py` 的 `TEST_LAYERS` 只登记**更重的层**，未登记的一律 `unit` | 三个新测试文件**默认就是 `unit`，无需登记**（与 `REQ-009` 的 4 个文件一致）；若某条实跑走查要进 `e2e`，再登记 |
| Makefile | `gui` / `gui-check` / `gui-collect` 一组 | 新增 `data-*` 一组，与既有并列；`gui-collect` 保留（§10.3） |
| 配置 | `scripts/webui/config.py` 的 `Config`（含 `archive_root`） | `datalayer` 有自己的配置（仓根、期次范围），**不复用 webui 的 `Config`**（数据层不该依赖 GUI）；`webui` 侧通过参数把 `archive_root` 传进来 |

## 4. 存储模型

### 4.1 为什么是 SQLite + JSONL 清单

| 诉求 | SQLite 怎么满足 |
|------|-----------------|
| 按 (标的, 数据集, 期次, 参数) 去重 | `UNIQUE` 约束，不是「先查后写」的竞态 |
| 原子（中断不留半条） | 单条写入是一个事务；`PRAGMA journal_mode=WAL` 下崩溃可恢复 |
| 可整体拷走 | 一个 `.db` 文件 + 一个 `manifest.jsonl`；`sqlite3 <db> ".backup …"` 亦可用 |
| 跨公司聚合（将来的选股/组合/对比） | `INDEX (dataset, ticker, period_type)` 直接查 |
| 人可读 / 可审计 | `manifest.jsonl` append-only 逐条留痕 + `--export` 导出可读 JSON |
| 不过期 | 没有 TTL 字段；删除只有显式动作 |

被否的两条见 `REQ-011` 的「## 备注 · 存储结构决策」。

### 4.2 表结构

```sql
PRAGMA journal_mode = WAL;      -- 崩溃可恢复；单写多读
PRAGMA foreign_keys = ON;

-- 原始记录：一次远程响应的全部内容与元信息（AC-3）
CREATE TABLE raw_record (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker            TEXT    NOT NULL,
  dataset           TEXT    NOT NULL,   -- Tushare 接口名，如 income / daily / stock_basic
  period            TEXT    NOT NULL,   -- '20260630' / 'latest'（时点型与序列型用 'latest'）
  param_key         TEXT    NOT NULL,   -- sha256(规范化参数)，见 §4.3
  params_json       TEXT    NOT NULL,   -- 原始入参（已剔除 token 等凭据）
  shape             TEXT    NOT NULL,   -- period_report | timeseries | snapshot
  period_type       TEXT    NOT NULL,   -- annual | half | quarter | point | series
  cumulative        INTEGER NOT NULL,   -- 1=累计口径, 0=单期/时点（AC-4）
  result            TEXT    NOT NULL,   -- ok | empty | no_permission | rate_limited | error
  error_excerpt     TEXT,               -- 接口原文摘要（已脱敏，≤500 字符）
  columns_json      TEXT    NOT NULL,   -- [{"name":..,"dtype":..}, ...]（§6.3 的往返契约）
  rows_json         TEXT    NOT NULL,   -- 规范 JSON 记录体（ensure_ascii=False, sort_keys）
  content_sha256    TEXT    NOT NULL,   -- rows_json 的 sha256
  bytes             INTEGER NOT NULL,
  fetched_at        TEXT    NOT NULL,   -- UTC ISO8601
  token_fingerprint TEXT    NOT NULL,   -- sha256 前 8 位（AC-3；不存 token 本身）
  tier_label        TEXT,
  quota_profile     TEXT,
  framework_version TEXT    NOT NULL,
  batch_id          TEXT,
  UNIQUE (ticker, dataset, period, param_key)
);
CREATE INDEX raw_record_lookup ON raw_record (dataset, ticker, period_type);
CREATE INDEX raw_record_result ON raw_record (result);
CREATE INDEX raw_record_gap    ON raw_record (result) WHERE result NOT IN ('ok', 'empty');

-- 标的宇宙（AC-1）
CREATE TABLE universe (
  ticker       TEXT PRIMARY KEY,
  display_name TEXT    NOT NULL,
  market       TEXT    NOT NULL,          -- SH | SZ | HK | US
  enabled      INTEGER NOT NULL DEFAULT 1,
  tier         TEXT,                      -- 所需数据档位（frugal | bulk | 自定义档案名）
  note         TEXT,
  updated_at   TEXT    NOT NULL
);

-- 批次（进度与配额；字段与既有 batches/*.json 对齐，见 §10）
CREATE TABLE batch (
  batch_id     TEXT PRIMARY KEY,
  profile      TEXT NOT NULL,
  status       TEXT NOT NULL,             -- pending|running|paused|done|partial|failed
  created_at   TEXT NOT NULL,
  heartbeat_at TEXT,
  owner_pid    INTEGER,
  estimate     INTEGER NOT NULL,
  targets_json  TEXT NOT NULL,
  progress_json TEXT NOT NULL,
  usage_json    TEXT NOT NULL,
  summary_json  TEXT
);
CREATE TABLE batch_target (
  batch_id      TEXT NOT NULL,
  target_key    TEXT NOT NULL,
  ticker        TEXT, dataset TEXT, period TEXT,
  result        TEXT, error_excerpt TEXT, updated_at TEXT,
  PRIMARY KEY (batch_id, target_key),
  FOREIGN KEY (batch_id) REFERENCES batch(batch_id) ON DELETE CASCADE
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- schema_version / 迁移记录 / 仓根指纹
```

### 4.3 唯一键与参数指纹

`param_key = sha256(json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":")))[:16]`，
在**剔除凭据类键**（`token` / `secret` / `password` / `api_key`…）之后计算。
与既有 `archive/batch.py::_target_key` 的算法保持一致（同一套目标 → 同一个 key，
迁移与续跑才能对齐）。`params_json` 存剔除后的入参，故 `param_key` 可由 `params_json` 复算——
由一条测试断言（防止有人改了归一化方式而旧记录再也命中不了）。

### 4.4 期次口径（AC-4）

#### 4.4.1 先解决「仓库里已经有三套期次写法」这件事

这是本设计**必须嵌进既有架构、而不是另起一套**的地方。现状：

| 写法 | 例子 | 出处 |
|------|------|------|
| **接口期次**（Tushare 的 `period` 入参、`end_date` 输出） | `20260630`、`latest` | `webui/archive/store.py` 的 `manifest.jsonl`、`tushare_modules/*` 的 `_safe_call(period=…)` |
| **项目期次**（全项目统一的期次标识） | `2026H1`、`2026Q1`、`2025FY` | `scripts/periods.py`（`ARCHITECTURE.md` §数据流 明确「期次标识由 `scripts/periods.py` 统一定义」） |
| **数据包表头**（给人看的列名） | `2026H1`、`2025Q1`、**`2025`**（年报写成裸年份） | `data_pack_market.md` §3/§12 的列；转换函数是 `tushare_modules/assembly.py::_yoy_period_label`（今天是私有函数） |

**本设计不新增第四套。** 做法：

1. 仓里的 `period` 字段用**接口期次**（`20260630` / `latest`）——因为它就是唯一键与幂等去重的
   自然粒度，也是既有 `manifest.jsonl` 已经在用的写法（迁移零换算）。
2. 展示/查询用的期次用**项目期次**（`periods.py` 的词汇），由**既有模块**换算：
   `scripts/periods.py` 提供 `parse_period` / `comparable_period` / `previous_period` /
   `single_quarter_base` / `sort_periods` / `list_periods_between`（含「Q1/H1/Q3 是年内累计、
   单季由累计相减」这条既有语义）。
3. **把 `assembly.py::_yoy_period_label` 提升为 `scripts/periods.py` 的公开函数**
   （`end_date_to_label("20260630") → "2026H1"`、`"20121231" → "2012"`），`assembly.py` 改为调用它。
   这是**一次小重构，不是新模块**：`periods.py` 已经是权威，`assembly.py` 里那份是实现细节泄漏。
   顺手补上它今天缺的 `end_date → 项目期次`（`20260630 → 2026H1`）映射，因为仓要写 `period_type`。
4. 断言（`test_data_store.py`）：`datalayer` 里**不得**出现自带的期次正则或月份映射；
   所有 `20260630 ↔ 2026H1` 的换算都经由 `scripts/periods.py`（扫描导入 + 一条换算表驱动的用例）。

#### 4.4.2 三个字段

| 字段 | 取值 | 来源 |
|------|------|------|
| `shape` | `period_report`（利润表/资产负债表/现金流量表/财务指标…）、`timeseries`（`daily`/`weekly`/`daily_basic`）、`snapshot`（`stock_basic`） | `registry.py` 按接口声明 |
| `period_type` | `annual`（`1231`）/ `half`（`0630`）/ `quarter`（`0331`/`0930`）/ `point`（时点）/ `series`（时间序列） | **由 `scripts/periods.py` 的换算结果推导**（不自己判 `endswith("1231")`） |
| `cumulative` | `1` 累计（利润表/现金流量表：`Q1`/`H1`/`Q3` 为年内累计）、`0` 单期或时点（资产负债表） | `registry.py` 按接口声明，语义与 `periods.py` 文档串一致 |

**这条直接修掉 `REQ-012` 的 `AC-7`（图表混口径）**：GUI 不再靠列名猜口径，
而是查 `period_type`；「年度」与「单季」是两组序列，不是一条折线上的相邻两点。

> 已知边界：Tushare 不同接口对「累计」的定义不完全一致，`registry.py` 的声明是**人工判断 + 测试锁定**的，
> 新增接口必须显式声明（`test_data_store.py` 断言「scanned 接口都有 registry 声明」，漏声明即失败）。
> `results/change_report.py` 里已经有同一条口径说明（`cumulative_vs_single_quarter`），
> 本设计不改它，只把同一条语义落到结构化字段上。

### 4.5 可读性补偿

- `manifest.jsonl`：每次写入 append 一行（与现状字段兼容），人可直接读、`grep`、`diff`；
- `make data-export [--ticker ...]`：把仓导出成一棵可读 JSON 树（与 `REQ-009.4` 的旧布局一致），
  用于代码评审、跨机对比与「拷走给别的工具用」；
- `meta.schema_version` 与迁移记录写在 `meta` 表，`--check` 能打印仓的规模与各结果计数。

### 4.6 仓放哪儿

**默认仍然是仓库之外的 `~/turtle_archive/`**（沿用 `REQ-009.4` 的资产语义），布局变为：

```
~/turtle_archive/
├── store.db                 ← 新增：唯一结构化仓
├── store.db-wal / -shm      ← WAL 副产物（可安全删除，见 §16）
├── manifest.jsonl           ← 保留：append-only 审计清单
├── batches/*.json           ← 兼容期继续写（§10）
└── <TICKER>/<dataset>/*.json← 迁移后只读；不再新写（§9.4）
```

理由：① 资产语义（仓库外、不被 `git clean` 波及、自己拷一份就是备份）；② 与既有存档同目录，
迁移就是「同目录内建索引」，不搬字节。

代价（**必须显式处理**）：`REQ-009.3` 的 `AC-3.7` 路径 jail 只允许 `output/` 子树。
GUI 要读仓，就得**新增一个显式允许根**（`archive_root`），而不是放宽 jail 的判定。
这条写进 `REQ-012.4` 的前置；`REQ-011` 只提供 CLI，不改 GUI 的 jail。

## 5. 标的宇宙（AC-1）

### 5.1 清单来源

`universe` 表是唯一事实来源，CLI 维护：

```
make data-universe ARGS='add --ticker 600887.SH --name 伊利股份 --market SH'
make data-universe ARGS='list'
make data-universe ARGS='disable --ticker 000858.SZ'
```

**兼容**：首次运行时若 `universe` 为空而 `output/` 下已有公司目录，提供
`make data-universe ARGS='import-output'` 从既有目录**建议**一份清单（`display_name` 取自目录名，
`market` 取自 `record.json` 的 `subject.ticker` 后缀），但**不自动写入**——
清单是用户的资产，自动生成会掩盖「哪些是真在维护的」这个判断。

### 5.2 目标枚举：接口清单从代码扫出来（D3）

```
universe（启用的标的 × 档位）
        ×
endpoints.py::scan_safe_calls()      ← AST 扫 scripts/**/*.py 里的 _safe_call("xxx", …)
        ×
期次范围（按接口的 shape 与 registry 声明的期次需求）
        =
目标集合
```

- `scan_safe_calls()` 用 `ast` 解析（不是正则），返回 `{接口名: [参数键集合]}`；
  它同时记录**调用点的文件与行号**，便于「这个接口是谁要的」排查。
- 期次范围：`period_report` 型接口按「最近 N 年 / 显式期次列表」展开；`timeseries` 与 `snapshot`
  只用 `latest`（真·全量时间序列由接口自身的日期范围决定，由 `params_json` 记录）。
- **一致性断言**（写进 `test_data_pull.py`）：`PROFILES['bulk'] ⊇ scan_safe_calls().keys()`。
  于是「新写一处 `_safe_call` 却忘了让它进拉取范围」会在 CI 红，而不是等到某天离线重建时
  才发现某个小节永远是「数据缺失」。
- `PROFILES`（`frugal` / `bulk`）的语义从「唯一的接口清单」降级为「**档位选择器**」：
  `frugal` = 低配额必需子集，`bulk` = 扫描得到的全集。自定义档位 = `universe.tier` 指向的一份接口列表。

### 5.3 调用量预估（AC-2）

`estimate = |目标集合|`，并按 `(档位, 数据集)` 分组打印：

```
调用量预估：1,284 次请求（bulk）
  stock_basic      12
  daily            12
  income          144   （12 标的 × 12 期次）
  balancesheet    144
  …
```

预估是**纯计算**（不联网、不读仓内容，只读 universe 与 registry），
所以「先看预估再决定」这一步零成本——与 `REQ-009.4` 的 `AC-4.2` 一致。

## 6. 取数门面（AC-3 / AC-5 的关键）

### 6.1 收口点

```python
# scripts/tushare_collector.py —— 唯一改动：一行转调
@rate_limit
def _safe_call(self, api_name: str, **kwargs) -> pd.DataFrame:
    return self._access.call(api_name, **kwargs)     # 原来的重试/限流代码搬进 DataAccess
```

`DataAccess` 承担原 `_safe_call` 的全部职责（VIP 路由、`MAX_RETRIES` 重试、连接错误重建客户端、
永久错误不重试），并**追加**仓的读写。三种模式：

| 模式 | 命中仓 | 未命中 | 用途 |
|------|--------|--------|------|
| `online`（默认） | 直接返回（记 `archive_hits`） | 联网拉 → 写仓 → 返回 | `--pull`、日常 `tushare_collector` |
| `refresh` | 忽略，联网重拉 → 覆盖写 | 同上 | `--force` / 显式「重新拉取」 |
| `offline` | 直接返回 | 抛 `DataMissing(dataset, params)`，**不联网** | `--rebuild`、GUI 浏览路径 |

`offline` 模式配一条测试：把 socket stub 成抛错，跑完整 `assemble_data_pack`，
断言「零 socket 调用」且产物结构与联网路径一致（`REQ-011` 的 `AC-5`）。

> **反模式守卫**：`test_data_store.py` 用 AST 断言 `self.pro.` 只出现在 `_new_pro_api` 与
> `DataAccess` 内部；任何新增的直连点都会红。

### 6.2 哪些东西**不**进仓

- 派生计算结果（`compute_derived_metrics` 的 §17 输出）——它是**算出来的**，进 `output/.webui_cache/`；
- 报告 HTML、图表序列、报告 Markdown——同上；
- 凭据——永不落盘（只有指纹）。

分界线一句话：**花钱买回来的进仓，能重算的进派生缓存**。
（这正是 `REQ-009` 概览文档 §2.2「三种过期策略」的落地。）

### 6.3 DataFrame 往返契约（本设计最大的技术风险）

仓里存 JSON 行，读出来要变回 DataFrame 给既有的 `get_*` 与 `compute_derived_metrics` 用。
dtype 漂移会让 markdown 表格与派生指标**静默**变化，所以往返必须是**契约**：

- 写入时记录 `columns_json`：`[{"name": "end_date", "dtype": "object"}, {"name": "revenue", "dtype": "float64"}]`；
- 读出时按 `columns_json` 调 `pd.read_json(..., dtype=...)`，并逐列核对 dtype；
- 断言：`assert_frame_equal(df, roundtrip(df))`，覆盖**每一种 shape 各一条真实样例**
  （`income` / `daily` / `stock_basic` / 空结果 / 含 NaN 的结果）。
- 空结果同样入仓（`result='empty'`，`rows_json='[]'`）：**空也是信息**——
  「这个接口确实返回空」与「还没拉过」必须能分开（`REQ-009.4` 的 `AC-4.5` 早就要求了这一点）。

若某接口的往返在实测中无法稳定（例如混类型列），**降级方案**：该接口的 `rows_json` 改存 parquet 字节
（`pyarrow` 已在依赖里），`columns_json` 记 `{"format": "parquet"}`。
方案切换要有实测证据，不预先采用。

## 7. 拉取编排（AC-2 / AC-6 / AC-7）

### 7.1 与 `REQ-009.4` 的关系

**升级不是重写**。`ArchiveBatch` 的状态机与语义（pending/running/paused/done/partial/failed、
`owner_pid` + lock 文件防并发、逐目标落盘、`KeyboardInterrupt` → `paused`）已经过独立验收，
本需求把它**搬进** `datalayer/pull.py` 并接上 `DataAccess`，行为逐条保留：

| 语义 | 现状实现 | 本需求 |
|------|----------|--------|
| 未配 token → `NO_TOKEN` 且零请求 | `ArchiveBatch.run` 首行 | 不变（`AC-4.1`） |
| 预估 + 必须显式确认（`--yes`） | `QuotaConfirmRequired` | 不变（`AC-4.2`） |
| 已有存档跳过、`--force` 重拉 | `store.has()` + `result in (ok, empty)` | 改成仓查询，语义不变（`AC-4.4`） |
| 断点续跑 | `completed` 逐目标落盘 | 改为 `batch_target` 表逐行落盘，语义不变 |
| 同批次不并发 | lock 文件 + `owner_pid` | 不变 |
| 四类计数报告 | `summary` / `usage` | 不变（`AC-4.4`） |
| 只补缺口 | `--only-gaps` + `gap_targets` | 不变（`AC-4.6`），另加 GUI 入口（属 `REQ-012.4`） |

### 7.2 目标级结果

每个目标的结果写入 `batch_target`，并把 `raw_record` 的 `result` 保持一致。
`classify_result`（`no_permission` / `rate_limited` / `empty` / `error` / `ok`）**继续复用**
既有实现——它是 `REQ-009.4` 的 `AC-4.5` 的判据，不允许在这里重写一套分类。

### 7.3 「拉全」与「刷新」是两件事

- `--pull`：把清单内**缺的**数据集补齐（幂等，重复执行只补缺口）；
- `--pull --force`：显式重拉（要给预估 + 确认）；
- `--rebuild`：不联网，从仓生成产物；
- 日常 `tushare_collector.py --code X`：`online` 模式（命中仓就不联网），
  行为对使用者不变，但**不再重复花钱**。

## 8. 离线重建（AC-5 / AC-7 的口径来源）

### 8.1 流程

```
store（raw_record: period_report / timeseries / snapshot）
   │  按 (ticker, dataset, period) 反序列化成 DataFrame
   ▼
DataAccess(mode=offline)
   │  既有 get_* 方法与 compute_derived_metrics **不改**
   ▼
assemble_data_pack(ts_code)   ← 格式代码一个字不改（这是契约不破的关键）
   ▼
output/<公司>/data_pack_market.md
```

**为什么不动格式代码**：`data_pack_market.md` 的小节与表头是 `REQ-002`/`REQ-006` 与下游脚本的输入契约。
重写装配会让「重建」与「联网产出」出现两份实现、必然漂移。所以重建 = 换数据源，不换模具。

### 8.2 缺口的表达

离线重建遇到仓里没有的目标时：

1. 该小节写成 `> ⚠️ 数据缺失：<接口> <期次>（本次重建时仓内无记录）`；
2. 该缺口进入 `warnings`，并在 `--rebuild` 结束时打印计数与建议命令
   （`make data-pull --only-gaps`）；
3. **不静默**：产物末尾的完备度行（`共 N/M 个数据板块成功获取`）按仓的实际情况计算。

> 这里顺手修掉 `REQ-009.4` 记录的现状盲区：现在的完备度把「无权限」当成功
> （实跑证据：`yc_cb` 连续 5 次无权限而末尾仍写 `14/14`）。重建路径改用
> `classify_result` 的结果分类统计，`no_permission` 不计入「成功」。

### 8.3 新鲜度

`--rebuild` 的产物头部的「生成时间」写重建时刻；同时写一行「数据截至」——
取该标的在仓内**业务数据集**的 `max(fetched_at)` 与 `max(period)`。
这样「报表什么时候生成的」与「数据是什么时候买的」不再混为一谈
（现状 `_check_staleness` 解析的是 Markdown 里的生成时间，属 §16 的反模式）。

## 9. 迁移（AC-8）

### 9.1 从 `~/turtle_archive/**` 导入

遍历既有 `manifest.jsonl`（每条一行，含 `file` 相对路径与元信息），逐条：
读 `data` 文件 → 建 `raw_record`（`period_type` / `cumulative` 由 `periods.py` 与 `registry.py` 补齐，
其余元信息按原值）→ 已存在则跳过（按唯一键）。

### 9.2 从 `output/.collector_cache/**` 导入

两类文件：

- `stock_basic_<code>.json`（`to_json(orient='records')` 产出）→ 直接成为 `raw_record`；
- `us_daily_all.parquet`（全市场快照）→ 按 `ts_code` 拆分成多条，或整体作为 `dataset='us_daily'`、
  `period='latest'`、参数标注「全市场快照」的一条记录（**设计选择：整体存一条**，
  因为它本来就是一次调用、一次付费；拆分会让「一次调用 = 一条记录」的账目对不上）。

### 9.3 幂等与冲突

迁移报告：`导入 / 跳过（已存在） / 冲突（同键不同内容摘要）`。
冲突不覆盖、只报告（并写进 `meta` 的迁移记录），由使用者决定 `--force-import`。

### 9.4 旧路径停写

迁移完成后 `output/.collector_cache/` **停写**（`_cached_basic_call` / `_cached_us_daily` 改为走仓），
目录保留为只读；`~/turtle_archive/<TICKER>/` 目录同样停写、保留只读。
不做删除，也不自动清理（资产语义，`REQ-009.4` 的 `AC-4.3`）。

## 10. 兼容与过渡（D7 / D8）

### 10.1 `REQ-009.4` 的既有判据怎么不被回退

| `REQ-009.4` 的 AC | 本设计怎么保证 |
|-------------------|----------------|
| `AC-4.1` 手动触发边界 | 不引入任何 scheduler；`--pull` 是显式动作；测试继续断言源码里无 `threading.Timer`/`sched`/`crontab` |
| `AC-4.2` 批次与配额档案 | 同一状态机、同一预估语义；`PROFILES` 仍在 `frugal`/`bulk` 之上扩展 |
| `AC-4.3` 原始存档不过期、不主动删、仓库之外 | 仓仍在 `~/turtle_archive/`；无 TTL 字段；无删除命令（只提供 `--export`） |
| `AC-4.4` 去重与断点续跑 | §7.1 对照表逐条保留 |
| `AC-4.5` 权限/积分缺口可识别 | `classify_result` 原样复用；重建路径的完备度也改用它（顺手修掉盲区） |
| `AC-4.6` 只补缺口 | `--only-gaps` 保留（CLI + 面板入口） |
| `AC-4.7` token 安全 | 只存指纹与档位标签；`params_json` 剔除凭据；`error_excerpt` 先脱敏 |
| `AC-4.8` 实跑 | 本需求的 `AC-9` 实跑记录包含它（同一次真实运行） |

### 10.2 兼容期双写

过渡期内 `store.py` 在写仓的同时**继续**产出：

- `manifest.jsonl`（append 一行，字段与现状一致）；
- `batches/<batch_id>.json`（批次快照，字段与现状一致）。

于是 GUI 的 `plugins/collect.py` 三个面板**不需要同时改**（它们的读路径不变），
`REQ-012.4` 再统一切到仓。切完后双写降级为「只写 manifest.jsonl」。

**触发条件写死**：`REQ-012.4` 合入之日，双写的 `batches/*.json` 部分在同一 PR 里去掉。

### 10.3 CLI 兼容

`make gui-collect ARGS='--profile frugal --ticker 600887.SH --period 20260630'` **必须继续可用**
（`REQ-009.4` 的实跑记录用的就是这条命令）。做法：`scripts/webui/__main__.py --collect` 变成
对 `datalayer.pull` 的**薄转调**，参数与退出码语义不变（`NO_TOKEN`→2、`KeyboardInterrupt`→130）。

## 11. CLI 与接口

```
scripts/datalayer/cli.py（python -m scripts.datalayer …）

  --universe list|add|disable|enable|import-output
  --pull [--profile frugal|bulk] [--ticker T]… [--periods 20251231,20260630] [--only-gaps] [--force] [--yes] [--batch-id ID]
  --rebuild [--ticker T]… [--out 路径]
  --gaps [--ticker T] [--dataset D] [--json]
  --import-legacy [--from 路径] [--dry-run] [--force-import]
  --export [--ticker T]… [--out 目录]
  --check                       # 打印 schema 版本、记录数、各 result 计数、仓根

退出码：0 成功 / 2 用法或前置错误（含 NO_TOKEN、未确认）/ 4 仓不可用 / 130 中断
```

Makefile 目标（与既有 `gui-*` 并列）：

```
make data-universe ARGS='…'
make data-pull     ARGS='--profile bulk --yes'
make data-rebuild  ARGS='--ticker 600887.SH'
make data-gaps
make data-import-legacy
```

**为什么不塞进 `scripts/webui`**：数据层要在没有 GUI 时可用（CI、定时人工脚本、将来的选股）。
`REQ-009.4` 把采集 CLI 放在 webui 里是当时的权宜，本需求把能力搬到正确的层，
同时保留 webui 侧的薄别名（§10.3）。

## 12. 目录与文件布局

```
scripts/
├── datalayer/                  ← 新增（REQ-011 的主体）
│   ├── __init__.py
│   ├── cli.py                  argparse 子命令（§11）
│   ├── store.py                SQLite 仓：schema / 事务 / 唯一键 / manifest 双写 / 导出
│   ├── access.py               DataAccess 门面（online / refresh / offline）+ 重试与限流
│   ├── dataframe_codec.py      DataFrame ⇄ columns_json+rows_json 往返（§6.3）
│   ├── universe.py             标的宇宙 CRUD 与 import-output
│   ├── registry.py             数据集声明（shape / 期次语义 / 累计语义 / 档位归属）
│   ├── endpoints.py            AST 扫 _safe_call → 接口清单与调用点
│   ├── pull.py                 目标枚举 + 编排（承接 REQ-009.4 的批次语义）
│   ├── rebuild.py              仓 → 既有 assembly → data_pack_market.md
│   ├── legacy.py               ~/turtle_archive 与 .collector_cache 的幂等导入
│   └── gaps.py                 缺口与完备度（复用既有 classify_result）
├── periods.py                  ← **不新建解析器**：期次口径的唯一权威（§4.4）；
│                                  本需求往里补 end_date ↔ 项目期次 的换算，
│                                  并把 assembly.py::_yoy_period_label 提升进来
├── tushare_collector.py        ← 只改一处：_safe_call 转调 DataAccess（§6.1）
└── webui/
    ├── __main__.py --collect   ← 薄别名（§10.3）
    └── archive/                ← 保留 GUI 面；其 store/batch/quota/gaps 逐步改为转调 datalayer
```

测试：

```
tests/test_data_store.py        仓 schema / 唯一键 / 原子写 / 往返契约 / 口径 / 迁移幂等
tests/test_data_pull.py         目标枚举一致性 / 预估 / 确认 / 续跑 / force / only-gaps / 并发拒绝
tests/test_offline_rebuild.py   禁网重建 / 契约不变 / 缺口记录 / 完备度不再把无权限当成功
```

## 13. 安全与凭据

| 面 | 做法 |
|----|------|
| token 来源 | 只从 `TUSHARE_TOKEN` / 项目 `.env`（沿用 `REQ-009.4` 的 `resolve_token`），CLI 不接收 `--token` |
| token 落盘 | 永不；只存 `token_fingerprint`（sha256 前 8 位）与用户填写的 `tier_label` |
| 错误原文 | `error_excerpt` 先过 `core/security.py::redact` 再入库（Tushare 的报错里可能回显 key） |
| 参数 | `params_json` 剔除 `token`/`secret`/`password`/`api_key` 类键 |
| 路径 | 仓根由 `--store` / `archive_root` 决定；写入前 `mkdir` 并确认可写，失败退出码 4 |
| 权限 | `.db` 文件 0600、目录 0700（`~/turtle_archive` 现状已如此）；不提供网络暴露面 |
| 离线保证 | `mode=offline` 下 `DataAccess` 直接拒绝联网（不是「忘了拉」），并有 socket-stub 测试 |

## 14. 测试与预算

### 14.1 门禁与预算现状

**2026-09-28 上调前后对照**（owner CHU-2002 批准，变更记录见 `REQ-006` 的 AC-7 与任务 **T9**）：

| 项 | 实测当前 | 上调前上限 | **上调后上限** | 余量 |
|----|----------|------------|----------------|------|
| 测试文件 | 38 | 48 | **52** | 14 |
| 用例（含 parametrize） | 1653 | 1800 | **2000** | **347** |

上调前 `REQ-011`（3 文件）+ `REQ-012`（4 文件）**共用只有 10 文件 / 147 条的余量**，
按 `REQ-009` 的实际消耗（4 文件 / 64 条）估算两条需求需要 140~180 条——**这就是上调的理由**。
使用者在被告知「先清理，再谈上调」的纪律与「现在就上调」的差别后，选择直接上调。

**纪律与欠账（必须一起读）**：

1. **本次没有先做用例清理**——这笔欠账已登记在
   `ledger.md` 的「待登记想法（Inbox）」，**要在下一次接近上限之前还掉**（合并重复用例、
   删掉只复述实现的测试），不得用「已经上调过两次」当作第三次上调的理由；
2. 判据逻辑没有放宽：仍是「超预算即失败」，只是阈值经批准调整；
3. 各片的用例预算仍按 §14.2 执行，**先按预算写**，不是「有 347 条余量就随便加」。

修改预算阈值的唯一入口是 `scripts/test_scope.py` 的 `MAX_TEST_FILES` / `MAX_COLLECTED_CASES`；
`docs/TEST_SCOPE.md`、`docs/TESTING.md` §5、`docs/DEVELOPMENT.md` §14 都只是转述。

### 14.2 各片用例预算（草案，登记时以 AC 定稿）

| 片 | 测试文件 | 预算（条） | 重点 |
|----|----------|-----------|------|
| `REQ-011.1` | `tests/test_data_store.py` | ≤ 30 | schema/唯一键/原子性/迁移幂等/口径解析/往返 dtype |
| `REQ-011.2` | `tests/test_data_pull.py` | ≤ 25 | 扫描清单 ⊇ 档位、预估确定值、确认拒绝、续跑、force、only-gaps、并发拒绝 |
| `REQ-011.3` | `tests/test_offline_rebuild.py` | ≤ 20 | 禁网零调用、小节契约、缺口记录、完备度口径修正 |

### 14.3 测试约定

- 全部 `tmp_path`，**不读真实 `output/`、不读真实 `~/turtle_archive`、不联网**；
- 有真实样例的必要性：往返契约（§6.3）与重建（§8）用**固化在 `tests/fixtures/` 的小样例**，
  不用真实数据包（避免把资产塞进仓库）；
- 新增测试文件按 `REQ-011.S` 声明归属（`# 覆盖需求：REQ-011.1`），并跑 `make scope-write`。

## 15. 交付顺序

```
REQ-011.1  ← 仓 + 清单 + 迁移（先把「落得下来、查得到、拷得走」立住）
   │        └ 关键测试：唯一键去重、原子写、往返 dtype、迁移幂等
   ▼
REQ-011.2  ← 扫描清单 + 一次拉全 + 续跑 + 补缺口
   │        └ 关键测试：PROFILES ⊇ scan_safe_calls、预估、确认、续跑
   ▼
REQ-011.3  ← 离线重建（换数据源不换模具）+ 口径结构化供 REQ-012.3 使用
            └ 关键测试：禁网零 socket、小节契约、完备度口径
```

每片一条分支 + 一个 PR；每片收口前跑一次**真实**小规模验证（不必用真实 token 的部分用
`--dry-run` 与 fixtures；用真实 token 的实跑只在 `REQ-011` 收口时做一次，见 `AC-9`）。

## 16. 反模式（明确写下来，防止以后走偏）

| 反模式 | 为什么不行 |
|--------|-----------|
| 把图表序列、报告 HTML 存进仓 | 它们是**算出来的**，源文件一变就该重算；进仓会让仓变成「又一份需要维护的派生数据」 |
| 给仓加 TTL / 自动清理 | 「过期」与「重新花钱」必须解耦（`REQ-009.4` 的 `AC-4.3`） |
| 绕过 `DataAccess` 直接 `self.pro.*` | 离线重建会静默缺数据；由 AST 断言守住 |
| 手抄接口清单 | 必然漂移；由 `endpoints.py` 扫描 + `PROFILES ⊇ scan` 断言守住 |
| 用 Markdown 当查询源 | 现状的根因（`_check_staleness` 解析生成时间、GUI 解析小节标题） |
| **再写一套期次解析**（`datalayer/periods.py`） | 仓库里已经有 `scripts/periods.py` 是权威（`ARCHITECTURE.md` 明写），再写一套就是第四套词汇；已有三套（接口期次 / 项目期次 / 数据包表头）够乱了 |
| **把 `datalayer` 的读路径接到 `webui/datastore`（或反过来）** | 一个管「买回来的」、一个管「算出来的」，混起来就再也说不清「删了会不会重新花钱」（§3.2） |
| 在拉取里顺手做业务计算 | 编排层只搬数据；算法留在既有模块 |
| 把 `store.db-wal` / `-shm` 当成需要备份的东西 | 它们是 WAL 副产物；备份 = `store.db` + `manifest.jsonl`（或 `sqlite3 .backup`） |
| 自动/定时拉取 | `REQ-009.4` 的 `AC-4.1` 手动边界不变 |
| 迁移时删除旧文件 | 资产不主动删；旧路径只停写、保留只读 |

## 17. 演进路线（写清触发条件，不提前做）

| 方向 | 触发条件 | 做法 |
|------|----------|------|
| 跨公司聚合 / 选股直接查仓 | 需要「全市场按 ROE 排序」这类查询时 | 直接在 `store.db` 上写 SQL / 视图，不再拉全量进内存 |
| 把 `scripts/screener_core.py` 的独立客户端并入 | 选股器要与数据包用**同一份**行情时（今天是第三套取数与缓存） | 让它的 `_safe_call` 也走 `DataAccess`（同一收口点原则） |
| 列存 / 大表优化 | 单表 > 数 GB 或查询变慢（实测触发） | 用已在依赖里的 `pyarrow` 存 parquet 列，`columns_json` 记 format |
| 仓的云端备份 | 使用者丢过机器或明确要求 | **另开 REQ**（含冲突解决策略） |
| GUI 直接读仓 | `REQ-012.4` | 新增 `archive_root` 允许根（不放宽 jail），面板改从仓取数 |

## 18. 开放问题

1. **期次范围谁定**：`AC-1` 要求清单可读可改，但「拉哪些期次」是放在清单里（每标的一套）
   还是全局配置（最近 N 年）？倾向：**全局默认 + 清单可覆盖**，因为多数标的的口径一致。
2. **港股/美股**：`hk_*` / `us_*` 接口已在扫描范围内，但 `us_daily` 是**全市场快照**
   （一次 6000 条），与逐标的拉取的成本模型不同。倾向：一期把 `us_*` 纳入仓但不纳入
   「按标的全量拉」的默认目标集合，等 `REQ-011.2` 的实跑数据出来再定。
3. **标的显示名/标识统一**（走查 `P-6`）：`display_name` 在清单里已经统一，
   但 `output/<公司>/` 的目录名是 `600887_伊利`。倾向：**本需求不动目录名**（动了会波及
   run-store 与所有报告链接），只在 GUI 侧用 `display_name` 显示；目录名与 ticker 的映射表
   写进 `universe`（`dir` 字段可选）。这条与 `REQ-012.1` 的 `AC-1.4` 对齐。
4. **仓的 schema 演进**：`meta.schema_version` 已有，但迁移脚本的存放约定（
   `datalayer/migrations/0002_*.py`？）要在 `REQ-011.1` 开工时定。
5. **配额档案的自定义档位**：`universe.tier` 指向自定义接口列表时，谁校验它与 `scan_safe_calls()`
   的关系？倾向：自定义档位必须是扫描集合的子集，由测试断言。
