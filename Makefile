# 本地开发入口：与 CI 对齐的校验命令。用法：make help
PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python)
COV_MIN ?= 74

.PHONY: help test unit cov lint trace verify clean-pyc

help:  ## 显示可用目标
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

test:  ## 全量测试（无覆盖率门禁）
	$(PYTHON) -m pytest -q

unit:  ## 只跑快层（跳过 contract / e2e / integration）
	$(PYTHON) -m pytest -q -m "not contract and not e2e and not integration"

cov:  ## 全量测试 + 覆盖率门禁（与 CI 一致）
	$(PYTHON) -m pytest -q --cov=scripts --cov-report=term-missing:skip-covered --cov-fail-under=$(COV_MIN)

lint:  ## 编译检查 + 空白/冲突标记检查
	$(PYTHON) -m compileall -q scripts tests
	git diff --check

trace:  ## 只跑需求/测试治理一致性门禁
	$(PYTHON) -m pytest -q tests/test_requirement_traceability.py

verify: lint cov  ## 合并前一键校验（等价于 CI 的 test + lint）

clean-pyc:  ## 清理 __pycache__
	find scripts tests -name '__pycache__' -type d -prune -exec rm -rf {} +
