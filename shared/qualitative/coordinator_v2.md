# 定性分析模块 — 协调器 v2

> **角色**：你是项目经理。职责：(1) 验证输入；(2) 加载数据；(3) 启动定性分析；(4) 交付完整报告。
>
> **架构变更 (v3)**：evidence-first 分层分析。年报 PDF 不再默认整份载入 Agent context；先建立可定位的证据索引，再按模块生成有预算的 context bundle。
> 子模块输出 `result.json` 和人工可读的 `report.md`，Cross-check Agent 负责冲突检查，Final Synthesis Agent 基于模块结果和精选证据重新撰写最终报告。

---

## 输入解析

| 输入项 | 示例 | 必需？ |
|--------|------|--------|
| 股票代码或名称 | `600690` / `海尔智家` / `0001.HK` / `AAPL` | 必需 |
| 年报 PDF | 本地文件路径 或 URL | 可选（有则跳过 WebSearch） |

**解析规则**：
1. 从用户消息中提取股票代码/名称
2. 若用户提供了 PDF 链接/路径 → 下载到 `{output_dir}/annual_report.pdf`
3. 代码格式化：A股 → `XXXXXX.SH/SZ`；港股 → `XXXXX.HK`；美股 → `AAPL.US`

---

## 执行流程

```
Step 1: Tushare + PDF 采集
        ↓
Step 2: evidence/index.json + contexts/*.json
        ↓
Step 3: 模块 Agent 并行输出 modules/*/result.json
        ↓
Step 4: Cross-check Agent 输出 reconciliation.json
        ↓
Step 5: Final Synthesis Agent 重新撰写 qualitative_report.md
        ↓
Step 6: HTML 仪表盘（可选）
```

---

## Step 1 详细指令

### 1A：Tushare 数据采集

```bash
mkdir -p {output_dir}
python3 scripts/tushare_collector.py --code {ts_code} --output {output_dir}/data_pack_market.md
```

### 1B：PDF 获取与加载

**PDF 获取优先级**：
1. 用户已提供 PDF 路径/URL → 直接使用
2. 用户未提供 PDF → 默认使用 `/download-report {stock_code} 年报 {output_dir}` 下载最近 3 个财年年报
   - 若用户明确只要某一年 → 使用 `/download-report {stock_code} {year_if_known} 年报 {output_dir}`
   - 下载目标目录：`{output_dir}/`
   - 若返回 `PARTIAL` 或 `FAILED` → 向上层汇报缺失年份，再对缺失年份/缺失上下文 fallback 到 WebSearch（Step 1C-fallback）

**PDF 读取策略**：

1. **先读目录**（通常前 3-5 页）→ 确认 PDF 类型和章节页码
2. **判断 PDF 类型**：
   - 纯文本 PDF → 也先使用 `pdf_preprocessor.py` 生成规范化 `pdf_sections.json`；仅在证据冲突时按页回查原 PDF
   - 扫描/图片 PDF → fallback 到 `python3 scripts/pdf_preprocessor.py`
3. **按需读取关键章节**（优先级排序）：

| 优先级 | 章节 | 典型页码范围 | 分析用途 |
|--------|------|-----------|--------|
| P0 | 致股东信 | 前 5-8 页 | 战略概览、管理层风格 |
| P0 | 管理层讨论与分析 | 16-60 | D1收入质量、D3行业、D5 MD&A |
| P0 | 公司治理 | 61-85 | D4 管理层 |
| **P0** | **第五节 重要事项** | **58-71（因公司而异，以目录为准）** | **收购重组、重大诉讼、关联交易、重大合同、对外担保、股票回购 — 此节是 D4 治理判断的核心依据，不可跳过** |
| P1 | 公司简介和主要财务指标 | 10-15 | D1 基础数据 |
| P1 | 股东情况 | 101-108 | D4 股权结构 |
| P2 | 财务报告附注 | 115+ | D6 控股结构、关联交易 |

每次读取最多 20 页，按优先级分批读取。原始 PDF 不直接传给最终汇总 Agent；提取结果必须写入 `pdf_sections.json` 或证据索引。

