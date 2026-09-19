# 价值分析报告模板

```markdown
# 价值分析报告：{公司名称}（{股票代码}）

> 分析日期：{YYYY-MM-DD} | 方法：Business Analysis + Python Precompute + DCF / Owner Earnings + EE

---

## Executive Summary

**一句话结论**：{公司名称} 是一家{是否为好生意}的公司，当前价格 {price} {currency}，相对估算内在价值 {value_range} {currency}，当前属于{便宜/合理/偏贵/暂难判断}。

**当前动作**：{若已生成买卖计划，原样引用 buy_sell_plan.execution.action 及原因，本次买入资金比例/卖出持仓比例、当前档位、成交后累计比例、下一档价格；否则写“尚未生成可执行买卖计划，阅读后可运行 /buy-sell-plan”}

**极端高估卖出价**：{若已生成买卖计划，原样引用 buy_sell_plan.basis.exit_price、币种及 exit_confirmation 状态；否则标注“未生成”}

**先别买的理由**：
- {no_buy_reason_1}
- {no_buy_reason_2}

**核心买点**：
- {buy_point_1}
- {buy_point_2}

**核心顾虑**：
- {concern_1}
- {concern_2}

| 项目 | 结论 |
|------|------|
| 商业质量 | {高/中/低} |
| 护城河 | {强/较强/中/弱} |
| 管理层与资本配置 | {优秀/合格/观察期/损害价值} |
| 诚信评级 | {可靠/存疑/不可靠} |
| 能力圈判断 | {高/中/低} |
| 复杂性惩罚 | {轻微/明显/严重} |
| 防守层评级 | {Strong/Adequate/Weak} |
| 估值姿态 | {Normal/Conservative/Defensive} |
| 核心估值锚 | {FCF/Owner Earnings/Profit} |
| Python 主结论 | {python_core_takeaway} |
| Python 默认区间 | {python_value_range} |
| 毛毛估合理参考价 | {rough_fair_price} |
| 充分安全边际参考价 | {deep_value_price} |
| 资产保护价 | {asset_backstop_price} |
| 当前安全边际 | {margin_of_safety} |
| 预期回报率（基准） | {expected_return_base} |
| 预期股息率 | {expected_dividend_yield} |
| EE | {ee_value} |
| 估值判断 | {好公司，价格有吸引力/好公司，价格合理/值得关注但价格偏高/质量一般，即使便宜也缺乏吸引力/需要等待更多证据} |

---

## 一、Business Analysis 结论承接

### 1.1 生意本质

{用 1-2 段话概括公司是怎么赚钱的，为什么能赚钱。}

### 1.2 护城河与可持续性

{承接 qualitative_report.md 的核心结论，不重复堆砌细节。}

### 1.3 管理层与资本配置

{判断管理层是否值得长期托付资本。}

### 1.3A 诚信与承诺兑现

{明确写出是否存在财报修订、管理层重大负面事件、承诺未兑现、信息披露不足等问题。}

### 1.4 先回答一个问题：我想不想拥有这门生意？

{用直白语言回答。如果不想拥有，后文估值只作为辅助，不作为买入理由。}

### 1.5 反方论证 / 为什么可能不能买

- {anti_thesis_1}
- {anti_thesis_2}
- {anti_thesis_3}

### 1.6 能力圈与复杂性

{直接回答：我是否真正看得懂；哪里复杂；这种复杂性是否足以让我放弃。}

### 1.7 银行专项：资产质量与业务结构（仅银行股填写）

| 指标 | 值 | 评价 |
|------|-----|------|
| 不良贷款率 (NPL) | {npl_ratio} | {npl_assessment} |
| 拨备覆盖率 | {prov_cov} | {prov_cov_assessment} |
| 净息差 (NIM) | {nim} | {nim_assessment} |
| 资本充足率 | {car} | {car_assessment} |
| 存款结构（活期/定期）| {deposit_structure} | {deposit_comment} |
| 贷款结构（零售/对公）| {loan_structure} | {loan_comment} |
| 贷存比趋势 | {ltd_trend} | {ltd_comment} |

{1-2段话说明当前存贷款结构变化对盈利的影响，以及这是否是暂时性问题。}

---

## 二、真实赚钱能力

| 指标 | 观察 | 结论 |
|------|------|------|
| 净利润 | {value} | {comment} |
| 经营现金流 | {value} | {comment} |
| 自由现金流 | {value} | {comment} |
| 资本开支 | {value} | {comment} |
| 利润含金量 | {high/medium/low} | {comment} |

{解释为什么最终选择 FCF / Owner Earnings / Profit 作为估值锚。}

---

## 三、价值估算

### 3.-1 关键数据看板

| 指标 | 值 | 说明 |
|------|----|------|
| 预期回报率（保守） | {expected_return_bear} | {comment} |
| 预期回报率（基准） | {expected_return_base} | {comment} |
| 预期回报率（乐观） | {expected_return_bull} | {comment} |
| 当前安全边际 | {margin_of_safety} | {comment} |
| 预期股息率（FY） | {forward_dividend_yield} | {comment} |
| 预期股息率（TTM） | {trailing_dividend_yield} | {comment} |
| 毛毛估合理参考价 | {rough_fair_price} | {comment} |
| 充分安全边际参考价 | {deep_value_price} | {comment} |

### 3.0 Python 预计算起点

| 项目 | Python 默认 | 调整后 |
|------|-------------|--------|
| 估值锚 | {python_anchor} | {adjusted_anchor} |
| 基准增长 | {python_base_growth} | {adjusted_base_growth} |
| 要求回报率 | {python_discount_rate} | {adjusted_discount_rate} |
| 终值增长率 | {python_terminal_growth} | {adjusted_terminal_growth} |

{如果发生调整，解释调整依据。}

### 3.0C Python 主结论复述

{用一句话准确复述 value_computed.md 的主结论，例如“Profit 锚 + Defensive 姿态 + 当前安全边际为负，因此价格未给足跨周期安全边际”。}

### 3.0A 投资语言翻译

{不要只写参数变化，要翻译成长期投资者语言，例如“当前价格要求公司未来持续高质量增长”“市场并未给足安全边际”等。}

### 3.0B 为什么现在仍然不能急着买

{把估值、治理、复杂性、防守层风险翻译成动作语言。}

### 3.0D 价值来源摘要（若主锚为 FCF / Owner Earnings 必填）

| 项目 | 值 | 说明 |
|------|----|------|
| 归一化锚值 | {normalized_anchor} | {例如 5-8 年中位数 / 跨周期归一化 / 保守 OE} |
| 第 1-5 年增长 | {stage1_growth} | {核心驱动} |
| 第 6-10 年增长衰减 | {fade_path} | {如何向终值增长率收敛} |
| 折现率 | {discount_rate} | {为什么合理} |
| 终值增长率 | {terminal_growth} | {为什么合理} |
| 前 10 年现金流现值 | {pv_stage1} | {占比说明} |
| 终值现值 | {pv_terminal} | {占比说明} |
| 基准股权价值 | {equity_value_base} | {对应每股价值} |

{用 1 段话说明：这份估值主要来自近 10 年现金流现值，还是主要依赖终值；如果终值占比高，需明确提示估值对终值假设敏感。}

### 3.1 情景设定

| 情景 | 增长假设 | 要求回报率 | 估值结论 |
|------|---------|---------|---------|
| 保守 | {assumption} | {assumption} | {value} |
| 基准 | {assumption} | {assumption} | {value} |
| 乐观 | {assumption} | {assumption} | {value} |

### 3.2 内在价值区间

| 项目 | 值 |
|------|----|
| 当前股价 | {price} |
| 保守价值 | {bear_value} |
| 基准价值 | {base_value} |
| 乐观价值 | {bull_value} |
| 综合判断 | {cheap/fair/expensive} |

---

## 四、EE 收购视角校验

`EE = (市值 + 负债 - 现金) / 利润`

| 变量 | 值 |
|------|----|
| 市值 | {market_cap} |
| 负债 | {liabilities} |
| 现金 | {cash} |
| 利润 | {profit} |
| EE | {ee_value} |
| 收购收益率 | {acquisition_yield} |
| 静态回本年限 | {payback_years} |

{解释这个倍数在“买下整个公司”的语境下意味着什么，以及它和公司质量是否匹配。}

## 四A、资产负债表保护

| 指标 | 值 | 说明 |
|------|----|------|
| 流动比率 | {current_ratio} | {comment} |
| 速动比率 | {quick_ratio} | {comment} |
| 现金/短债 | {cash_to_short_debt} | {comment} |
| 净流动资产/股 | {ncav_per_share} | {comment} |
| 每股有形净资产 | {tangible_book_per_share} | {comment} |

{回答：如果未来 3 年经营不及预期，资产负债表还能给我多少保护？}

## 四B、银行专项：股息率估值与历史区间（仅银行股填写）

### 股息率分析

| 指标 | 值 |
|------|----|
| 当前股息率（FY）| {forward_dividend_yield} |
| 当前股息率（TTM）| {trailing_dividend_yield} |
| 10年期国债收益率 | {rf_rate} |
| 股息率 vs 国债利差 | {yield_spread} |
| 历史股息率低点 | {hist_yield_low} |
| 历史股息率高点 | {hist_yield_high} |

{判断当前股息率在历史中处于什么位置，与国债的利差是否有吸引力。如果当前股息率接近历史低点（如农行从 7.5% 降至 3.9%），应注明性价比已显著下降。}

### 历史 PE/PB 区间

| 指标 | 当前 | 5年低点 | 5年高点 | 5年均值 | 位置判断 |
|------|------|---------|---------|---------|---------|
| PE | {current_pe} | {pe_5y_low} | {pe_5y_high} | {pe_5y_mean} | {pe_position} |
| PB | {current_pb} | {pb_5y_low} | {pb_5y_high} | {pb_5y_mean} | {pb_position} |

{用均值回归逻辑解释当前估值位置。银行股通常呈现以均值为中心的对称运动。若当前 PE 显著高于 5 年均值，应提示估值偏高。}

---

## 五、价格与价值

### 5.1 当前价格隐含了什么

{解释市场当前价格背后的增长/风险预期。}

### 5.2 安全边际

{说明当前价格是否给了足够安全边际。}

### 5.2B 预测型安全边际 vs 资产型安全边际

{区分：当前安全边际主要来自 DCF 假设，还是来自资产负债表 backstop。}

### 5.2A 如果管理层诚信有问题，这个安全边际够不够？

{不要泛泛而谈，要直接回答：当前折价是否已经大到足以覆盖诚信/治理折价。}

### 5.3 如果这是整个公司，我愿不愿意按这个价格买？

{结合 EE、收购收益率、回本年限，用 1-2 段话直接回答。}

---

## 六、关键风险

- {risk_1}
- {risk_2}
- {risk_3}

---

{买卖计划默认不生成。仅当用户已触发 `/buy-sell-plan` 且 `buy_sell_plan.md` 存在时，在此整段原样插入，不自行重算或改写。必须包含 V_bear/V_base/V_bull、CV、要求安全边际 M、四档价格/资金比例、单日上限、卖出价/确认、当前指令、累计比例、下一档、固定基准日期与来源。N/A 或 BLOCKED 原样保留，绝不编造价格。未生成时写“尚未生成可执行买卖计划；阅读报告后可运行 `/buy-sell-plan`”，不写占位计划。}

---

## 七、最终结论

**结论**：{final_conclusion}

**执行指令**：{若已生成买卖计划，再次原样引用 buy_sell_plan 当前动作及百分比，不以“可能买入/观察”等定性意见替代；未生成时说明可在阅读后运行 `/buy-sell-plan`}

**下一档价格/退出条件**：{已生成时原样引用 buy_sell_plan 的下一档价格、极端高估价及确认状态，或硬退出/数据阻断原因；未生成时标注“未生成”}
```
