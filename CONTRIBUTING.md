# 贡献指南 (Contributing Guide)

感谢你考虑为本项目做出贡献。本指南说明开发环境、分支模型、提交规范与 PR 流程。

## 目录

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

`main` 分支受保护，**所有改动必须通过 Pull Request 合入**，禁止直接 push。

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
git checkout main
git pull
git checkout -b feat/your-change
# ... 修改并提交 ...
git push -u origin feat/your-change
gh pr create --fill
```

## 提交规范

提交信息遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：

```
<type>(<scope>): <subject>

<body>

<footer>
```

**type**：`feat` / `fix` / `docs` / `refactor` / `test` / `chore` / `perf` / `build` / `ci`

**scope**（可选，建议使用）：`results` / `value` / `turtle` / `valuation` / `portfolio` / `collector` / `pdf` / `screener` / `schema` / `deps` 等。

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

1. 从最新 `main` 切出主题分支。
2. 保持 PR 聚焦：一个 PR 解决一个问题。较大的改动请拆分为可独立审阅的 PR。
3. 填写 PR 模板（仓库会自动加载）。
4. 确保本地验证通过：
   ```bash
   .venv/bin/python -m pytest -q
   .venv/bin/python -m compileall -q scripts tests
   git diff --check
   ```
5. 至少完成一次自查后再请求 review；CI 通过且至少 1 个 review 批准后才能合并。
6. 合并前解决所有 review 评论，保持分支与 `main` 同步。
7. 使用 **Squash and merge**，确保 `main` 的历史保持线性且信息清晰。

## CI 与 Review

每个 PR 会自动运行 [CI](.github/workflows/ci.yml)，包含：

| 检查 | 内容 |
|------|------|
| `pytest (3.10)` / `pytest (3.12)` | 全量测试 |
| `lint` | 编译检查与空白/冲突标记检查 |
| `pr-title` | PR 标题符合 Conventional Commits，且不超过 72 字符 |
| `ci-success` | 汇总以上检查，作为分支保护唯一必需的状态检查 |

合并条件（由分支保护强制）：

- `ci-success` 通过；
- 至少 1 个 review 批准，且 CODEOWNERS 指定的审阅人已批准；
- 所有 review 对话已解决；
- 分支与 `main` 同步（`strict` 模式）。

## 管理员与分支保护

管理员可在保护规则中直接合并 PR，并绕过必需检查与 review。

- **管理员名单**：编辑 [`.github/admins.yml`](.github/admins.yml)，在 `admins` 下追加 `- username` 即可，无需改代码。
- **应用规则**：进入 GitHub 的 Actions → **Setup Branch Protection** → Run workflow，按需设置 review 数、是否限制管理员、是否 dry-run，然后运行。
- **所需密钥**：需要仓库 Secret `ADMIN_TOKEN`，值为对仓库有 admin 权限的 Personal Access Token（`repo` + `admin:repo_hook`，或细粒度令牌的 Administration 写权限）。
- **同步 CODEOWNERS**：新增管理员后，请同时把用户名加到 [`.github/CODEOWNERS`](.github/CODEOWNERS)，用于指定审阅人。

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
.venv/bin/python -m pytest tests/ -q
```

## 文档

- 面向用户的改动更新 `README.md`
- 架构或数据流变化更新 `docs/ARCHITECTURE.md`
- 行为变更记录到 `CHANGELOG.md` 的 `[Unreleased]` 段
- 命令、参数、schema 变更需同步更新对应策略文档与模板

## Review 检查清单

审阅者与作者共同确认：

- [ ] 改动目标清晰，范围聚焦，无无关文件
- [ ] 与现有架构和约定一致
- [ ] 测试覆盖新行为与失败路径，且全部通过
- [ ] 无硬编码密钥、调试输出或临时代码
- [ ] 错误信息可操作，不会静默失败
- [ ] 文档、CHANGELOG 与合同测试已同步
- [ ] 不破坏向后兼容；如有破坏性变更已明确标注

## 行为准则

参与本项目即表示你同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
