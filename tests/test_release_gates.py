"""流程三道门的单测：PR 描述守卫、独立验收门禁、main 批量回归门禁。

覆盖需求：REQ-006（需求-测试-开发流程与三道门）—— AC-2 研发自测必填、
AC-3 独立验收报告、AC-4 批量回归阈值。
REQ-006 任务 T2（门禁与治理工具加固）—— AC-1 需求编号小节必填、AC-4 空的逐条验收小节被拒、
AC-8 夹具不用已废弃分支名。
REQ-006 任务 T6（大特性拆子需求）—— 子需求的 AC 按小节核对、父需求的同号 AC 不被
子需求的 `AC-S.n` 顶替、未登记的子需求编号被拒。
"""

import subprocess

import acceptance_gate
import pr_body_guard
import regression_gate
import req_registry

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


def review_body(*req_ids):
    """收口 PR 的正文：推进到 verified 的编号 + 验收报告链接（acceptance_gate 只认这两个）。"""
    return (
        "## 需求编号\n\n" + ", ".join(req_ids) + "\n\n"
        "## 验收报告\n\n见 docs/verification/2026-09-20-batch.md\n"
    )


def test_pr_body_guard_flags_empty_sections():
    problems = pr_body_guard.evaluate("## 变更概述\n\n做了点事。\n", "feat/periodic-update")
    assert any("需求编号" in problem for problem in problems)
    assert any("研发自测" in problem for problem in problems)


def test_pr_body_guard_accepts_filled_feature_pr():
    assert pr_body_guard.evaluate(FEATURE_BODY, "feat/periodic-update") == []


def test_pr_body_guard_ignores_placeholder_html_comments():
    body = "## 需求编号\n\nREQ-003\n\n## 研发自测（手工）\n\n<!-- 待填 -->\n"
    problems = pr_body_guard.evaluate(body, "feat/periodic-update")
    assert any("研发自测" in problem for problem in problems)


def test_pr_body_guard_no_longer_requires_a_report_for_main():
    """评审改为按子需求收口：报告栏不再由 PR 描述守卫要求（那条判定在 acceptance_gate）。"""
    assert pr_body_guard.evaluate(FEATURE_BODY, "main") == []


def test_acceptance_gate_needs_no_report_without_a_status_promotion():
    """评审按子需求/大特性收口，不按 PR：没有状态推进就不看报告。"""
    assert acceptance_gate.evaluate("## 需求编号\n\nREQ-005\n", []) == []
    assert acceptance_gate.evaluate("## 需求编号\n\nREQ-005\n", [], set()) == []


def test_acceptance_gate_requires_requirement_ids():
    problems = acceptance_gate.evaluate("## 变更概述\n\n没有需求编号\n", [], {"REQ-005"})
    assert any("REQ-005" in problem and "批次" in problem for problem in problems)


def test_acceptance_gate_flags_unchecked_acceptance_criteria(tmp_path):
    acs = acceptance_gate.requirement_ac_ids("REQ-005")
    assert acs, "REQ-005 应当有验收标准"
    checked = "\n".join(
        f"- [x] **AC-{ac}**：符合预期" for ac in acs if ac != acs[0]
    )
    report = _report(tmp_path, VERIFICATION_FRONT + "\n## 逐条验收\n\n" + checked + "\n")
    problems = acceptance_gate.evaluate(review_body("REQ-005"), [report], {"REQ-005"})
    assert any(f"AC-{acs[0]}" in problem for problem in problems)


def test_acceptance_gate_passes_when_report_is_complete(tmp_path):
    acs = acceptance_gate.requirement_ac_ids("REQ-005")
    checked = "\n".join(f"- [x] **AC-{ac}**：符合预期" for ac in acs)
    report = _report(tmp_path, VERIFICATION_FRONT + "\n## 逐条验收\n\n" + checked + "\n")
    assert acceptance_gate.evaluate(review_body("REQ-005"), [report], {"REQ-005"}) == []