**证据索引与模块上下文**（在 Step 1 全部完成后执行，包括 WebSearch 补充与 PDF 附注提取）：

```bash
python3 -m scripts.results.prepare \
  --output-dir "{output_dir}" \
  --ticker "{ts_code}" \
  --company "{company_name}" \
  --market "{market}"
```

每个 context bundle 默认不超过 24,000 字符，并记录实际字符数、估算 Token 数和是否发生裁剪。
市场正文、PDF 正文和证据摘录使用独立预算，各章节公平分配。检查 `selection.evidence_coverage`：值为证据 ID 表示已提供摘录，`missing` 表示索引缺少对应章节，`omitted` 表示预算内未选入。章节命中和摘录覆盖不等于完整核验；对必查项缺口，应授权按 source/section 或 ID 限量回查同 run 索引，并记录补证范围。索引也缺失时才补充原始输入并重新 prepare。
新版 Markdown 证据按二级标题分段，例如 `market_data:3:001`、`pdf_footnotes:P6:001`。旧 run 的索引及结果仍可校验，但不得把新旧 ID 混用；复跑验证使用独立目录。
prepare 会固定输入及 evidence/context/routing 产物的 SHA-256。之后不得改写这些文件；需要补充数据时重新 prepare，并重跑模块、reconciliation 和 synthesis。

> **重要**：第五节 重要事项（Significant Matters）是中国年报的法定必备章节，包含：
> - 重大资产收购/出售/重组（"发行股份购买资产"、"重大资产重组"）
> - 重大关联交易
> - 重大诉讼/仲裁
> - 对外担保
> - 募集资金使用情况
> - 承诺事项履行情况
> 如果此节未读取，M&A、重大合同等关键信息将完全遗漏，严重影响 D4（管理层与治理）和 D5（MD&A 解读）的分析质量。

**1C-fallback：WebSearch 降级（仅当 PDF 下载失败时）**：
- 使用 WebSearch 补充 §7（管理层）、§8（行业）、§10（MD&A）
- 搜索时优先获取最近完整财年数据，WebSearch 关键词中加入"年报""全年"以避免返回半年报/季报结果
- 在报告中标注数据来源为 WebSearch，可信度相应降低

### 1C：PDF 附注提取（仅当有 PDF 时，须在 prepare 前完成）

> 此步骤为下游策略提供结构化附注数据，也是本次定性证据索引的输入。可以与其他采集步骤并行，但必须全部完成后再 prepare。

```
Agent(
  subagent_type = "general-purpose",
  prompt = """
  请阅读 {workspace}/prompts/phase2_PDF解析.md 中的提取清单和输出格式。

  年报 PDF 文件：{output_dir}/{pdf_filename}

  步骤：
  1. 使用 Read 工具读取 PDF 前 3-5 页，获取目录页，定位附注各章节的页码。
  2. 判断 PDF 类型（纯文本 or 扫描件）：
     - 若 Read 返回清晰的中文文字和表格 → 纯文本 PDF，继续步骤 3
     - 若 Read 返回乱码或极少文字 → 扫描件，输出标记 `PDF_TYPE=SCANNED` 后停止
  3. 按优先级从 PDF 中直接 Read 对应章节（每次最多 20 页）：
     P0: 非经常性损益明细(P13)、受限现金明细(P2)
     P1: 应收账款账龄(P3)、关联交易(P4)、或有负债与承诺(P6)
     P2: 主要控股参股公司(SUB，条件触发：仅控股公司结构)
  4. 按 phase2_PDF解析.md 的格式提取结构化数据。
  5. P6 必须交叉核对重要事项的对外担保总表与附注，覆盖供应商、经销商及子公司担保。
     区分年度发生额、期末责任余额、逾期金额和已确认损失；关联担保跨表披露不得重复加总。
     同时核对受限资金期初/期末、非经常性损益当前期/比较期、子公司注册资本原始单位。
     pdf_sections 的 sections_found 仅代表命中章节，不代表上述检查完成。

  将提取结果写入：{output_dir}/data_pack_report.md
  """,
  description = "PDF附注提取(供下游策略)"
)

# 扫描件 fallback（仅当上述 Agent 返回 PDF_TYPE=SCANNED 时执行）
Bash(
  command = "python3 scripts/pdf_preprocessor.py --pdf {output_dir}/{pdf_filename} --output {output_dir}/pdf_sections.json",
  description = "PDF预处理-扫描件fallback"
)
Agent(
  prompt = """
  请阅读 {workspace}/prompts/phase2_PDF解析.md 中的完整指令。
  pdf_sections.json 文件路径：{output_dir}/pdf_sections.json
  公司名称：{company_name}
  将解析结果写入：{output_dir}/data_pack_report.md
  """,
  description = "PDF精提取-扫描件fallback"
)
```

