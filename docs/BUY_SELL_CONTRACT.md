# 买卖计划合同 v1.0

## 审查结论与方案修正

- 原 `value_analysis_engine.py` 只输出 Markdown，三情景内部已有 `scenario/per_share`；`valuation_engine.py` 内部方法为 `method/intrinsic`，交叉校验使用**样本**标准差。新增 JSON 适配层，不解析 Markdown 数字，不改变原有估值公式。
- `V_bear/base/bull` 分别引用价值引擎的保守/基准/乐观（含受限乐观）每股价值。CV 不把同一个模型的三种情景当作独立方法：现金流主锚取代通用 DCF，另取分类选中的 DDM、PE_Band、PEG、PS。银行/监管金融只沿用 ResidualIncome，避免不适用模型影响价格；方法不足时 CV 为 null，并明确提高折价。
- 现有防守层 severe/caution flags 包含“需谨慎”等提示，不代表已发生偿债/业务失效。首版不做自由文本定性解析；确定性 `valuation_mode` 只调安全边际。经 resolver 验证的 `integrity_rating=不可靠` 延续诚信底线，确定性输出全部卖出。其他明确硬退出通过带证据的事件输入，见下文。
- 现有周线混有 Tushare 周末标签和 yfinance 周初标签/默认复权，不能直接当完整未复权周收盘确认。复用已取回的 A 股日线、美股 us_daily、港股 fallback 日线（不新增请求）；A 股完整周五收盘可确认。港美股不确定的周线不确认，可提供合同化完整收盘数据。行情未带日期时暂停交易，绝不把运行日期冒充行情日期。
- `/valuation` 继续作为研究子命令，不生成或覆盖交易计划：其分类不包含主流程的银行适配和固定基准。独立估值方法由主流程复用，执行计划统一由 `/value-analysis` 交付。

## 产物与调用

```bash
# 正常主流程：采集/预计算，然后离线生成计划；命令仍只要求股票代码
.venv/bin/python scripts/value_analysis_engine.py --code 600887 --output-dir output/600887_伊利
.venv/bin/python scripts/buy_sell_engine.py --output-dir output/600887_伊利

# 离线重放，读取同一组已导出的 JSON，不访问网络
.venv/bin/python scripts/buy_sell_engine.py --output-dir output/600887_伊利 --as-of 2026-09-14

# 明确周期复核（例如新周期判断/财报更正/拆并股），理由会被记录并延续
.venv/bin/python scripts/value_analysis_engine.py --code 600887 --output-dir output/600887_伊利 --valuation-cycle 2026-09-14-report-restatement
```

| 产物 | 内容 |
|------|------|
| `value_computed.json` | `investment.value_snapshot` v1.0；subject、as_of、financial_period、cycle、values、value_sources、methods、risk、原始 scenarios |
| `buy_sell_market.json` | 本次采集的实际报价、行情日期、完整未复权收盘序列，与估值快照分离 |
| `buy_sell_basis.json` | 固定 values、方法值/CV、M、四档、退出价、财报期、周期、原始 source_snapshot（含情景参数）、源 SHA-256 与 basis_id |
| `buy_sell_plan.json` | 完整 basis、行情摘要及 SHA-256、execution、exit_confirmation、hard_exits、warnings、blockers、instruction_id |
| `buy_sell_plan.md` | 引擎生成的完整中文计划，最终报告必须整段原样插入 |
| `buy_sell_state.json` | 可选，真实成交状态；引擎只读，不会假装已经成交 |
| `buy_sell_risk.json` | 可选，明确的重大失效事实及证据；经确认则覆盖价格动作 |

引擎退出码：`0` 有效指令（含 HOLD/SELL_ALL）；`3` 写出了 BLOCKED 计划；`2` 输入文件/合同错误，停止组装报告，修复后重跑，不使用旧产物。引擎不提交实际订单。

## 价格与基准

所有价格均为每股原币金额：CNY/元、HKD/港元、USD/美元，**不是百万单位**。沿用估值层币种合同，不自行推测汇率；上游必须保证财务与估值币种一致。买入限价向下取两位小数，卖出触发价向上取两位小数，Decimal 中间值和公式保留用于追溯。两位小数是项目报告精度，不替代交易所 tick/手数规则。没有预算时只输出百分比，不假造金额或股数。

1. CV = 独立方法有效正值的样本标准差 / 算术均值。0/负数/缺失/非有限值不作为有效方法；少于两个有效方法时 CV=null，不声称分歧为零。
2. 默认 M：CV<=15% 为 20%；15%<CV<=30% 为 30%；CV>30% 为 40%。CV 未知按 40%，方法不足加 10 个百分点。Defensive 加 10，Conservative 加 5；方法缺失/无效加 5；银行指标缺失加 5；年报附注包缺失加 5。最后限制到 20%-50%，每项调整保留原因；同类缺失不按条数无限累加。
3. P1...P4 = V_base * (1-M-0/10%/20%/30%)，资金 20/30/30/20%，累计 20/50/80/100%。
4. P_exit = max(2*V_base, 1.2*V_bull)。三情景必须为正且 V_bear<=V_base<=V_bull；缺失、乱序、舍入后买价重合/归零或退出价无效时，不发布交易价格，输出 BLOCKED。非标准 JSON 的 NaN/Infinity 拒绝发布产物（退出码 2）；采集适配层把缺失/非有限值归为 null。
5. 同财报期且 cycle 不变，保留原 basis（包括 M）。新财报期取 collector 中 income/balance_sheet/cashflow/fina_indicators 的最新 end_date；期次倒退或缺失报错。更正报表/明确周期变化使用带日期理由的新 `--valuation-cycle`。同财报期新增附注/方法数据也须明确复核周期后纳入。
6. 每次 `value_computed.md/json` 仍反映本次研究计算，计划只消费固定 basis。报告注明研究日与基准日，避免混淆。禁止因价格变化删除 basis、随意修改 basis_id 或每天生成新 cycle。公司行动改变每股口径时须明确重建基准；首版不自动校正拆并股。

