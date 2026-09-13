Run a standalone Business Model & Moat Qualitative Analysis (商业模式与护城河定性分析) on stock: $ARGUMENTS

## Input Validation
- Stock code must be a valid A-share (e.g., 600887, 000858.SZ), HK stock (00700.HK), or US stock (AAPL)
- If $ARGUMENTS is empty or invalid, ask the user for a valid stock code before proceeding
- If only digits are given, the code will be normalized by scripts/config.py

## Execution Instructions

Read shared/qualitative/coordinator_v2.md for the full pipeline specification, then execute each step:

### Step 1: Data Collection (parallel)

**1A: Tushare structured data**
```bash
mkdir -p output/{code}_{company}
python3 scripts/tushare_collector.py --code $ARGUMENTS --output output/{code}_{company}/data_pack_market.md
```
- `data_pack_market.md` must end up in the **same** `output/{code}_{company}/` directory as `qualitative_report.md`
- If any tool or fallback path writes market data to an alternate directory (e.g. `output/{code}_auto/`), reconcile it before finishing this command

**1B: PDF acquisition and loading**
- First check if a PDF already exists in output/{code}_{company}/ (glob for `*年报*.pdf` or `*annual*.pdf`)
  - If found → use the existing PDF, skip download
- If user provided a PDF path or URL → use it directly
- If no PDF found and no PDF provided → use `/download-report {stock_code} 年报 output/{code}_{company}` by default to download the latest 3 annual reports
  - If the user explicitly requested a single fiscal year, use `/download-report {stock_code} {year_if_known} 年报 output/{code}_{company}`
   - Download target: output/{code}_{company}/
   - If status is `PARTIAL` or `FAILED` → **stop immediately**, tell the user which years are missing, and ask the user to download/provide the annual report PDF before continuing
- For the layered pipeline, normalize the newest available annual report before starting module Agents:
  `python3 scripts/pdf_preprocessor.py --pdf output/{code}_{company}/{pdf_filename} --output output/{code}_{company}/pdf_sections.json`
  Direct PDF page reads are reserved for targeted evidence verification and must not be passed to Final Synthesis.
- Read PDF using Read tool: first read table of contents (pages 1-5), then read key sections by priority:
  - P0: Letter to shareholders (pp 5-8), MD&A (pp 16-60), Corporate governance (pp 61-85)
  - P1: Company overview & key financials (pp 10-15), Shareholder info (pp 101-108)
  - P2: Financial statement notes (pp 115+)
- Each Read call: max 20 pages, read by priority order

**1C: WebSearch fallback (only if user explicitly chooses to continue without PDF after being informed of lower confidence)**
- Use WebSearch (via Agent) to supplement §7 (management/governance), §8 (industry/competition), §10 (MD&A)
- Read shared/qualitative/data_collection.md for WebSearch instructions
- Search queries must include "年报" or "全年" to prioritize full-year data over interim (H1/Q3) data
- Append results to data_pack_market.md
- Mark data source as WebSearch in report (lower confidence)

**1D: PDF Footnote Extraction (if PDF available, produces data_pack_report.md for downstream strategies)**
- Read prompts/phase2_PDF解析.md for extraction format spec
- For plain-text PDFs: Read footnote sections directly from PDF by page range (from TOC)
- For scanned PDFs: fallback to `python3 scripts/pdf_preprocessor.py` → pdf_sections.json → Agent extraction
- Extract: P2 (restricted cash), P3 (A/R aging), P4 (related party transactions),
  P6 (contingent liabilities), P13 (non-recurring items), SUB (subsidiaries, conditional)
- Output: output/{code}_{company}/data_pack_report.md
- This step may run alongside other collection steps, but must finish before `scripts.results.prepare` because its output is a hashed evidence input.
- If no PDF available: skip (downstream strategies use degraded mode)

### Step 2: Layered Module Qualitative Analysis

Do not load the complete annual report or `data_pack_market.md` into one Agent. Wait for all collection, WebSearch supplements, and footnote extraction to finish, then build bounded module contexts. Inputs and prepared artifacts must remain unchanged until the run completes:

```bash
python3 -m scripts.results.prepare \
  --output-dir "output/{code}_{company}" \
  --ticker "{stock_code}" \
  --company "{company_name}" \
  --market "{market}"
```

并行启动模块 Agent。每个 Agent 只读取自己的 context bundle，并写入：

```text
output/{code}_{company}/modules/{module}/result.json
output/{code}_{company}/modules/{module}/report.md
```

每个 `result.json` 必须符合 `investment.result` v1.0；重要判断必须引用 `evidence_id`。

模块 Agent 分别读取：

```text
shared/qualitative/agents/modules/business_moat.md
shared/qualitative/agents/modules/environment.md
shared/qualitative/agents/modules/governance.md
shared/qualitative/agents/modules/mda_quality.md
shared/qualitative/agents/modules/holding_structure.md
```

等待模块完成后运行：

