"""Shared test fixtures for Value Analysis Framework."""

import json
import os
import sys

import pytest

# Add scripts/ to path so we can import from there
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
MOCK_TUSHARE_DIR = os.path.join(FIXTURES_DIR, "mock_tushare_responses")

# 测试分层登记表：按文件归类，未登记的文件一律归入 DEFAULT_LAYER。
# 更重的层（contract / e2e / integration）请在此登记；登记项由
# tests/test_requirement_traceability.py 校验（不得指向已删除的文件）。
# 策略与用法见 docs/TESTING.md，marker 定义见 pytest.ini。
TEST_LAYERS = {
    "test_integration.py": "integration",
    "test_results_pipeline.py": "e2e",
    "test_qualitative_consumers.py": "contract",
}
DEFAULT_LAYER = "unit"


def pytest_collection_modifyitems(config, items):
    """按文件给每个测试自动附加分层 marker，避免逐个测试人工标注。"""
    for item in items:
        path = getattr(item, "path", None) or getattr(item, "fspath", None)
        filename = os.path.basename(str(path))
        layer = TEST_LAYERS.get(filename, DEFAULT_LAYER)
        item.add_marker(getattr(pytest.mark, layer))


@pytest.fixture
def fixtures_dir():
    """Path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def mock_tushare_dir():
    """Path to the mock Tushare API responses directory."""
    return MOCK_TUSHARE_DIR


@pytest.fixture
def load_mock_response():
    """Factory fixture to load a mock Tushare API response by filename."""

    def _load(filename: str) -> dict:
        filepath = os.path.join(MOCK_TUSHARE_DIR, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    return _load


@pytest.fixture
def sample_stock_code():
    """Standard test stock code (Yili 伊利股份)."""
    return "600887.SH"


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Temporary output directory for test file generation."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return output_dir


# Mark integration tests that require live API
integration = pytest.mark.skipif(
    not os.environ.get("TUSHARE_TOKEN"),
    reason="TUSHARE_TOKEN not set, skipping integration test",
)


@pytest.fixture(autouse=True)
def _isolate_env_file(monkeypatch, tmp_path):
    """Prevent _load_env_file from finding the real .env during tests."""
    import config as config_mod

    monkeypatch.setattr(config_mod, "__file__", str(tmp_path / "scripts" / "config.py"))