## 当前指令与成交状态

`execution.action` 为 `BUY / HOLD / SELL_ALL / DO_NOT_BUY / BLOCKED`，并直接给出 `execute/current_tier/buy_now_pct/sell_position_pct/committed_pct/cumulative_after_fill_pct/target_cumulative_pct/next_price`。

- 当前档位按 `close <= Pi` 取最深档位；等于边界即触发，0 表示高于 P1。缺失价格或无有效价格表时档位为 null。
- 本次资金 = min(当前档位累计目标 - 已投入, 30% - 当日已投入)，下限为零。跳到 P4 时首次只投 30%，不是 100%。待买余额逐日补齐，每次都重新检查价格；反弹后不得补买未达标档位。
- 下一档指**成交后仍未足额配置的首个档位**。跳档时可能是已触及但尚未配满的档位，例如 P4 首投 30% 后下一档为 P2（其累计目标为 50%）。已有持仓不因价格上升自动减仓。
- `next_execution_date` 是最早后续日历日，不声称是交易日；只在实际下一交易日仍达价时执行。日内不重复突破 30% 上限。
- 缺少状态按已投入 0% 的首次方案明确标记 `state_assumed=true`。重复生成是重复建议，不能当新的追加指令。成交后必须提供真实状态；引擎不修改成交账本。
- `SELL_ALL` 卖出现有持仓的 100%，不是预算的 100%；无仓位则无股票可卖，禁止开空仓。退出后 `exited=true` 禁止自动重入，重新建仓需新投资计划/状态。

状态示例（与估值、行情同一 ticker/currency，比例基于该公司固定预算；更新基准不自动清空已投入比例）：

```json
{
  "subject": {"ticker": "600887.SH", "currency": "CNY"},
  "committed_pct": 30,
  "session_date": "2026-09-14",
  "session_spent_pct": 30,
  "exited": false
}
```

## 行情与卖出确认

`buy_sell_market.json` 至少含 subject、close、quote_date、price_basis="unadjusted"。quote_date 必须不晚于 as_of 且不超过 7 个日历日；超期/无日期时暂停价格交易。适配层记录 retrieved_at，不能当行情日期使用。不同市场/币种输入拒绝混用。

当前报价仍 >= P_exit，且以下任一成立时 SELL_ALL；刚触价无确认则 HOLD 并明确等待收盘确认：

- `daily_closes` 最新两个完整收盘均 >= P_exit，最新日期等于 quote_date，两者间隔不超过 7 天。最新记录 `previous_session` 必须等于前一记录日期，用于证明相邻交易日，不用简单自然日推算节假日。
- `weekly_closes` 最近一个已完整收盘 >= P_exit，日期不晚于报价日、距 as_of 不超过 10 天。

每条记录包含 `date/close/complete/price_basis`，日线另含 `previous_session`。未收盘、复权、日期重复、未来/陈旧记录不得确认。离线 JSON 提供者负责 complete 与相邻交易日元数据真实性，不接受 LLM 根据价格“猜测已确认”。适配层只对已知来源推导完整性，不额外联网获取交易日历。

## 硬退出事件

现有结构化诚信评级由 resolver 校验后输入；不可靠直接触发治理硬退出，并记录 governance 证据卡片。对于其余明确事实，报告 Agent 读取已验证的年报/公告证据后可写入以下事件，随后必须重跑引擎让最终动作落入产物，不能留给用户自行裁决：

```json
{
  "subject": {"ticker": "600887.SH", "currency": "CNY"},
  "events": [
    {
      "code": "insolvency",
      "confirmed": true,
      "observed_at": "2026-09-14",
      "evidence": ["evidence_id:对应公告或年报的确切证据编号"]
    }
  ]
}
```

code 仅允许 `financial_fraud/governance_failure/insolvency/core_business_failure`。confirmed 必须为布尔，evidence 必须有非空引用，observed_at 不得晚于 as_of；疑虑不能标为 confirmed。事件文件持续有效，解除时应以新的明确证据更新；不能为了触发买入删除已有硬退出。明确硬退出优先于价格缺失/无效和买入上限。缺少结构化定性时提示未检查/legacy，不能声称“无风险”。

## 报告合同

`/value-analysis` 的两套命令及 coordinator 必须运行新引擎。报告 Agent 必须读取 `buy_sell_plan.json` 并将 `buy_sell_plan.md` 整段原样插入；摘要/最终结论的数字、动作必须引用该产物。不能自行重新计算 M、CV、买卖价、资金比例，也不能用日内指标改变估值。BLOCKED/N/A 必须保留并解释数据缺失，不能编造价格填表。
