# 本地开发入口：与 CI 对齐的校验命令。用法：make help
PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python)
COV_MIN ?= 74

.PHONY: help test unit cov lint trace scope scope-write scope-check gates verify clean-pyc

help:  ## 显示可用目标
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

test:  ## 全量测试（无覆盖率门禁）
	$(PYTHON) -m pytest -q

unit:  ## 只跑快层（跳过 contract / e2e / integration）
	$(PYTHON) -m pytest -q -m "not contract and not e2e and not integration"

cov:  ## 全量测试 + 覆盖率门禁（与 CI 一致）
	$(PYTHON) -m pytest -q --cov=scripts --cov-report=term-missing:skip-covered --cov-fail-under=$(COV_MIN)

lint:  ## 编译检查 + 空白/冲突标记检查
	$(PYTHON) -m compileall -q scripts tests
	git diff --check

trace:  ## 需求↔测试追溯门禁
	$(PYTHON) -m pytest -q tests/test_requirement_traceability.py

scope:  ## 打印整体测试 scope 与预算使用情况
	$(PYTHON) scripts/test_scope.py --report

scope-write:  ## 重新生成 docs/TEST_SCOPE.md（增删测试文件后必跑）
	$(PYTHON) scripts/test_scope.py --write

scope-check:  ## 校验测试 scope 登记表与预算
	$(PYTHON) scripts/test_scope.py --check

gates:  ## 说明三道门怎么在本地自检（正式校验在 CI，需要 PR 上下文）
	@echo "三道门需要 PR 上下文，CI 里自动跑；本地自检："
	@echo "  PR 描述：$(PYTHON) scripts/pr_body_guard.py --body-file <描述文件> --base develop"
	@echo "  独立验收：$(PYTHON) scripts/acceptance_gate.py --body-file <描述文件>"
	@echo "  批量回归：$(PYTHON) scripts/regression_gate.py --check"

verify: lint cov trace scope-check  ## 提交前必跑（等价于 CI 的检查项）
	@echo "本地校验全部通过。"

clean-pyc:  ## 清理 __pycache__
	find scripts tests -name '__pycache__' -type d -prune -exec rm -rf {} +
