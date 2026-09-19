# 价值分析模块 v1.0 — 协调器（Coordinator）

> **角色**：你是项目经理。职责：(1) 验证输入；(2) 检查 `/business-analysis` 前置输出；(3) 刷新市场敏感数据；(4) 调度 Python 预计算；(5) 调度单次价值分析与报告生成；(6) 交付最终报告。你不先入为主，不把低 PB 或低净资产折价当作核心结论。
>
> 本模块采用巴菲特、芒格、段永平风格：先判断是不是好生意、好管理层、合理价格，再看当前股价相对未来现金流折现是否有吸引力。
>
> 通用估值（`/valuation`）是本模块的子模块，位于 `strategies/value/valuation/`；本模块聚焦现金流折现与收购视角，需要 DCF / DDM / PE Band / PEG / PS 等多方法估值时可单独调用该子模块。

---

## 输入解析

| 输入项 | 示例 | 必需？ |
|--------|------|--------|
| 股票代码或名称 | `600887` / `伊利股份` / `00700.HK` / `AAPL` | 必需 |

**解析规则**：
1. 代码格式化：A股 → `XXXXXX.SH/SZ`；港股 → `XXXXX.HK`；美股 → `AAPL`
2. 若用户只给了 6 位数字 → 由 `scripts/config.py` 自动补充后缀
3. 若模糊 → AskUserQuestion 确认

---

## 前置条件检查

```
{ticker} = scripts/config.py 校验后的 canonical ticker
{ts_code} = {ticker}（传给数据脚本的 canonical ticker）
{directory_code} = 仅移除 {ticker} 最后一个市场后缀后的目录代码（BRK.B.US -> BRK.B）
{code} = {directory_code}（仅用于目录和报告文件名）
{output_dir} = 唯一匹配的 {workspace}/output/{directory_code}_*/ 公司目录
```

**必须存在**：
1. 可消费的定性输入：优先四个核心 `modules/*/result.json`，兼容回退到 `{output_dir}/qualitative_report.md`
2. `{output_dir}/data_pack_market.md` — 市场与财务数据包

执行 `.venv/bin/python -m scripts.results.resolve_qualitative --output-dir "{output_dir}" --ticker "{ticker}" --output "{output_dir}/qualitative_input.json"`。退出状态 3 视为定性输入缺失，退出状态 2 表示命令参数错误；不得混用不完整 JSON 结果集和旧报告的参数。

如果发现同一股票的 `qualitative_report.md` 与 `data_pack_market.md` 落在不同 `output/{code}_*/` 目录：
- 先停止“缺文件”结论
- 优先识别哪个目录是最终公司目录
- 将缺失的数据包重新生成或整理到同一目录后再继续

**可选**：
3. `{output_dir}/data_pack_report.md` — 年报附注提取，用于验证利润含金量、现金真实性、或有负债等

| 条件 | 操作 |
|------|------|
| resolver source 可用且数据包存在 | 继续执行 |
| resolver unavailable 或数据包缺失 | **先自动执行** `/business-analysis {stock_code}`，完成后重新检查 |

若自动执行 `/business-analysis` 后仍缺失，或年报下载失败，则停止并提示用户提供/下载年报 PDF。

---

## 方法原则

1. **先生意，后估值**：没有商业质量支撑的低估值，不构成价值机会。
2. **现金流视角优先**：核心问题是“今天买下这家公司，未来能拿回多少现金流”。
3. **不以净资产折价为核心**：除金融、地产、强清算属性行业外，不把 Graham 式资产折价作为主估值逻辑。
4. **收购视角检查**：必须计算 `EE = (市值 + 负债 - 现金) / 利润`，并参考 owner-earnings 收购倍数，把买股票视作买企业整体。
5. **先做否决，再做估值**：若诚信、复杂性、资产负债表防守层不过关，默认动作应更严格。
6. **容忍模糊，但不容忍自欺**：若数据质量差、利润水分高、资本配置差，即使表面便宜也应降低估值置信度。
7. **特定行业启用双锚**：周期、重资产、高不确定性行业，除现金流估值外，还必须检查资产负债表 backstop。
8. **金融行业单独建模**：银行、保险、券商、多元金融等强监管资本行业，不把普通工业企业式 FCF / Owner Earnings DCF 作为主锚，应优先使用 `ROE / 资本充足率 / 股息能力 / P-TBV / 残余收益` 框架。其中银行股需额外关注：不良贷款率、拨备覆盖率、净息差、存贷款结构变化、股息率 vs 国债收益率对比。

