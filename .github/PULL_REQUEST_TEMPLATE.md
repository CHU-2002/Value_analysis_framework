<!--
感谢提交 Pull Request！请填写以下内容，帮助审阅者快速理解改动。
标题请遵循 Conventional Commits，例如：feat(results): add artifact hash verification
-->

## 变更概述

<!-- 用一两句话说明这个 PR 做了什么、为什么需要 -->

## 关联 Issue

<!-- 如 Closes #123 / Refs #456，没有则填“无” -->

## 改动类型

- [ ] `feat` 新功能
- [ ] `fix` 缺陷修复
- [ ] `docs` 文档
- [ ] `refactor` 重构（不改变行为）
- [ ] `test` 测试
- [ ] `chore` / `ci` / `build` 杂项
- [ ] 破坏性变更（请在下方说明）

## 主要改动

<!-- 按文件或模块列出关键改动 -->

-
-

## 验证方式

<!-- 说明如何验证，附上命令与结果 -->

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q scripts tests
git diff --check
```

## 自查清单

- [ ] 改动聚焦，未包含无关文件
- [ ] 已补充或更新测试，且全部通过
- [ ] 已更新相关文档 / CHANGELOG
- [ ] 无硬编码密钥、调试输出或临时代码
- [ ] 未破坏向后兼容（如有破坏性变更已明确标注）

## 破坏性变更

<!-- 如有，描述影响范围与迁移方式；没有则填“无” -->

## 补充信息

<!-- 截图、设计取舍、后续计划等 -->