**无 PDF 时**：跳过此步骤。下游策略在无 `data_pack_report.md` 时使用降级方案。

---

## Step 2 详细指令

### 模式 A：分层模块分析（推荐）

并行启动以下模块 Agent。每个 Agent 只读取对应的 context bundle，不读取完整 PDF 或完整 data_pack：

```text
business_moat      → contexts/business_moat.json      → modules/business_moat/result.json
environment        → contexts/environment.json        → modules/environment/result.json
governance         → contexts/governance.json         → modules/governance/result.json
mda_quality        → contexts/mda_quality.json        → modules/mda_quality/result.json
holding_structure  → contexts/holding_structure.json  → modules/holding_structure/result.json（条件触发）
```

每个 Agent 必须：

1. 只分析自己的 scope，区分事实、推断和判断。
2. 每个重要判断引用 `evidence_id`。
3. 输出符合 `investment.result` v1.0 的 `result.json`。
4. 同时输出供人工审阅的 `report.md`，但该正文不作为最终汇总默认输入。
5. 数据不足时输出 `partial` 或 `failed`，不得补造结论。只有 D6 明确不适用时使用 `not_applicable`；D6 缺失不能推断为不适用。
6. 写入后执行 `python3 -m scripts.results.validate_result "{output_dir}/modules/{module}/result.json" --evidence-index "{output_dir}/evidence/index.json"`，并复核摘录是否真正支持相应判断。

模块提示词：

```text
shared/qualitative/agents/modules/business_moat.md
shared/qualitative/agents/modules/environment.md
shared/qualitative/agents/modules/governance.md
shared/qualitative/agents/modules/mda_quality.md
shared/qualitative/agents/modules/holding_structure.md
```

所有模块 Agent 另行读取 `shared/qualitative/agents/module_output_contract.md`。Agent A/B 的旧版长 Markdown 输出只作为兼容路径，不作为 v3 汇总输入。

### 模式 B：Cross-check 与 Final Synthesis

等待所有模块完成后运行：

```bash
python3 -m scripts.results.reconcile_results \
  --input "{output_dir}/modules/business_moat/result.json" \
  --input "{output_dir}/modules/environment/result.json" \
  --input "{output_dir}/modules/governance/result.json" \
  --input "{output_dir}/modules/mda_quality/result.json" \
  --optional-input "{output_dir}/modules/holding_structure/result.json" \
  --output "{output_dir}/synthesis/reconciliation.json"
```

然后为最终 Agent 构建专用上下文：

```bash
python3 -m scripts.results.synthesis \
  --input "{output_dir}/modules/business_moat/result.json" \
  --input "{output_dir}/modules/environment/result.json" \
  --input "{output_dir}/modules/governance/result.json" \
  --input "{output_dir}/modules/mda_quality/result.json" \
  --optional-input "{output_dir}/modules/holding_structure/result.json" \
  --reconciliation "{output_dir}/synthesis/reconciliation.json" \
  --evidence-index "{output_dir}/evidence/index.json" \
  --output "{output_dir}/synthesis/context.json"
```