---

## 阶段调度

```
┌─────────────────────────────────────────────────┐
│              用户输入解析                          │
│   股票代码 = {code}, 公司名称 = {company}         │
└──────────┬──────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────┐
│  前置检查                                         │
│  qualitative_report.md 存在? ✓                   │
│  data_pack_market.md 存在? ✓                     │
│  data_pack_report.md 存在? 可选                   │
└──────────┬──────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────┐
│  Step 1: 市场数据刷新                              │
│  tushare_collector.py --refresh-market            │
│  → 更新价格 / 市值 / 周线 / 无风险利率             │
└──────────┬──────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────┐
│  Step 2: Python 预计算                              │
│  value_analysis_engine.py → value_computed.md       │
│  buy_sell_engine.py → buy_sell_plan.json / .md      │
└──────────┬──────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────┐
│  Step 3: 价值分析与报告生成                         │
│  读取 business-analysis 输出 + value_computed      │
│  → 未来现金流折现                                 │
│  → EE 收购视角校验                                │
│  → 输出价值分析报告                                │
└──────────────────────────────────────────────────┘
```

---

## Step 1: 市场数据刷新

### 执行命令

```bash
cp "{output_dir}/data_pack_market.md" "{output_dir}/data_pack_market_current.md"
.venv/bin/python scripts/tushare_collector.py --code "{ticker}" --output "{output_dir}/data_pack_market_current.md" --refresh-market
```

保留 manifest 固定的原始 `data_pack_market.md`，只刷新副本。下游定性仍以原 run 为准；若发现影响定性判断的新财报，重新执行 `/business-analysis`。

### 目标
- 刷新当前股价与总市值
- 刷新 52 周区间和长期价格位置
- 刷新无风险利率
- 不重做完整 business-analysis，只刷新估值最敏感的行情数据

### 失败处理
- 若刷新失败，允许使用现有 `data_pack_market.md`
- 必须在最终报告中明确标注时效性下降

---

## Step 2: Python 预计算

### 执行命令

```bash
.venv/bin/python scripts/value_analysis_engine.py --code "{ticker}" --output-dir "{output_dir}"
```

### 输出

`{output_dir}/value_computed.md`、`value_computed.json`（默认**不**生成买卖计划或 `buy_sell_market.json`）

### 包含内容
- 默认估值锚（FCF / Owner Earnings / Profit）
- Owner Earnings 桥接
- 保守 OE、跨周期归一化判断、资本配置观察项、防守层评级
- 默认增长率与要求回报率护栏
- 三情景估值与敏感性表
- `EE = (市值 + 负债 - 现金) / 利润`
- 资产保护价（如净流动资产/股、有形净资产/股）
- 给最终分析 Agent 的调整接口

---

## Step 2B: 买卖计划（触发式，默认不执行）

主流程只产出研究报告与冻结的 `value_computed.json`，**不自动生成**买卖计划。用户阅读报告后，若决定生成，再单独运行 `/buy-sell-plan {ticker}`：

```bash
.venv/bin/python scripts/buy_sell_plan.py --code "{ticker}" --output-dir "{output_dir}"
```

该命令采集当时行情、写出 `buy_sell_market.json`，再对冻结基准离线运行 `buy_sell_engine.py`，输出 `buy_sell_basis.json`、`buy_sell_plan.json`、`buy_sell_plan.md`。它不重算、不覆盖 `value_computed.json`；同财报期沿用固定基准，只有新财报期或显式 `--valuation-cycle "日期-原因"` 更新。`buy_sell_state.json` 仅记录实际成交，不把推荐当成交。无状态时按首次投入百分比输出。

硬退出优先：结构化 `integrity_rating=不可靠` 自动卖出全部持仓；明确的重大财务造假、治理失效、偿债失败、核心业务失效，按合同记录带证据的 `buy_sell_risk.json` 并重跑。一般防守层 caution/severe flags 不能等同于已确认硬退出。