def test_acceptance_gate_requires_independence(tmp_path):
    report = _report(tmp_path, VERIFICATION_FRONT.replace(
        "independence: independent", "independence: implementer"
    ))
    problems = acceptance_gate.evaluate(review_body("REQ-005"), [report], {"REQ-005"})
    assert any("independence" in problem for problem in problems)


def test_acceptance_gate_only_looks_at_status_promotions():
    """只改台账的补录不推进状态 → 免报告；推进到 verified → 无论改了什么都要报告。"""
    assert acceptance_gate.evaluate("## 需求编号\n\nREQ-003\n", []) == []
    problems = acceptance_gate.evaluate("## 需求编号\n\nREQ-003\n", [], {"REQ-003"})
    assert any("验收报告" in problem for problem in problems)


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
    assert acceptance_gate.evaluate(review_body("REQ-005"), [first, second], {"REQ-005"}) == []


def test_acceptance_gate_requires_recorded_full_suite_result(tmp_path):
    report = _report(tmp_path, VERIFICATION_FRONT.replace("1389 passed, 3 skipped", "跑过了"))
    problems = acceptance_gate.evaluate(review_body("REQ-005"), [report], {"REQ-005"})
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
    problems = acceptance_gate.evaluate(review_body("REQ-003", "REQ-005"), [report], {"REQ-003", "REQ-005"})
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
    problems = acceptance_gate.evaluate(review_body("REQ-003", "REQ-005"), [report], {"REQ-003", "REQ-005"})
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


def test_acceptance_gate_batch_comes_from_the_requirement_section(tmp_path):
    """正文里提到「REQ-005 不在本批」不应把 REQ-005 卷进批次。"""
    checked = "\n".join(
        f"- [x] **AC-{ac}**：成立" for ac in acceptance_gate.requirement_ac_ids("REQ-003")
    )
    report = tmp_path / "batch.md"
    report.write_text(
        "---\nreviewer: r\nindependence: independent\n"
        "requirements: REQ-003\nfull-suite: 见正文\n---\n\n1440 passed\n\n"
        f"### REQ-003 台账\n\n{checked}\n\n"
        "### REQ-005 文档\n\n- [ ] **AC-1**：不成立\n",
        encoding="utf-8",
    )
    body = (
        "## 需求编号\n\nREQ-003\n\n"
        "## 验收报告\n\n见 docs/verification/2026-09-20-batch.md；"
            "REQ-005 本次不在批内：它未通过验收（见下文）。\n"
    )
    assert acceptance_gate.batch_requirements(body) == ["REQ-003"]
    assert acceptance_gate.evaluate(body, [report], {"REQ-003"}) == []


def test_acceptance_gate_falls_back_to_whole_body_without_the_section(tmp_path):
    body = "本次要交付 REQ-005，但没有写需求编号小节。\n"
    assert acceptance_gate.batch_requirements(body) == ["REQ-005"]
    problems = acceptance_gate.evaluate(body, [], {"REQ-005"})
    assert any("验收报告" in problem for problem in problems), problems


def test_acceptance_gate_fallback_strips_html_comments():
    """回退扫描也要剥注释：模板残留注释里的 REQ 不应被当成批次。"""
    body = (
        "## 需求编号\n\n<!-- 模板残留，提到 REQ-005 -->\n\n"
        "## 变更概述\n\n本批交付 REQ-006。\n"
    )
    assert acceptance_gate.batch_requirements(body) == ["REQ-006"]


# 故意构造一个未登记的需求编号：用拼接书写，避免治理门禁
# （tests/test_requirement_traceability.py 要求测试里出现的 REQ-NNN 必须真实存在）
# 把这条「不存在的编号」夹具本身判成悬空引用。
UNKNOWN_REQ = "REQ-" + "999"