启动 Final Synthesis Agent，读取 `shared/qualitative/agents/final_synthesis.md` 和 `synthesis/context.json`。它由大模型重新撰写执行摘要、六维度分析、跨维度判断、风险排序和投资启示，不得直接拼接模块正文；同时输出 `qualitative_report.md` 和 `synthesis/result.json`。
汇总默认不超过 30,000 字符；参数、主判断、关键风险、指标口径和 reconciliation 保留原值，其他条目按整体 JSON 预算选入。检查各卡片 `omitted` 计数，对影响结论的省略项按 `result_path` 限量补读；未能补齐时披露缺口。引用沿用模块精准摘录，按 `quote_index` 选择对应原文，不得拼接多个不连续摘录。交付前执行带 `--evidence-index` 的 sidecar 校验，并单独核查金额、单位、日期与判断的语义对应。

交付前执行 `.venv/bin/python -m scripts.results.resolve_qualitative --output-dir "{output_dir}" --ticker "{ts_code}" --output "{output_dir}/qualitative_input.json"`。只有 `source=structured` 才表示完整 run 通过校验；退出码 3 需要修复数据或重跑分析，不能回退同目录的 Markdown。

---

## Step 3：HTML 仪表盘（可选 — 仅用户明确要求时执行）

**默认跳过此步骤。** 仅当用户明确要求 HTML 输出时执行（如参数含 `--html`，或提到"HTML"/"网页"/"仪表盘"）。

```bash
# 本地预览（内嵌 CSS）
python3 scripts/report_to_html.py \
  --input {output_dir}/qualitative_report.md \
  --output {output_dir}/qualitative_report.html \
  --standalone

# 网站部署（引用外部 CSS）
python3 scripts/report_to_html.py \
  --input {output_dir}/qualitative_report.md \
  --output ~/Projects/Teracnejiang.com/zh/stock/{slug}.html
```

---

## 异常处理

| 异常情况 | 处理方式 |
|---------|---------|
| PDF 下载失败 | 提示用户重新提供链接；fallback 到 WebSearch |
| PDF 为扫描件 | 定性分析：使用 pdf_preprocessor.py 处理；附注提取：fallback 到 pdf_preprocessor.py + Agent |
| PDF 附注提取失败 | 不影响定性分析；下游策略使用降级方案（无 data_pack_report.md） |
| Tushare Token 缺失 | 降级使用 yfinance，标注数据源 |
| PDF + Tushare 数据冲突 | 以 PDF 为准，标注差异 |
| 模块 Agent 失败 | 写入 `failed`/`partial` 结果或记录缺失，Final Synthesis 明确降低置信度，不得猜测 |
| 模块结论冲突 | 由 `reconcile_results.py` 记录，Final Synthesis 必须解释冲突及其投资影响 |

---

## 文件路径约定

```
{workspace}/
├── shared/qualitative/
│   ├── coordinator_v2.md              ← 本文件
│   ├── qualitative_assessment_v2.md   ← 分析框架 v2
│   ├── agents/writing_style.md        ← 写作风格（复用）
│   └── references/                    ← 参考文件（复用）
├── scripts/
│   ├── tushare_collector.py           ← Tushare 采集
│   └── report_to_html.py             ← MD→HTML
├── prompts/
│   └── phase2_PDF解析.md              ← 附注提取格式规范（Step 1C 引用）
└── output/{code}_{company}/
    ├── annual_report.pdf              ← 年报 PDF
    ├── data_pack_market.md            ← Tushare 结构化数据
    ├── data_pack_report.md            ← PDF 附注结构化数据（Step 1C 输出，供下游策略）
    ├── evidence/index.json            ← 可定位证据索引
    ├── contexts/*.json                ← 模块化、有预算的上下文
    ├── modules/*/result.json          ← 机器消费的模块结果
    ├── modules/*/report.md            ← 模块人工审阅报告
    ├── synthesis/reconciliation.json  ← 冲突与交叉验证结果
    ├── qualitative_report.md          ← Final Synthesis Agent 报告
    └── qualitative_report.html        ← HTML 仪表盘（可选）
```

---

*定性分析模块 v3.0 | Evidence-first 分层协调器*