退出码 3：已生成 BLOCKED 计划，报告原样披露缺失项；退出码 2：修复输入后重跑，禁止使用上次产物。详见 `docs/BUY_SELL_CONTRACT.md`。

## Step 3: 价值分析与报告生成

### 读取文件

按顺序读取以下文件：

1. `strategies/value/phase2_value_analysis.md`
2. `strategies/value/references/value_principles.md`
3. `strategies/value/references/report_template.md`
4. `strategies/value/references/bank_valuation.md`（仅金融股读取）
5. `strategies/value/references/gip_handling.md`（仅 GIP 触发时读取）
6. `{output_dir}/value_computed.md`
7. `{output_dir}/qualitative_input.json`
8. `{output_dir}/data_pack_market_current.md`（刷新失败时可读原始包并披露时效性）
9. `{output_dir}/data_pack_report.md`（若存在）
10. `{output_dir}/buy_sell_plan.json` 与 `{output_dir}/buy_sell_plan.md`（**仅当用户已触发 `/buy-sell-plan` 时存在**；存在则必须读取并原样引用确定性数字，Markdown 整段原样插入；不存在时明确说明尚未生成，不得编造）

`source=structured` 时只从 `parameters` 和模块卡片读取定性参数，不再读取旧报告；`source=legacy` 时读取 `legacy_report.path`。

### 输出

`{output_dir}/{company}_{code}_价值分析报告.md`

### 结果检查

确认报告包含：
- 投资结论先行
- 商业质量是否值得持有
- 未来现金流/所有者收益估值主线（基于 `value_computed.md`）
- `EE` 指标与解读（基于 `value_computed.md`）
- 当前价格与内在价值比较
- 若已触发买卖计划：四档买入价格、20/30/30/20 资金比例、单日最多 30%、明确卖出价、确认状态，以及当前档位/动作/累计比例/下一档价格均与 `buy_sell_plan.json` 一致；禁止 LLM 自行重算。未触发时明确标注“尚未生成买卖计划”，不用占位或定性结论替代
- 明确区分当日研究估值与带日期的固定买卖基准；最终交易动作只引用买卖计划
- 先别买的理由 / 反方论证 / 能力圈与复杂性判断
- 关键假设、风险与置信度

---

## 完成交付

```
价值分析完成 ✅

📊 {公司名称}（{股票代码}）
📁 报告路径: {output_dir}/{company}_{code}_价值分析报告.md

当前股价: {price}
估算内在价值区间: {value_range}
EE: {ee}
估值判断: {judgment}
当前执行指令: {buy_sell_plan.execution.action}
本次资金比例: {buy_sell_plan.execution.buy_now_pct}%
下一档价格: {buy_sell_plan.execution.next_price}
极端高估卖出价: {buy_sell_plan.basis.exit_price}
```

---

## 异常处理

| 阶段 | 异常 | 处理 |
|------|------|------|
| 输入 | 代码为空 | AskUserQuestion |
| 前置检查 | qualitative resolver unavailable | 自动执行 `/business-analysis`；若年报下载失败则停止并联系用户提供 PDF |
| 前置检查 | data_pack_market.md 缺失 | 自动执行 `/business-analysis`；若年报下载失败则停止并联系用户提供 PDF |
| Step 1 | `TUSHARE_TOKEN` 缺失或无效 | 使用现有数据包，标注时效风险 |
| Step 2 | `value_analysis_engine.py` 失败 | 停止，提示检查 Python 环境 / Token |
| Step 3 | data_pack_report.md 缺失 | 继续，降低利润/现金质量置信度 |
| Step 3 | 某项财务数据缺失 | 使用保守假设并显式披露 |

---

## 文件路径约定

```
{workspace}     = 项目根目录
{strategy_dir}  = {workspace}/strategies/value
{output_dir}    = {workspace}/output/{code}_{company}
{qualitative}   = {output_dir}/qualitative_input.json
{market_pack}   = {output_dir}/data_pack_market_current.md
{report_pack}   = {output_dir}/data_pack_report.md
{computed}      = {output_dir}/value_computed.md
{report}        = {output_dir}/{company}_{code}_价值分析报告.md
```