def test_acceptance_gate_rejects_unknown_requirement_ids(tmp_path):
    """写一个不存在的 REQ 编号不能绕过 AC 校验。"""
    report = tmp_path / "batch.md"
    report.write_text(
        "---\nreviewer: r\nindependence: independent\n"
        f"requirements: {UNKNOWN_REQ}\nfull-suite: 见正文\n---\n\n1442 passed\n",
        encoding="utf-8",
    )
    problems = acceptance_gate.evaluate(review_body(UNKNOWN_REQ), [report], {UNKNOWN_REQ})
    assert any(UNKNOWN_REQ in problem and "不存在" in problem for problem in problems), problems


def test_pr_body_guard_rejects_prose_only_requirement_ids():
    """AC-1：正文提到 REQ-NNN 不能替代「## 需求编号」小节。"""
    body = (
        "## 变更概述\n\n顺便修了 REQ-003 的一个问题。\n\n"
        "## 研发自测（手工）\n\n- 手工跑了一遍，命令与输出见下，行为符合预期。\n"
    )
    problems = pr_body_guard.evaluate(body, "feat/periodic-update")
    assert any("需求编号" in problem for problem in problems), problems


def test_regression_record_rejects_an_empty_acceptance_section(tmp_path):
    """AC-4：小节存在但里面没有任何 AC 结论时也要拒。"""
    empty = tmp_path / "2026-09-22.md"
    empty.write_text(
        "---\ndate: 2026-09-22\ncovered-until: abc123\nreviewer: someone\n"
        "full-suite: 1458 passed\n---\n\n# 回归\n\n## 全量测试\n\n1458 passed\n\n"
        "## 逐条验收\n\n（待补）\n",
        encoding="utf-8",
    )
    problems = regression_gate.validate_record(regression_gate.load_records(tmp_path)[0])
    assert any("逐条验收" in problem and "空" in problem for problem in problems), problems


def test_acceptance_gate_ignores_ac_examples_in_quotes_and_code(tmp_path):
    """报告里字面举例「未打勾的 AC 长什么样」不该被判成结论（REQ-006 评审者踩过）。"""
    acs = acceptance_gate.requirement_ac_ids("REQ-005")
    checked = "\n".join(f"- [x] **AC-{ac}**：符合预期" for ac in acs)
    report = tmp_path / "batch.md"
    report.write_text(
        "---\nreviewer: r\nindependence: independent\n"
        "requirements: REQ-005\nfull-suite: 见正文\n---\n\n1440 passed\n\n"
        "## 逐条验收\n\n" + checked + "\n\n"
        "## 未通过 / 存疑项\n\n"
        "> 举例：未通过时写成这种形式（下面是引用里的例子，不是结论）\n"
        "> - [ ] **AC-1**：不成立\n\n"
        "```markdown\n- [ ] **AC-2**：不成立\n```\n\n"
        "行内示例：`- [ ] **AC-3**：不成立`\n",
        encoding="utf-8",
    )
    assert acceptance_gate.evaluate(review_body("REQ-005"), [report], {"REQ-005"}) == []


