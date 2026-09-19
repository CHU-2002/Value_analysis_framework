# 价值分析子模块 · 通用估值 v2.0 — 协调器（Coordinator）

> **模块定位**：本模块是价值分析（`/value-analysis`）的子模块，也可通过 `/valuation` 单独调用。它用多方法估值（DCF / DDM / PE Band / PEG / PS）回答"价格合不合理"，不替代价值分析对生意质量与管理层的判断。

> **角色**：你是项目经理。职责：(1) 验证输入并补全缺失信息；(2) 检查前置条件（定性报告）；(3) 调度 Python 计算 → LLM 定性调整；(4) 监控超时；(5) 交付最终估值报告。你不执行估值计算或数学运算。
>
> 本模块依赖 `/business-analysis` 的结构化定性结果（旧 `qualitative_report.md` 可兼容回退）和 `data_pack_market.md`。

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
2. `{output_dir}/data_pack_market.md` — Tushare 数据包

先解析 run 目录：resolver 不会自己跟随 `latest.json`，直接把公司目录传进去会静默退回 `source=legacy`/`unavailable`。`{output_dir}/latest.json` 存在时执行 `.venv/bin/python scripts/runs.py resolve --company-dir "{output_dir}" --latest` 并记输出为 `{run_dir}`，否则 `{run_dir}` = `{output_dir}`；随后执行 `.venv/bin/python -m scripts.results.resolve_qualitative --output-dir "{run_dir}" --ticker "{ticker}" --output "{run_dir}/qualitative_input.json"`。退出状态 3 视为定性输入缺失，退出状态 2 表示命令参数错误；不得混用不完整 JSON 结果集和旧报告参数。

| 条件 | 操作 |
|------|------|
| resolver source 可用且数据包存在 | 继续执行 |
| resolver unavailable 或数据包缺失 | **停止执行**，输出提示 |

缺失时的提示：
```
⚠️ 前置条件不满足：未找到可消费的定性输入和/或 data_pack_market.md
请先运行 /business-analysis {stock_code} 生成定性分析报告和数据包。
```

> 注意：如果用户已有数据但 output 目录名与代码不匹配（例如手动重命名），需要确认目录路径。

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
└──────────┬──────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────┐
│  Step 1: Python 估值计算                          │
│  valuation_engine.py → valuation_computed.md      │
│  (分类 + WACC + 全部估值方法 + 敏感性表)           │
└──────────┬──────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────┐
│  Step 2: LLM 定性调整与报告                       │
│  读取 qualitative_report.md + valuation_computed  │
│  → 定性调整 → 选择情景 → 组装报告                  │
│  → {company}_{code}_估值报告.md                   │
└──────────────────────────────────────────────────┘
```

---

## Step 1: Python 估值计算

### 执行命令

```bash
cd {workspace}
python3 scripts/valuation_engine.py --code {ts_code} --output-dir {output_dir}
```

### 输出
- `{output_dir}/valuation_computed.md` — 包含：
  - 公司分类（蓝筹价值型/成长型/混合型）+ 选定方法
  - WACC 完整计算
  - 各估值方法的详细结果 + 5×5 敏感性矩阵
  - 交叉验证（初步，未经定性调整）
  - 关键假设清单（标注哪些待定性调整）

### 超时：3 分钟
失败 → 检查 TUSHARE_TOKEN 是否设置，提示用户重试。

---

## Step 2: LLM 定性调整与报告

### 读取文件

按顺序读取以下文件的**完整内容**：

1. `strategies/value/valuation/phase2_valuation.md` — 定性调整执行指令
2. `strategies/value/valuation/references/valuation_methods.md` — 方法论参考
3. `strategies/value/valuation/references/report_template.md` — 报告模板
4. `{run_dir}/qualitative_input.json` — 经校验的结构化定性输入或旧报告回退指针
5. `{output_dir}/valuation_computed.md` — Python 计算结果
6. `{output_dir}/data_pack_market.md` — 原始数据包（备查）

### 执行

按 `phase2_valuation.md` 指令执行：
1. 提取定性参数和叙述洞察
2. 对每个关键假设执行定性调整
3. 从敏感性矩阵中选择调整后的情景
4. 组装最终报告

### 输出

`{output_dir}/{company}_{code}_估值报告.md`

### 结果检查

确认报告包含：
- Executive Summary
- 定性调整说明（每个假设的 before/after）
- 估值方法详情（引用 Python 计算结果）
- 交叉验证（调整后）
- 估值结论
- 免责声明

### 超时：5 分钟
超时 → 输出已完成的部分结果。

---

## 完成交付

```
估值分析完成 ✅

📊 {公司名称}（{股票代码}）
📁 报告路径: {output_dir}/{company}_{code}_估值报告.md

公司类型: {type}
估值方法: {methods}
Python初步估值: {python_central} {币种}/股
定性调整后估值: {adjusted_central} {币种}/股
当前股价: {price} {币种}/股
估值判断: {judgment}
```

---

## 异常处理

| 阶段 | 异常 | 处理 |
|------|------|------|
| 输入 | 代码为空 | AskUserQuestion |
| 前置检查 | qualitative resolver unavailable | 停止，提示 /business-analysis |
| 前置检查 | data_pack_market.md 缺失 | 停止，提示 /business-analysis |
| Step 1 | TUSHARE_TOKEN 缺失 | 停止，提示设置 Token |
| Step 1 | valuation_engine.py 报错 | 展示错误信息，提示用户检查 |
| Step 1 | 超时 | 停止，提示重试 |
| Step 2 | legacy 定性报告格式异常 | 跳过定性调整，直接用 Python 结果 |
| Step 2 | 某方法在 computed 中缺失 | 正常（已被 Python 跳过） |
| Step 2 | 超时 | 输出已完成部分 |

---

## 文件路径约定

```
{workspace}     = 项目根目录
{strategy_dir}  = {workspace}/strategies/value/valuation
{output_dir}    = {workspace}/output/{code}_{company}
{computed}      = {output_dir}/valuation_computed.md
{qualitative}   = {run_dir}/qualitative_input.json
{report}        = {output_dir}/{company}_{code}_估值报告.md
```
