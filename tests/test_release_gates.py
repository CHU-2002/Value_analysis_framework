"""流程三道门的单测：PR 描述守卫、独立验收门禁、main 批量回归门禁。

覆盖需求：REQ-006（需求-测试-开发流程与三道门）—— AC-2 研发自测必填、
AC-3 独立验收报告、AC-4 批量回归阈值。
"""

import acceptance_gate
import pr_body_guard
import regression_gate

FEATURE_BODY = """## 变更概述

新增 run-store 台账。

## 需求编号

REQ-003

## 研发自测（手工）

- 验了 AC-3：原地覆盖源文件后快照 sha256 不变
- 命令：python -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('a').read_bytes()).hexdigest())"
- 结果：覆盖前与覆盖后哈希一致，run 仍可 validate

## 验收报告

不适用
"""

VERIFICATION_FRONT = """---
batch: 2026-09-20-REQ-005
date: 2026-09-20
reviewer: independent-agent
independence: independent
requirements: REQ-005
base: 6c974ab
full-suite: 见正文
---

## 独立验收声明

无上下文的独立 agent，未参与实现。

## 全量测试

1389 passed, 3 skipped，覆盖率 76.77%。
"""


def _report(tmp_path, body):
    path = tmp_path / "2026-09-20-REQ-005.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_pr_body_guard_flags_empty_sections():
    problems = pr_body_guard.evaluate("## 变更概述\n\n做了点事。\n", "develop")
    assert any("需求编号" in problem for problem in problems)
    assert any("研发自测" in problem for problem in problems)


def test_pr_body_guard_accepts_filled_feature_pr():
    assert pr_body_guard.evaluate(FEATURE_BODY, "develop") == []


def test_pr_body_guard_ignores_placeholder_html_comments():
    body = "## 需求编号\n\nREQ-003\n\n## 研发自测（手工）\n\n<!-- 待填 -->\n"
    problems = pr_body_guard.evaluate(body, "develop")
    assert any("研发自测" in problem for problem in problems)


def test_pr_body_guard_requires_verification_report_for_main():
    problems = pr_body_guard.evaluate(FEATURE_BODY.replace(
        "不适用", "见 docs/verification/2026-09-20-REQ-003.md"
    ), "main")
    assert problems == []
    problems = pr_body_guard.evaluate(FEATURE_BODY, "main")
    assert any("验收报告" in problem for problem in problems)


def test_acceptance_gate_requires_a_report():
    problems = acceptance_gate.evaluate("## 需求编号\n\nREQ-005\n", [])
    assert any("验收报告" in problem for problem in problems)


def test_acceptance_gate_requires_requirement_ids():
    problems = acceptance_gate.evaluate("## 变更概述\n\n没有需求编号\n", [])
    assert any("REQ-NNN" in problem for problem in problems)


def test_acceptance_gate_flags_unchecked_acceptance_criteria(tmp_path):
    acs = acceptance_gate.requirement_ac_ids("REQ-005")
    assert acs, "REQ-005 应当有验收标准"
    checked = "\n".join(
        f"- [x] **AC-{ac}**：符合预期" for ac in acs if ac != acs[0]
    )
    report = _report(tmp_path, VERIFICATION_FRONT + "\n## 逐条验收\n\n" + checked + "\n")
    problems = acceptance_gate.evaluate("REQ-005\n", [report])
    assert any(f"AC-{acs[0]}" in problem for problem in problems)


def test_acceptance_gate_passes_when_report_is_complete(tmp_path):
    acs = acceptance_gate.requirement_ac_ids("REQ-005")
    checked = "\n".join(f"- [x] **AC-{ac}**：符合预期" for ac in acs)
    report = _report(tmp_path, VERIFICATION_FRONT + "\n## 逐条验收\n\n" + checked + "\n")
    assert acceptance_gate.evaluate("REQ-005\n", [report]) == []


def test_acceptance_gate_requires_independence(tmp_path):
    report = _report(tmp_path, VERIFICATION_FRONT.replace(
        "independence: independent", "independence: implementer"
    ))
    problems = acceptance_gate.evaluate("REQ-005\n", [report])
    assert any("independence" in problem for problem in problems)


def test_acceptance_gate_skips_report_for_docs_only_pr():
    paths = ["docs/requirements/ledger.md", "docs/requirements/REQ-003-x.md", "CHANGELOG.md"]
    assert acceptance_gate.is_docs_only(paths)
    assert acceptance_gate.evaluate("REQ-003\n", [], paths) == []


def test_acceptance_gate_does_not_skip_when_code_changed():
    paths = ["docs/requirements/ledger.md", "scripts/runs.py"]
    assert not acceptance_gate.is_docs_only(paths)
    assert acceptance_gate.evaluate("REQ-003\n", [], paths)


def test_acceptance_gate_unions_multiple_reports(tmp_path):
    acs = acceptance_gate.requirement_ac_ids("REQ-005")
    half, rest = acs[:2], acs[2:]
    first = tmp_path / "a.md"
    first.write_text(
        VERIFICATION_FRONT.replace("requirements: REQ-005", "requirements: REQ-005")
        + "\n## 逐条验收\n\n"
        + "\n".join(f"- [x] **AC-{ac}**：符合预期" for ac in half)
        + "\n",
        encoding="utf-8",
    )
    second = tmp_path / "b.md"
    second.write_text(
        "---\nreviewer: independent-agent\nindependence: independent\n"
        "requirements: REQ-005\nfull-suite: 见正文\n---\n\n## 逐条验收\n\n"
        + "\n".join(f"- [x] **AC-{ac}**：符合预期" for ac in rest)
        + "\n",
        encoding="utf-8",
    )
    assert acceptance_gate.evaluate("REQ-005\n", [first, second]) == []


