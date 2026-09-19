"""测试 scope 工具的单测（登记表解析、渲染、预算常量）。

覆盖需求：REQ-006（需求-测试-开发流程与三道门）—— AC-5 整体 scope 登记与预算。

说明：不测 ``collect_cases_via_pytest`` 与 CLI ``main``——它们会再次启动 pytest，
在 pytest 里递归调用没有意义；真实校验由 CI 的 `test-scope` 作业执行
（`python scripts/test_scope.py --check`）。
"""

import test_scope


def test_collect_scope_covers_all_test_files_with_expected_fields():
    entries = test_scope.collect_scope()
    assert entries, "至少应采集到一支测试文件"
    for entry in entries:
        assert set(entry) == {"file", "reqs", "layer", "target", "cases"}
        assert entry["file"].startswith("tests/test_")
        assert entry["reqs"], "每支文件都要有归属（需求编号或「基线」）"
        assert entry["layer"] in test_scope.LAYERS
        assert entry["cases"] >= 0


def test_collect_scope_maps_requirement_notes():
    by_file = {entry["file"]: entry for entry in test_scope.collect_scope()}
    assert "REQ-006" in by_file["tests/test_release_gates.py"]["reqs"]
    assert by_file["tests/test_release_gates.py"]["layer"] == "unit"


def test_collect_scope_marks_unattributed_files_as_baseline():
    by_file = {entry["file"]: entry for entry in test_scope.collect_scope()}
    assert by_file["tests/test_config.py"]["reqs"] == "基线"


def test_count_test_functions_counts_only_test_defs(tmp_path):
    sample = tmp_path / "test_sample.py"
    sample.write_text(
        "def helper():\n    pass\n\n"
        "def test_one():\n    pass\n\n"
        "class TestX:\n"
        "    def test_two(self):\n        pass\n",
        encoding="utf-8",
    )
    assert test_scope.count_test_functions(sample) == 2


def test_count_test_functions_survives_syntax_error(tmp_path):
    broken = tmp_path / "test_broken.py"
    broken.write_text("def test_x(:\n", encoding="utf-8")
    assert test_scope.count_test_functions(broken) == 0


def test_registered_files_parses_table_rows():
    text = (
        "| 测试文件 | 归属需求 |\n"
        "|----------|----------|\n"
        "| `tests/test_a.py` | REQ-003 |\n"
        "\n"
        "| `tests/test_b.py` | 基线 |\n"
    )
    assert test_scope.registered_files(text) == {"tests/test_a.py", "tests/test_b.py"}


def test_render_includes_budget_and_every_entry():
    entries = test_scope.collect_scope()
    rendered = test_scope.render(entries, collected=1234)
    assert str(test_scope.MAX_TEST_FILES) in rendered
    assert "1234" in rendered
    for entry in entries:
        assert entry["file"] in rendered


def test_budget_constants_are_sane():
    entries = test_scope.collect_scope()
    assert test_scope.MAX_TEST_FILES >= len(entries), "预算不应低于当前文件数"
    assert test_scope.MAX_COLLECTED_CASES >= 1400, "预算不应低于当前用例数"
