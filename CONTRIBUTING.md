# 贡献指南 (Contributing Guide)

感谢你考虑为本项目做出贡献。本指南说明开发环境、分支模型、提交规范与 PR 流程。

## 目录

- [需求、测试与开发](#需求测试与开发)
- [开发环境](#开发环境)
- [分支模型](#分支模型)
- [提交规范](#提交规范)
- [Pull Request 流程](#pull-request-流程)
- [CI 与 Review](#ci-与-review)
- [管理员与分支保护](#管理员与分支保护)
- [代码风格](#代码风格)
- [测试要求](#测试要求)
- [文档](#文档)
- [Review 检查清单](#review-检查清单)

## 开发环境

```bash
git clone https://github.com/CHU-2002/Value_analysis_framework.git
cd Value_analysis_framework
bash init.sh
```

- Python >= 3.10
- 运行测试无需 Tushare Token（全部使用 mock 数据）
- 仅在需要实时采集数据时配置 `TUSHARE_TOKEN`

## 分支模型

`main` 受保护，**所有改动必须通过 Pull Request 合入**，禁止直接 push。

工作方式是**一个特性一条特性分支**：该特性的子 PR 都开向这条特性分支（目标分支写特性分支名），
特性做完后整支**直接合入 `main`**，并删除特性分支。不维护长期集成分支。
判据与三道门见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

| 分支前缀 | 用途 | 示例 |
|----------|------|------|
| `feat/` | 新功能 | `feat/portfolio-tax-aware-rebalance` |
| `fix/` | 缺陷修复 | `fix/resolver-stale-artifact` |
| `docs/` | 文档 | `docs/architecture-refresh` |
| `refactor/` | 重构（不改行为） | `refactor/results-context-budget` |
| `test/` | 仅测试 | `test/evidence-index-edge-cases` |
| `chore/` | 构建、依赖、杂项 | `chore/bump-dependencies` |

工作流：

```bash
# 1) 起一条特性分支（一个特性一条）
git checkout main
git pull
git checkout -b feat/periodic-update
git push -u origin feat/periodic-update

# 2) 子任务：从特性分支切出，PR 开向特性分支
git checkout feat/periodic-update
git checkout -b feat/periodic-update-download
# ... 修改并提交 ...
make verify                      # 提交前自检：本地可复现的全部门禁
git push -u origin feat/periodic-update-download
gh pr create --base feat/periodic-update --fill

# 3) 特性做完：特性分支 → main，附独立验收报告（门②）
gh pr create --base main --head feat/periodic-update --fill
```

## 提交规范

提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：

```
<type>(<scope>): <subject>

<body>

<footer>
```

**type**：`feat` / `fix` / `docs` / `refactor` / `test` / `chore` / `perf` / `build` / `ci`

**scope**（可选，建议使用）：`results` / `value` / `valuation` / `portfolio` / `collector` / `pdf` / `screener` / `schema` / `deps` 等。

要求：

- subject 使用祈使语气、不超过 72 字符、句末不加句号
- body 说明**为什么**改，而不是复述改了什么
- 破坏性变更使用 `!` 并在 footer 写 `BREAKING CHANGE:`
- 一个提交只做一件事

示例：

```
feat(results): enforce artifact hash verification in resolver

The manifest recorded input hashes but not prepared artifact hashes, so a
tampered evidence index could still be consumed. Record SHA-256 for every
prepared artifact and reject mismatches during resolution.

Closes #42
```

## Pull Request 流程

1. 子任务从**特性分支**切出，PR 的目标分支写**特性分支**；只有「特性分支 → `main`」的 PR
   才把目标写成 `main`，并必须附独立验收报告。
2. 保持 PR 聚焦：一个 PR 解决一个问题。较大的改动请拆分为可独立审阅的 PR。
3. 填写 PR 模板（仓库会自动加载）。
4. 确保本地验证通过。`make verify` 覆盖 CI 里**可在本地复现**的检查项
   （编译/空白检查 + 全量测试 + 覆盖率门禁 + 追溯门禁 + 测试 scope 检查）；
   依赖 PR 上下文的检查（`pr-title`、`pr-body`、`acceptance-gate`、`regression-gate`）
   只能由 CI 执行，本地预演方式见 `make gates`。
   ```bash
   make verify
   ```
5. 至少完成一次自查后再请求 review；CI 通过且至少 1 个 review 批准后才能合并。
6. 合并前解决所有 review 评论，保持分支与 `main` 同步。
7. 使用 **Squash and merge**，确保 `main` 的历史保持线性且信息清晰。

## CI 与 Review

每个 PR 会自动运行 [CI](.github/workflows/ci.yml)，包含：

CI 只有两个 job：一个干全部活，一个汇总（分支保护只认汇总）。

| job | 内容（按顺序） |
|-----|----------------|
| `ci` | ① 编译/空白检查（含相对基线的整段 diff）② 测试 scope 登记表与预算 ③ PR 标题（Conventional Commits，≤ 72 字符）④ PR 描述（需求编号 + 研发自测栏） ⑤ 独立验收报告（仅特性分支 → `main`）⑥ 批量回归记录（仅特性分支 → `main`） ⑦ **全量测试 + 覆盖率门禁（≥ 74%）** |
| `ci-success` | 汇总 `ci` 结果，作为分支保护唯一必需的状态检查 |

便宜的检查在前、全量测试在后：门禁不过就不用等测试。CI 默认只跑 Python 3.12（与开发环境一致），最低支持版本 3.10 在 Actions 里手动触发 `workflow_dispatch` 填 `python-version: "3.10"` 核验；只装 `requirements-test.txt`（测试真正 import 的最小集），并用 `pytest -n auto` 并行。

合并条件（由分支保护强制）：

- `ci-success` 通过（含 `pr-body`；「特性分支 → `main`」还含 `acceptance-gate` 与 `regression-gate`）；
- 至少 1 个 review 批准，且 CODEOWNERS 指定的审阅人已批准；
- 所有 review 对话已解决；
- 分支与 `main` 同步（`strict` 模式）。

## 需求、测试与开发

三者用需求编号连成闭环，各有独立权威文档：

| 部分 | 权威文档 | 你要交的产物 |
|------|----------|--------------|
| 需求 | [`docs/requirements/README.md`](docs/requirements/README.md) | `REQ-NNN` 条目 + [台账](docs/requirements/ledger.md) 一行，验收标准必须可判定 |
| 测试 | [`docs/TESTING.md`](docs/TESTING.md) | 覆盖新行为的测试，文件里标注 `# 覆盖需求：REQ-NNN` |
| 开发 | [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | 主题分支 + Conventional Commits + 聚焦的 PR；正文的「## 需求编号」小节必须真的填（正文提及不算，`pr_body_guard` 会拦） |

闭环：**需求登记 → 验收标准定稿 → 实现（PR）→ 测试追溯 → 逐条验收 → 台账状态推进**。
就绪定义（DoR）与完成定义（DoD）见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

[tests/test_requirement_traceability.py](tests/test_requirement_traceability.py) 在 CI 中强制这条链：
台账漏登记、编号对不上、已交付需求没有测试引用，都会直接失败。

## 管理员与分支保护

管理员可在保护规则中直接合并 PR，并绕过必需检查与 review。

- **管理员名单**：编辑 [`.github/admins.yml`](.github/admins.yml)，在 `admins` 下追加 `- username` 即可，无需改代码。
- **应用规则**：进入 GitHub 的 Actions → **Setup Branch Protection** → Run workflow，按需设置 review 数、是否限制管理员、是否 dry-run，然后运行。
- **所需密钥**：需要仓库 Secret `ADMIN_TOKEN`，值为对仓库有 admin 权限的 Personal Access Token（`repo` + `admin:repo_hook`，或细粒度令牌的 Administration 写权限）。
- **同步 CODEOWNERS**：新增管理员后，请同时把用户名加到 [`.github/CODEOWNERS`](.github/CODEOWNERS)，用于指定审阅人。
- **用户仓库与组织仓库的差异**：GitHub 仅允许组织仓库设置 push restrictions。用户仓库的管理员需在 Settings → Collaborators 里设为 **Admin**，再配合 `enforce_admins=false` 直接合并 PR；组织仓库则会自动把 `admins.yml` 写入 push restrictions。

## 代码风格

- 遵循 PEP 8，保持与现有文件一致
- 优先使用标准库；新增第三方依赖必须在 `requirements.txt` 中声明
- 函数与模块添加简洁 docstring，说明意图与边界
- 不要把机密（token、`.env`）写入代码或测试
- 涉及路径的代码使用 `pathlib.Path`，命令示例中路径加引号
- 除非必要，不添加注释；让命名和结构自解释

## 测试要求

- 新功能与缺陷修复必须附带测试
- 测试使用 mock 数据，不得依赖真实网络或 Token
- 覆盖正常路径、边界情况与失败路径
- 修改公共接口（schema、命令、解析器）时，同步更新合同测试

```bash
make verify   # 本地全部门禁：lint + 全量测试 + 覆盖率 + 追溯 + scope；日常迭代可用 make unit
```

额外要求：

- 覆盖率不得低于 74%（基线 76.8%）；门禁与基线见 [docs/TESTING.md](docs/TESTING.md) §6
- 测试文件用注释标注 `# 覆盖需求：REQ-NNN`，并写明覆盖到的 `AC-n`
- 「要么全用新结果，要么整体退回旧报告」等既有原则要有合同测试守护

## 文档

- 面向用户的改动更新 `README.md`
- 架构或数据流变化更新 `docs/ARCHITECTURE.md`
- 行为变更记录到 `CHANGELOG.md` 的 `[Unreleased]` 段
- 命令、参数、schema 变更需同步更新对应策略文档与模板

## Review 检查清单

审阅者与作者共同确认：

- [ ] 改动目标清晰，范围聚焦，无无关文件
- [ ] 与现有架构和约定一致
- [ ] 需求台账与状态已同步推进，测试里标注了 `REQ-NNN`
- [ ] 测试覆盖新行为与失败路径，且全部通过
- [ ] 无硬编码密钥、调试输出或临时代码
- [ ] 错误信息可操作，不会静默失败
- [ ] 文档、CHANGELOG 与合同测试已同步
- [ ] 不破坏向后兼容；如有破坏性变更已明确标注

## 行为准则

参与本项目即表示你同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