def test_acceptance_gate_requires_recorded_full_suite_result(tmp_path):
    report = _report(tmp_path, VERIFICATION_FRONT.replace("1389 passed, 3 skipped", "跑过了"))
    problems = acceptance_gate.evaluate("REQ-005\n", [report])
    assert any("全量测试结果" in problem for problem in problems)


def test_regression_gate_below_threshold_passes():
    subjects = ["feat(a): one", "feat(b): two"]
    assert regression_gate.evaluate([], subjects, 3) == []


def test_regression_gate_blocks_when_threshold_reached_without_record():
    subjects = ["feat(a): one", "feat(b): two", "feat(c): three"]
    problems = regression_gate.evaluate([], subjects, 3)
    assert len(problems) == 1 and "累积" in problems[0]


def test_regression_gate_counts_only_feature_merges():
    assert regression_gate.FEATURE_RE.match("feat(x): y")
    assert regression_gate.FEATURE_RE.match("feat!: breaking")
    assert not regression_gate.FEATURE_RE.match("docs(x): y")
    assert not regression_gate.FEATURE_RE.match("fix(x): y")


def test_regression_gate_counts_merge_commits_as_features():
    """特性分支用 merge（非 squash）合入时，也要算作一个特性。"""
    subjects = [
        "feat(a): one",
        "Merge pull request #12 from feat/b",
        "docs(c): three",
        "fix(d): four",
    ]
    assert regression_gate.count_features(subjects) == 2
    assert regression_gate.select_features(subjects) == [
        "feat(a): one",
        "Merge pull request #12 from feat/b",
    ]


def test_regression_gate_loads_records_and_picks_latest(tmp_path):
    (tmp_path / "2026-09-01.md").write_text(
        "---\ndate: 2026-09-01\ncovered-until: aaa\n---\n", encoding="utf-8"
    )
    (tmp_path / "2026-09-20.md").write_text(
        "---\ndate: 2026-09-20\ncovered-until: bbb\n---\n", encoding="utf-8"
    )
    (tmp_path / "TEMPLATE.md").write_text(
        "---\ndate: 2099-01-01\ncovered-until: zzz\n---\n", encoding="utf-8"
    )
    records = regression_gate.load_records(tmp_path)
    assert len(records) == 2, "模板不应被当作记录"
    assert regression_gate.latest_record(records)["covered-until"] == "bbb"


def test_acceptance_gate_scopes_acs_per_requirement(tmp_path):
    """回归：A 需求的未打勾 AC 不得连累 B 需求的同号 AC。"""
    report = tmp_path / "batch.md"
    report.write_text(
        "---\nreviewer: independent-agent\nindependence: independent\n"
        "requirements: REQ-003, REQ-005\nfull-suite: 见正文\n---\n\n"
        "1389 passed\n\n"
        "### REQ-003 台账\n\n- [x] **AC-1**：成立\n\n"
        "### REQ-005 文档\n\n- [ ] **AC-1**：不成立\n",
        encoding="utf-8",
    )
    problems = acceptance_gate.evaluate("REQ-003, REQ-005\n", [report])
    assert not any("REQ-003 的 AC-1" in problem for problem in problems), problems
    assert any("REQ-005 的 AC-1" in problem for problem in problems), problems


def test_acceptance_gate_requires_per_requirement_section(tmp_path):
    report = tmp_path / "batch.md"
    report.write_text(
        "---\nreviewer: r\nindependence: independent\n"
        "requirements: REQ-003, REQ-005\nfull-suite: 见正文\n---\n\n1389 passed\n\n"
        "### REQ-003 台账\n\n- [x] **AC-1**：成立\n",
        encoding="utf-8",
    )
    problems = acceptance_gate.evaluate("REQ-003, REQ-005\n", [report])
    assert any("没有 REQ-005 的逐条验收内容" in problem for problem in problems), problems


def test_regression_record_must_not_be_an_empty_shell(tmp_path):
    shell = tmp_path / "2026-09-21.md"
    shell.write_text(
        "---\ndate: 2026-09-21\ncovered-until: abc123\nreviewer: someone\n"
        "full-suite: 1436 passed\n---\n\n# 回归\n\n## 全量测试\n\n1436 passed\n",
        encoding="utf-8",
    )
    records = regression_gate.load_records(tmp_path)
    problems = regression_gate.validate_record(records[0])
    assert any("逐条验收" in problem for problem in problems), problems


def test_regression_record_accepts_a_complete_one(tmp_path):
    good = tmp_path / "2026-09-21.md"
    good.write_text(
        "---\ndate: 2026-09-21\ncovered-until: abc123\nreviewer: someone\n"
        "full-suite: 1436 passed\n---\n\n# 回归\n\n## 全量测试\n\n1436 passed\n\n"
        "## 逐条验收\n\n- [x] **AC-1**：通过\n",
        encoding="utf-8",
    )
    records = regression_gate.load_records(tmp_path)
    assert regression_gate.validate_record(records[0]) == []
