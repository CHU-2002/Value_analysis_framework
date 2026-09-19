# 测试策略

本文件说明「测试怎么写、跑到什么程度算够」。需求与验收标准见 [`docs/requirements/`](requirements/README.md)，
开发流程与完成定义见 [`docs/DEVELOPMENT.md`](DEVELOPMENT.md)，分支/提交/CI 约定见 [`CONTRIBUTING.md`](../CONTRIBUTING.md)。

## 1. 原则

1. **全 mock，无网络**：CI 与本地测试都不得依赖 `TUSHARE_TOKEN`、真实 HTTP 或真实财报 PDF。
   任何需要真实数据的验证放到手工流程，并用 `integration` 层标记。
2. **确定性**：同样输入必须得到同样输出；不依赖当前时间、随机顺序或真实 `output/` 目录。
3. **测行为不测实现**：断言用户可观察的结果（退出码、文件内容、schema 字段），
   不复制实现里的字面量来「反向确认」它自己。
4. **失败路径与正常路径同等重要**：每条验收标准至少要有一条失败路径的测试。
5. **可追溯**：测试要能回答「它在验哪条需求的哪一款」。

## 2. 测试分层

分层 marker 由 [`tests/conftest.py`](../tests/conftest.py) 的 `TEST_LAYERS` **按文件自动附加**，
不需要逐个测试标注；未登记的文件归入 `unit`。marker 定义在 [`pytest.ini`](../pytest.ini)。

| 层 | 范围 | 现行登记文件 | 运行 |
|----|------|--------------|------|
| `unit` | 单模块逻辑，无网络、无真实文件系统副作用 | 其余全部文件 | `make unit` |
| `contract` | 契约与不变量：schema / 提示词三方一致、哈希校验、原子回退 | `test_qualitative_consumers.py` | `pytest -m contract` |
| `e2e` | mock 端到端，串起多条管线 | `test_results_pipeline.py` | `pytest -m e2e` |
| `integration` | 需要真实 Token 或网络，未配置时跳过 | `test_integration.py` | 手工，CI 不跑 |

新增更重的层时，在 `TEST_LAYERS` 加一行；治理测试会校验登记项不指向已删除的文件。

## 3. 目录与约定

- 测试放在 [`tests/`](../tests) 顶层，命名 `test_<被测模块>.py`；一个文件对应一个模块或一条需求。
- 共享 fixture 一律进 [`tests/conftest.py`](../tests/conftest.py)：`fixtures_dir` / `mock_tushare_dir` /
  `load_mock_response` / `sample_stock_code` / `tmp_output_dir`。
- `_isolate_env_file` 是 autouse fixture，会把 `config.__file__` 指到临时目录，
  **防止测试读到真实 `.env`**；不要把 token 写进测试或 fixture。
- 产出文件一律写进 `tmp_path`（或用 `tmp_output_dir`），禁止污染仓库的 `output/`。
- 测试里不要 `time.sleep`；需要「时间」时注入固定值。

## 4. 运行方式

```bash
make verify     # 与 CI 等价：lint + 全量测试 + 覆盖率门禁（提交前跑这个）
make test       # 全量测试，无覆盖率门禁
make cov        # 全量测试 + 覆盖率报告与门禁
make unit       # 只跑快层，日常迭代用
make trace      # 只跑需求/测试治理一致性门禁
```

单文件与筛选：`.venv/bin/python -m pytest tests/test_periods.py -q`、`-k 关键字`、`-x` 遇到第一个失败即停。

## 5. 覆盖率

基线（2026-09-20，`main` = `02a2dc1`）：**76.77%**（10717 语句 / 2490 未覆盖），1389 passed / 3 skipped，约 63s。

- 门禁：`--cov-fail-under=74`（`make cov` 与 CI 都启用）。低于 74% 直接失败。
- 新增能力应当**提高**覆盖率；确因「一次性脚本」不宜补测的模块，在
  [`docs/requirements/ledger.md`](requirements/ledger.md) 的 Inbox 登记豁免理由，而不是默默压门禁。
- 已知洼地（同基线）：`scripts/valuation_engine.py` 34%、`scripts/portfolio_engine.py` 47%、
  `scripts/split_data_pack.py` 45%、`scripts/value_analysis_engine.py` 65%、
  `scripts/results/manifest.py` 74%，以及 0% 的 `scripts/report_to_html.py` /
  `scripts/md_to_mobile_html.py` / `scripts/generate_available_fields.py`。

## 6. 合同测试

接口一旦被下游消费，就要有一条合同测试锁住「两边不会各改各的」：

- **三方一致**：`scripts/results/schema.py` 的 `RESULT_TYPE_CONTRACTS` ↔ `context.py` 的 `MODULE_CONFIG` ↔
  `shared/qualitative/references/output_schema.md` 提示词参数表。见 `tests/test_qualitative_consumers.py`。
- **哈希与原子性**：manifest 的输入 / 产物摘要校验、`resolve_qualitative` 的「要么全用新结果，要么整体退回」。
- **路径与命名契约**：`pdf_sections_{period}.json`、`runs/{run_id}/run.json`、`latest.json` 等布局一旦写入文档，
  就要有测试断言，否则文档会先腐烂。

## 7. 需求 ↔ 测试追溯

- 测试文件里用注释声明归属，关键条款写 `AC-n`：

  ```python
  # 覆盖需求：REQ-001（定期报告发现与下载）—— AC-1 四类发现与 secCode 过滤、AC-6 单条失败不中断整批
  ```

- 治理门禁 [`tests/test_requirement_traceability.py`](../tests/test_requirement_traceability.py) 强制：
  - 测试引用的 `REQ-NNN` 必须真实存在（防悬空引用）；
  - 状态为 `implemented` / `verified` 的需求必须至少被一个测试文件引用；
  - 台账与 `REQ-*.md` 文件集合、状态必须一致。

## 8. 完成定义（测试部分）

一条需求的测试只有在下面全部成立时才算完成：

- [ ] 每条 `AC-n` 都有对应断言，失败路径也有
- [ ] 全 mock，本地 `make verify` 通过，覆盖率不低于门禁
- [ ] 文件里标注了 `# 覆盖需求：REQ-NNN` 与覆盖到的 `AC-n`
- [ ] 没有为了通过而放宽的断言、`pytest.mark.skip` 或空 `except`
- [ ] 公共接口变更时同步更新了合同测试

## 9. 反模式

| 反模式 | 为什么不行 | 替代做法 |
|--------|------------|----------|
| 测试断言复述实现里的字面量 | 实现改了就一起改，等于没测 | 断言外部可观察行为与边界 |
| 真网络 / 真 Token | CI 会不稳定，token 会进日志 | mock，必要的手工验证标 `integration` |
| 修改验收标准让测试通过 | 把「没做完」变成「做完了」 | 需求变更走 [`docs/requirements/README.md`](requirements/README.md) §7 |
| 一个测试文件覆盖十条需求 | 追溯形同虚设，失败定位困难 | 按模块拆分，按需求标注 |
| 为了覆盖率写无断言测试 | 覆盖率虚高，缺陷照旧 | 覆盖率是下限，不是目标 |