```bash
python3 -m scripts.results.reconcile_results \
  --input "output/{code}_{company}/modules/business_moat/result.json" \
  --input "output/{code}_{company}/modules/environment/result.json" \
  --input "output/{code}_{company}/modules/governance/result.json" \
  --input "output/{code}_{company}/modules/mda_quality/result.json" \
  --optional-input "output/{code}_{company}/modules/holding_structure/result.json" \
  --output "output/{code}_{company}/synthesis/reconciliation.json"
```

构建最终汇总上下文：

```bash
python3 -m scripts.results.synthesis \
  --input "output/{code}_{company}/modules/business_moat/result.json" \
  --input "output/{code}_{company}/modules/environment/result.json" \
  --input "output/{code}_{company}/modules/governance/result.json" \
  --input "output/{code}_{company}/modules/mda_quality/result.json" \
  --optional-input "output/{code}_{company}/modules/holding_structure/result.json" \
  --reconciliation "output/{code}_{company}/synthesis/reconciliation.json" \
  --evidence-index "output/{code}_{company}/evidence/index.json" \
  --output "output/{code}_{company}/synthesis/context.json"
```

最后读取 `shared/qualitative/agents/final_synthesis.md`，启动 Final Synthesis Agent。它由大模型重新撰写执行摘要、六维度综合判断、风险排序和投资启示；不得直接拼接模块正文，也不得读取完整 PDF。输出 `qualitative_report.md` 和 `synthesis/result.json`。

验证最终 sidecar：

```bash
python3 -m scripts.results.validate_result "output/{code}_{company}/synthesis/result.json"
```

Before delivery, validate the entire run:
```bash
.venv/bin/python -m scripts.results.resolve_qualitative --output-dir "output/{code}_{company}" --ticker "{stock_code}" --output "output/{code}_{company}/qualitative_input.json"
```
Require `source=structured`. Exit status 3 requires fixing or rerunning the analysis; never fall back to the Markdown report in a manifest-backed run. Status 2 is an invocation error.

### Step 3: Generate HTML Dashboard Report (optional — only when user requests)

**Skip this step by default.** Only execute if the user explicitly requests HTML output (e.g., "--html" in arguments, or mentions "HTML"/"网页"/"仪表盘").

```bash
# For local viewing (standalone, inline CSS):
python3 scripts/report_to_html.py --input output/{code}_{company}/qualitative_report.md --output output/{code}_{company}/qualitative_report.html --standalone

# For website deployment (external CSS, terancejiang.com):
python3 scripts/report_to_html.py --input output/{code}_{company}/qualitative_report.md --output ~/Projects/Teracnejiang.com/zh/stock/{slug}.html
```

## 6 Dimensions Covered
1. Business model & capital characteristics (商业模式与资本特征)
2. Competitive advantage & moat (竞争优势与护城河)
3. External environment (外部环境)
4. Management & governance (管理层与治理)
5. MD&A interpretation (MD&A 解读)
6. Holding structure analysis (控股结构分析, conditional)

## v3 Key Changes
- **Evidence-first architecture**: Annual reports are indexed and addressed by evidence IDs instead of being loaded wholesale
- **Module result protocol**: Each module writes validated `result.json` plus an auditable `report.md`
- **Hierarchical LLM synthesis**: Cross-check Agent detects conflicts and Final Synthesis Agent rewrites the final investment narrative
- **Bounded contexts**: Each module context has a hard character budget and records truncation
- **WebSearch fallback**: Only used when primary sources are unavailable, with lower confidence recorded

## Error Recovery
- If PDF download fails → stop and ask the user to download/provide the annual report PDF
- If PDF is scanned → use python3 scripts/pdf_preprocessor.py
- If Tushare fails → use yfinance fallback
- If PDF and Tushare data conflict → trust PDF, note discrepancy
- If WebSearch returns no results → mark as "⚠️ 数据不可用" and degrade that dimension
- Always produce a final report even with partial data only after PDF access is resolved or the user explicitly accepts degraded mode

## Final Output Audit
- Before finishing, verify that these files are in the **same** `output/{code}_{company}/` directory:
  - `qualitative_report.md`
  - `data_pack_market.md`
  - `data_pack_report.md` (if generated)
  - `evidence/index.json`
  - `synthesis/reconciliation.json`
  - `synthesis/result.json`
- If `data_pack_market.md` was written to any alternate directory (such as `output/{code}_auto/`), regenerate or move it into the final company directory before returning success
- Return the final resolved output directory explicitly

## Output
- **MD report** (default): output/{code}_{company}/qualitative_report.md
  - Includes: Executive Summary + 6 Dimensions + Cross-Validation + Deep Conclusion + Structured Parameters
- **PDF footnote data** (if PDF available): output/{code}_{company}/data_pack_report.md
  - Structured extraction: P2/P3/P4/P6/P13/SUB — used by downstream strategies (value analysis, etc.)
- **HTML dashboard** (optional, only when requested): output/{code}_{company}/qualitative_report.html
  - For local viewing: `--standalone` flag embeds CSS inline
  - For website deployment: references external CSS from terancejiang.com

Usage: /business-analysis 600887 or /business-analysis 00700.HK or /business-analysis AAPL
