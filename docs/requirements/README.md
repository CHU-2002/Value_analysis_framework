# 需求管理

本目录是项目的**需求唯一权威来源（single source of truth）**。任何「要做什么、做到什么程度、算不算做完」的问题，都以这里的条目为准；设计文档、Issue、PR 都是需求的**下游**。

配套文档：

- 测试策略与追溯规则：[`docs/TESTING.md`](../TESTING.md)
- 开发流程与就绪/完成定义：[`docs/DEVELOPMENT.md`](../DEVELOPMENT.md)
- 分支、提交、CI、评审约定：[`CONTRIBUTING.md`](../../CONTRIBUTING.md)

## 1. 三层结构

```
需求 (本目录)          设计 (docs/*.md)              任务 (GitHub Issue / PR)
要做什么、验收标准  →   怎么实现、取舍、接口      →   一次可评审的改动
REQ-NNN                 ARCHITECTURE / *_PLAN          issue → feat/* → PR
```

规矩只有一条：**需求写「要什么」，设计写「怎么做」，两者不互相复制。**
需求条目可以短，但验收标准必须可判定；设计可以长，但不改验收标准。

## 2. 目录内容

| 文件 | 作用 |
|------|------|
| `README.md` | 本文件：编号规则、状态机、优先级、操作步骤 |
| `TEMPLATE.md` | 需求条目模板，新建需求时复制 |
| `ledger.md` | **需求台账**：全部需求的单一索引与状态总览 |
| `REQ-NNN-*.md` | 单条需求：背景、验收标准、范围、追溯 |

`ledger.md` 与 `REQ-*.md` 的一致性由 [`tests/test_requirement_traceability.py`](../../tests/test_requirement_traceability.py) 强制校验，**台账漏登记或编号对不上会让 CI 失败**。

## 3. 编号规则

- 需求编号：`REQ-NNN`，三位十进制、单调递增、**永不复用**。删除的需求保留编号并置为 `rejected` / `superseded`。
- 验收标准编号：条目内 `AC-1`、`AC-2`…… 序号在本条需求内唯一。
- 分支与提交沿用 [Conventional Commits](../../CONTRIBUTING.md#提交规范)；PR 描述写 `REQ-NNN`，使 GitHub 能反查。

## 4. 状态机

| 状态 | 含义 | 允许的下一步 |
|------|------|--------------|
| `proposed` | 已登记，尚未确认要做 | `accepted` / `rejected` / `deferred` |
| `accepted` | 已确认要做，验收标准已定稿 | `in-progress` / `deferred` / `superseded` |
| `in-progress` | 有分支/PR 正在实现 | `implemented` / `deferred` / `superseded` |
| `implemented` | 代码已合入 `main`，**尚未**逐条验收 | `verified` / `in-progress`（验收不通过时回退） |
| `verified` | 逐条验收标准已核对通过，测试可追溯 | 终态（除非 `superseded`） |
| `deferred` | 本期不做，保留编号 | `accepted` |
| `rejected` | 决定不做 | 终态 |
| `superseded` | 被新需求取代，须写明 `supersedes` | 终态 |

硬性约束：

- `implemented` 与 `verified` 的需求**必须**在测试里有 `REQ-NNN` 引用，否则追溯测试失败。
- 状态只能「逐级前进」；要回退必须同时在条目里记一句原因。
- **状态与台账必须同一次改动内同步**，禁止只在代码里改而忘了台账。

## 5. 优先级

| 级别 | 判据 |
|------|------|
| `P0` | 阻断主流程 / 数据正确性问题 / 安全问题，需立即处理 |
| `P1` | 当前迭代目标，或已交付能力的必要补全 |
| `P2` | 有价值的改进，可排期 |
| `P3` | 想法与探索，不承诺时间 |

## 6. 新增一条需求

1. **先开 Issue**（`功能请求` 模板），在 Issue 里把背景和期望写清楚，拿到初步共识。
2. `cp docs/requirements/TEMPLATE.md docs/requirements/REQ-NNN-<slug>.md`，填 front matter 与验收标准。
   - 写不出**可判定**的验收标准，说明需求还没想清楚，停在 `proposed`。
3. 在 `ledger.md` 的「需求台账」表加一行，状态填 `proposed`。
4. 只做这一步也可以单独提 PR（`docs(req): register REQ-NNN`），让需求先被评审再写代码。
5. 需求被受理后 → `accepted`，并在 GitHub 建里程碑；开始编码 → `in-progress`。

## 7. 变更与废弃

- **修改验收标准**：属于需求变更，必须在条目内新增 `AC-n` 或显式标注被替换的旧条款，并同步更新关联测试。
  不允许偷偷改小标准让测试通过。若修改**收窄**了判据（例如原条款按字面不可满足），
  条目内必须留下变更记录：原条款原文、变更原因、新条款、批准人（需求 owner），
  并告知独立评审者复核——静默收窄视为违规。
- **范围扩大**：新开一条 `REQ-NNN`，用 `depends-on` 关联，不要把两条需求揉进一条。
- **放弃**：状态置 `superseded` 并填写 `supersedes` / `superseded-by`，或 `rejected` 并写明理由。

## 8. 与 GitHub 的映射

| 本目录 | GitHub |
|--------|--------|
| 需求 `REQ-NNN` | 一个 Issue，标题前缀 `[REQ-NNN]`，标签 `type:requirement` |
| 优先级 `P0`–`P3` | 标签 `priority:P0` … `priority:P3` |
| 领域 | 标签 `area:data` / `area:report` / `area:pdf` / `area:results` / `area:value` / `area:portfolio` / `area:screener` / `area:tooling` |
| 一次交付 | PR，标题 `feat(scope): ...`，正文写 `REQ-NNN`；合并后回填台账 `pr` 字段 |
| 一个阶段 | Milestone，与 `ledger.md` 的「里程碑」小节一一对应 |

GitHub 是**协作视图**，不是权威来源：Issue 可以先于需求存在，但代码合入前需求必须已登记。
Issue 在**验收通过后**才关闭（`verified`），不在 PR 合并时自动关闭——否则等于跳过验收。

## 9. 与测试、开发的接口

| 接口 | 约定 | 校验方式 |
|------|------|----------|
| 需求 → 测试 | 测试文件用注释 `# 覆盖需求：REQ-NNN` 声明归属；关键条款在注释里写 `AC-n` | `tests/test_requirement_traceability.py` |
| 需求 → 开发 | 分支名 `feat/<slug>`，PR 正文写 `REQ-NNN` | PR 模板 + 评审 |
| 开发 → 需求 | 合并后把状态推到 `implemented`；逐条验收后推到 `verified` 并关闭 Issue | 台账 review |

## 10. 当前状态

见 [`ledger.md`](ledger.md)。需求条目一览：

| ID | 标题 | 状态 |
|----|------|------|
| [REQ-001](REQ-001-periodic-report-discovery.md) | 定期报告发现与下载 | `verified` |
| [REQ-002](REQ-002-comparable-periods.md) | 同比可比期与按期次章节包 | `verified` |
| [REQ-003](REQ-003-run-history-ledger.md) | 分析迭代台账（run-store） | `verified` |
| [REQ-004](REQ-004-period-delta-analysis.md) | 定期报告增量更新分析与变化报告 | `verified` |
| [REQ-005](REQ-005-periodic-update-docs.md) | 增量更新文档与下游接线 | `implemented` |
| [REQ-006](REQ-006-requirement-test-dev-flow.md) | 需求-测试-开发流程与三道门 | `verified` |