def test_acceptance_gate_still_detects_a_real_unchecked_ac(tmp_path):
    """容忍举例之后，真正的未打勾行仍必须被抓出。"""
    acs = acceptance_gate.requirement_ac_ids("REQ-005")
    lines = [f"- [x] **AC-{ac}**：符合预期" for ac in acs]
    lines[0] = f"- [ ] **AC-{acs[0]}**：不成立"
    report = tmp_path / "batch.md"
    report.write_text(
        "---\nreviewer: r\nindependence: independent\n"
        "requirements: REQ-005\nfull-suite: 见正文\n---\n\n1440 passed\n\n"
        "## 逐条验收\n\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    problems = acceptance_gate.evaluate(review_body("REQ-005"), [report], {"REQ-005"})
    assert any(f"AC-{acs[0]}" in problem and "未打勾" in problem for problem in problems), problems


# --- T6：大特性拆子需求（REQ-NNN.S） ---
# 故意用运行期拼接的假编号，避免治理门禁把夹具本身判成悬空引用。
DEMO_PARENT = "REQ-" + "903"
DEMO_SUB = DEMO_PARENT + ".1"

DEMO_REQ_DOC = """---
id: {parent}
title: 演示大特性
status: {parent_status}
priority: P2
owner: TBD
created: 2026-09-20
updated: 2026-09-20
---

# {parent} 演示大特性

## 验收标准

- **AC-1**：整个大特性端到端可用。

## 子需求

{sub_heading}
"""

DEMO_SUB_BODY = """### {sub} 第一片

- 状态：`{sub_status}`
- 目标：把第一片做完。
- 验收标准：
  - **AC-1.1**：切片可用。
  - **AC-1.2**：切片可回退。
"""


def _demo_req_text(parent_status="in-progress", sub_status="in-progress", sub_heading=".1"):
    """演示需求条目的正文（父需求与子需求的状态可调，供状态推进检查用）。"""
    heading = ""
    if sub_heading:
        heading = DEMO_SUB_BODY.format(sub=DEMO_PARENT + sub_heading, sub_status=sub_status)
    return DEMO_REQ_DOC.format(
        parent=DEMO_PARENT, sub_heading=heading, parent_status=parent_status
    )


def _demo_registry(tmp_path, monkeypatch, sub_heading=".1"):
    """在临时目录里放一个「父需求 + 子需求」条目，并把注册表指向它。"""
    reqs = tmp_path / "requirements"
    reqs.mkdir()
    (reqs / f"{DEMO_PARENT}-demo.md").write_text(_demo_req_text(sub_heading=sub_heading),
                                                 encoding="utf-8")
    monkeypatch.setattr(req_registry, "REQUIREMENTS_DIR", reqs)
    return DEMO_PARENT + sub_heading if sub_heading else DEMO_PARENT


def _demo_report(tmp_path, body, name="demo-batch.md"):
    report = tmp_path / name
    report.write_text(
        "---\nreviewer: r\nindependence: independent\n"
        f"requirements: {DEMO_PARENT}\nfull-suite: 见正文\n---\n\n1500 passed\n\n" + body,
        encoding="utf-8",
    )
    return report


def test_acceptance_gate_reads_child_acs_from_its_own_section(tmp_path, monkeypatch):
    """批次写子需求编号时，只核对那个子需求小节的 AC。"""
    sub = _demo_registry(tmp_path, monkeypatch)
    report = _demo_report(
        tmp_path, f"### {sub} 第一片\n\n- [x] **AC-1.1**：切片可用\n"
    )
    problems = acceptance_gate.evaluate(review_body(sub), [report], {sub})
    assert acceptance_gate.requirement_ac_ids(sub) == ["1.1", "1.2"]
    assert any("AC-1.2" in problem for problem in problems), problems
    assert not any("AC-1 " in problem or "AC-1**" in problem for problem in problems), problems


def test_acceptance_gate_child_ac_does_not_satisfy_the_parent_ac(tmp_path, monkeypatch):
    """`**AC-1.1**` 不能被当成父需求的 `**AC-1**`：否则父需求的大特性判据会被悄悄跳过。"""
    sub = _demo_registry(tmp_path, monkeypatch)
    report = _demo_report(
        tmp_path,
        f"### {DEMO_PARENT} 大特性\n\n- [x] **AC-1.1**：切片可用\n",
    )
    problems = acceptance_gate.evaluate(review_body(DEMO_PARENT), [report], {DEMO_PARENT})
    assert any("AC-1" in problem and "缺少结论" in problem for problem in problems), problems


def test_acceptance_gate_rejects_an_unregistered_sub_id(tmp_path, monkeypatch):
    """写了父文件里不存在的子需求小节：必须报「未登记」，而不是当成已登记放过去。"""
    sub = _demo_registry(tmp_path, monkeypatch)
    unknown_sub = DEMO_PARENT + ".7"
    report = _demo_report(tmp_path, f"### {unknown_sub} 不存在的一片\n\n- [x] **AC-7.1**：ok\n")
    problems = acceptance_gate.evaluate(review_body(unknown_sub), [report], {unknown_sub})
    assert any(unknown_sub in problem and "子需求" in problem for problem in problems), problems
    assert sub != unknown_sub


def test_acceptance_gate_requires_promoted_ids_in_the_batch(tmp_path, monkeypatch):
    """推进到 verified 的编号必须署名，报告也要覆盖它自己的 AC。

    回归（独立评审者 2026-09-20 实测出来的通道）：只把子需求写进批次、却把父需求推到
    verified，父需求的 AC-1 就永远不会被独立核对。
    """
    sub = _demo_registry(tmp_path, monkeypatch)
    child_body = f"### {sub} 第一片\n\n- [x] **AC-1.1**：ok\n- [x] **AC-1.2**：ok\n"
    only_child = _demo_report(tmp_path, child_body, name="child-only.md")
    problems = acceptance_gate.evaluate(review_body(sub), [only_child], {DEMO_PARENT})
    assert any(DEMO_PARENT in problem and "批次" in problem for problem in problems), problems

    both = _demo_report(
        tmp_path,
        child_body + f"\n### {DEMO_PARENT} 大特性\n\n- [x] **AC-1**：端到端可用\n",
        name="both.md",
    )
    assert acceptance_gate.evaluate(review_body(DEMO_PARENT, sub), [both], {DEMO_PARENT}) == []


def test_acceptance_gate_requires_a_linked_report_when_promoting(tmp_path):
    """推进状态时，PR 正文的「## 验收报告」必须链接 docs/verification/ 下的报告。"""
    acs = acceptance_gate.requirement_ac_ids("REQ-005")
    checked = "\n".join(f"- [x] **AC-{ac}**：符合预期" for ac in acs)
    report = _report(tmp_path, VERIFICATION_FRONT + "\n## 逐条验收\n\n" + checked + "\n")
    problems = acceptance_gate.evaluate("## 需求编号\n\nREQ-005\n", [report], {"REQ-005"})
    assert any("验收报告" in problem for problem in problems), problems


def _demo_git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_verified_promotions_reads_real_git_history(tmp_path):
    """推进判定走真实 git 历史：只有推到 verified 才算（改标题、回退到 implemented 都不算）。"""
    repo = tmp_path / "repo"
    (repo / "docs" / "requirements").mkdir(parents=True)
    _demo_git(repo, "init", "-q")
    _demo_git(repo, "config", "user.email", "tester@example.com")
    _demo_git(repo, "config", "user.name", "tester")
    entry = repo / "docs" / "requirements" / f"{DEMO_PARENT}-demo.md"
    entry.write_text(_demo_req_text(), encoding="utf-8")
    _demo_git(repo, "add", "-A")
    _demo_git(repo, "commit", "-qm", "one")
    entry.write_text(
        _demo_req_text(parent_status="verified", sub_status="verified"), encoding="utf-8"
    )
    _demo_git(repo, "add", "-A")
    _demo_git(repo, "commit", "-qm", "two")
    assert acceptance_gate.verified_promotions("HEAD~1", "HEAD", repo) == {DEMO_PARENT, DEMO_SUB}
    entry.write_text(
        _demo_req_text(parent_status="verified", sub_status="verified").replace(
            "演示大特性", "改了标题"
        ),
        encoding="utf-8",
    )
    _demo_git(repo, "add", "-A")
    _demo_git(repo, "commit", "-qm", "three")
    assert acceptance_gate.verified_promotions("HEAD~1", "HEAD", repo) == set()
    entry.write_text(
        _demo_req_text(parent_status="implemented", sub_status="implemented"), encoding="utf-8"
    )
    _demo_git(repo, "add", "-A")
    _demo_git(repo, "commit", "-qm", "four")
    assert acceptance_gate.verified_promotions("HEAD~1", "HEAD", repo) == set()
    entry.write_text(
        _demo_req_text(parent_status="verified", sub_status="verified"), encoding="utf-8"
    )
    _demo_git(repo, "add", "-A")
    _demo_git(repo, "commit", "-qm", "five")
    assert acceptance_gate.verified_promotions("HEAD~1", "HEAD", repo) == {DEMO_PARENT, DEMO_SUB}
