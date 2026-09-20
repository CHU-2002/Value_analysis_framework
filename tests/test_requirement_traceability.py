#!/usr/bin/env python
"""需求、测试与开发治理的一致性门禁。

本文件把「流程约定」变成 CI 可执行的检查，避免文档与实现各说各话。
强制的不变量：

1. `docs/requirements/REQ-*.md` 的 front matter 合法，`id` 与文件名前缀一致；
2. 需求台账 `ledger.md` 与 `REQ-*.md` 文件集合**严格一致**（不重不漏）；
3. 台账中的状态 / 优先级取值合法，链接指向真实存在的文件，且与条目内状态一致；
4. 逐条需求都有「验收标准」，且至少一条 `AC-n`；
5. 测试里引用的 `REQ-NNN` / `REQ-NNN.S` 必须真实存在（无悬空引用）；
6. 状态为 `implemented` / `verified` 的需求（含子需求）必须被至少一个测试文件引用；
7. `tests/conftest.py` 的测试分层表不指向已删除的文件。
8. **子需求**（`REQ-NNN.S`）与台账「子需求台账」表严格一致，状态合法，
   AC 编号写作 `AC-S.n`，且**父需求的状态不得比它最慢的子需求更靠前**
   ——否则「拆子需求」会变成「偷偷少验收」。

约定：测试用注释 `# 覆盖需求：REQ-NNN`（子需求写完整编号 `REQ-NNN.S`）声明归属，
关键条款写 `AC-n` / `AC-S.n`。详见 docs/requirements/README.md 与 docs/TESTING.md。
"""

import importlib.util
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

import req_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_DIR = REPO_ROOT / "docs" / "requirements"
LEDGER_PATH = REQUIREMENTS_DIR / "ledger.md"
TESTS_DIR = REPO_ROOT / "tests"

# 本文件自身会被扫描 REQ 引用，但它只讨论编号而不覆盖需求，故排除。
SELF = Path(__file__).resolve()

STATUSES = {
    "proposed",
    "accepted",
    "in-progress",
    "implemented",
    "verified",
    "deferred",
    "rejected",
    "superseded",
}
PRIORITIES = {"P0", "P1", "P2", "P3"}
DELIVERED_STATUSES = {"implemented", "verified"}
LAYERS = {"unit", "contract", "e2e", "integration"}

REQUIRED_FIELDS = ("id", "title", "status", "priority", "owner", "created", "updated")
FRONT_MATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
LEDGER_ROW_RE = re.compile(r"^\|\s*\[(REQ-\d{3})\]\(([^)]+)\)\s*\|(.*)\|\s*$")
SUB_LEDGER_ROW_RE = re.compile(r"^\|\s*\[(REQ-\d{3}\.\d+)\]\(([^)]+)\)\s*\|(.*)\|\s*$")
REQ_ID_RE = req_registry.REQ_ID_RE
AC_RE = re.compile(r"\*\*AC-\d+\*\*")

# 状态推进的先后次序；deferred / rejected / superseded 是旁路终态，不参与比较。
STATUS_RANK = {
    "proposed": 1,
    "accepted": 2,
    "in-progress": 3,
    "implemented": 4,
    "verified": 5,
}
TERMINAL_STATUSES = {"deferred", "rejected", "superseded"}


def parse_front_matter(path: Path) -> dict:
    """解析简易 `key: value` front matter（不引入 YAML 依赖）。"""
    match = FRONT_MATTER_RE.match(path.read_text(encoding="utf-8"))
    if not match:
        raise AssertionError(f"{path.relative_to(REPO_ROOT)} 缺少 `---` 包裹的 front matter")
    fields = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        assert sep, f"{path.relative_to(REPO_ROOT)} front matter 行缺少冒号：{line!r}"
        fields[key.strip()] = value.strip().strip("\"'")
    return fields


def requirement_files() -> dict:
    """返回 {REQ 编号: (路径, front matter)}。"""
    found = {}
    for path in sorted(REQUIREMENTS_DIR.glob("REQ-*.md")):
        fields = parse_front_matter(path)
        req_id = fields.get("id", "")
        found[req_id] = (path, fields)
    return found


def parse_ledger() -> dict:
    """返回台账行 {REQ 编号: {"file":.., "status":.., "priority":..}}。"""
    rows = {}
    for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        match = LEDGER_ROW_RE.match(line)
        if not match:
            continue
        req_id, target, rest = match.groups()
        cells = [cell.strip() for cell in rest.split("|")]
        assert len(cells) >= 5, f"台账列数不足：{line!r}"
        rows[req_id] = {
            "file": target,
            "title": cells[0],
            "status": cells[1].strip("`"),
            "priority": cells[2].strip("`"),
            "issue": cells[3],
            "pr": cells[4],
        }
    return rows


def referenced_requirements() -> dict:
    """扫描测试目录，返回 {REQ 编号: {引用它的测试文件}}。"""
    references = {}
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        if path.resolve() == SELF:
            continue
        text = path.read_text(encoding="utf-8")
        for req_id in set(REQ_ID_RE.findall(text)):
            references.setdefault(req_id, set()).add(str(path.relative_to(REPO_ROOT)))
    return references


def sub_requirements() -> dict:
    """扫描父需求文件的「## 子需求」小节，返回 {子需求编号: {...}}。"""
    found = {}
    for path in sorted(REQUIREMENTS_DIR.glob("REQ-*.md")):
        for sub_id, body in req_registry.sub_sections(path.read_text(encoding="utf-8")).items():
            status = req_registry.SUB_STATUS_RE.search(body)
            goal = req_registry.SUB_GOAL_RE.search(body)
            found[sub_id] = {
                "path": path,
                "parent": req_registry.parent_of(sub_id),
                "body": body,
                "status": status.group(1) if status else None,
                "goal": goal.group(1).strip() if goal else None,
                "ac_ids": req_registry.ac_ids(sub_id),
            }
    return found


def parse_sub_ledger() -> dict:
    """返回台账「子需求台账」表 {子需求编号: {...}}（表不存在时为空）。"""
    rows = {}
    for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        match = SUB_LEDGER_ROW_RE.match(line)
        if not match:
            continue
        sub_id, target, rest = match.groups()
        cells = [cell.strip() for cell in rest.split("|")]
        assert len(cells) >= 5, f"子需求台账列数不足：{line!r}"
        rows[sub_id] = {
            "file": target,
            "parent": cells[0].strip("`"),
            "title": cells[1],
            "status": cells[2].strip("`"),
            "pr": cells[3],
            "tests": cells[4],
        }
    return rows


def _rel(path: Path) -> str:
    """相对仓库根的显示路径；演示用的临时目录不在仓库内，就原样显示。"""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def sub_placement_problems() -> list:
    """子需求必须写在父需求文件的「## 子需求」小节里，且编号不得重复。

    只按 `^### REQ-NNN.S` 全文搜索是不够的：写在「## 范围」下面的小节同样会被识别，
    文档 §6.1 的「写在 ## 子需求 小节里」就成了空话；重复编号会被 dict 悄悄合并，
    台账与 AC 只取到最后一段。
    """
    problems = []
    for path in sorted(REQUIREMENTS_DIR.glob("REQ-*.md")):
        text = path.read_text(encoding="utf-8")
        headings = req_registry.SUB_HEADING_RE.findall(text)
        if not headings:
            continue
        inside = set(
            req_registry.SUB_HEADING_RE.findall(req_registry.named_section(text, "子需求"))
        )
        for sub_id in headings:
            if sub_id not in inside:
                problems.append(
                    f"{_rel(path)} 的 {sub_id} 不在「## 子需求」小节里；"
                    "子需求必须写在该小节下（README.md §6.1）"
                )
        for sub_id, count in sorted(Counter(headings).items()):
            if count > 1:
                problems.append(
                    f"{_rel(path)} 里 {sub_id} 出现了 {count} 次；编号必须唯一，"
                    "重复会让台账与 AC 只取到最后一段"
                )
    return problems


def sub_requirement_problems() -> list:
    """子需求与其台账的一致性、编号写法问题；返回问题列表（空 = 通过）。"""
    problems = []
    if "## 子需求台账" not in LEDGER_PATH.read_text(encoding="utf-8"):
        problems.append("ledger.md 缺少「## 子需求台账」小节（子需求也要登记，见 README.md §6.1）")
    subs = sub_requirements()
    rows = parse_sub_ledger()
    for sub_id in sorted(set(subs) - set(rows)):
        problems.append(
            f"{subs[sub_id]['path'].name} 里的子需求 {sub_id} 未登记到台账「子需求台账」表"
        )
    for sub_id in sorted(set(rows) - set(subs)):
        problems.append(f"台账里的子需求 {sub_id} 在需求文件里找不到 `### {sub_id}` 小节")
    problems.extend(sub_placement_problems())
    for sub_id, sub in sorted(subs.items()):
        rel = _rel(sub["path"])
        index = sub_id.split(".", 1)[1]
        if sub["status"] not in STATUSES:
            problems.append(
                f"{rel} 的 {sub_id} 状态不合法或缺失：{sub['status']!r}（需 `- 状态：`xxx``）"
            )
        if not sub["goal"] or sub["goal"].upper() in {"TBD", "待填"}:
            problems.append(f"{rel} 的 {sub_id} 缺少「- 目标：」（一句话说清这片要什么）")
        if not sub["ac_ids"]:
            problems.append(f"{rel} 的 {sub_id} 没有任何验收标准（需 `**AC-{index}.n**`）")
        for ac in sub["ac_ids"]:
            if ac.split(".", 1)[0] != index:
                problems.append(
                    f"{rel} 的 {sub_id} 用了 `AC-{ac}`：子需求 AC 必须写作 `AC-{index}.n`"
                )
        if re.search(r"\*\*AC-\d+\*\*", sub["body"]):
            problems.append(
                f"{rel} 的 {sub_id} 用了父需求式 `**AC-n**`：子需求须写 `**AC-{index}.n**`，"
                "否则与父需求的同号 AC 无法区分"
            )
        row = rows.get(sub_id)
        if row is None:
            continue
        if row["parent"] != sub["parent"]:
            problems.append(
                f"台账 {sub_id} 的父需求写作 {row['parent']!r}，应为 {sub['parent']!r}"
            )
        if row["file"] != sub["path"].name:
            problems.append(
                f"台账 {sub_id} 链接指向 {row['file']!r}，应为它所在的父需求文件 {sub['path'].name!r}"
            )
        if row["status"] != sub["status"]:
            problems.append(
                f"{sub_id} 台账状态 {row['status']!r} 与文件 {sub['status']!r} 不一致；"
                "两处必须同步更新"
            )
    return problems


def parent_status_problems() -> list:
    """父需求的状态不得比它最慢的子需求更靠前（旁路终态不参与比较）。"""
    children = {}
    for sub_id, sub in sub_requirements().items():
        children.setdefault(sub["parent"], []).append((sub_id, sub))
    files = requirement_files()
    problems = []
    for parent, subs in sorted(children.items()):
        if parent not in files:
            continue  # 父需求缺失由其它检查负责
        parent_status = files[parent][1]["status"]
        if parent_status in TERMINAL_STATUSES or parent_status not in STATUS_RANK:
            continue
        for sub_id, sub in sorted(subs):
            if sub["status"] in TERMINAL_STATUSES or sub["status"] not in STATUS_RANK:
                continue
            if STATUS_RANK[parent_status] > STATUS_RANK[sub["status"]]:
                problems.append(
                    f"{parent} 状态是 {parent_status}，但子需求 {sub_id} 还停在 {sub['status']}："
                    "父需求不得比它的子需求更靠前——子需求没验收完，父需求就不能算做完"
                )
    return problems


def _load_conftest():
    spec = importlib.util.spec_from_file_location(
        "_governance_conftest", TESTS_DIR / "conftest.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_front_matter_is_valid():
    files = requirement_files()
    assert files, "docs/requirements/ 下没有任何 REQ-*.md"
    for req_id, (path, fields) in files.items():
        rel = path.relative_to(REPO_ROOT)
        for field in REQUIRED_FIELDS:
            assert fields.get(field), f"{rel} 缺少必填字段 {field}"
        assert re.fullmatch(r"REQ-\d{3}", req_id), f"{rel} 的 id 不合法：{req_id!r}"
        assert path.name.startswith(f"{req_id}-"), f"{rel} 文件名前缀与 id {req_id} 不一致"
        assert fields["status"] in STATUSES, f"{rel} 状态不合法：{fields['status']!r}"
        assert fields["priority"] in PRIORITIES, f"{rel} 优先级不合法：{fields['priority']!r}"


def test_every_requirement_has_acceptance_criteria():
    for req_id, (path, _) in requirement_files().items():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO_ROOT)
        assert "## 验收标准" in text, f"{rel} 缺少「## 验收标准」小节"
        assert AC_RE.search(text), f"{rel} 没有任何 `**AC-n**` 验收条款"


def test_ledger_matches_requirement_files():
    files = set(requirement_files())
    rows = parse_ledger()
    assert rows, "ledger.md 中未解析到任何需求台账行"
    assert files == set(rows), (
        f"台账与需求文件不一致：仅在文件中 {sorted(files - set(rows))}；"
        f"仅在台账中 {sorted(set(rows) - files)}"
    )


def test_ledger_entries_are_wellformed():
    for req_id, row in parse_ledger().items():
        assert row["status"] in STATUSES, f"台账 {req_id} 状态不合法：{row['status']!r}"
        assert row["priority"] in PRIORITIES, f"台账 {req_id} 优先级不合法：{row['priority']!r}"
        target = REQUIREMENTS_DIR / row["file"]
        assert target.exists(), f"台账 {req_id} 链接指向不存在的文件：{row['file']}"
        assert target.name.startswith(f"{req_id}-"), f"台账 {req_id} 与链接文件不匹配：{row['file']}"


def test_ledger_status_matches_front_matter():
    for req_id, row in parse_ledger().items():
        _, fields = requirement_files()[req_id]
        assert row["status"] == fields["status"], (
            f"{req_id} 台账状态 {row['status']!r} 与需求文件 {fields['status']!r} 不一致；"
            "两处必须同步更新"
        )


def test_no_dangling_requirement_references():
    known = set(requirement_files()) | set(sub_requirements())
    dangling = {
        req: sorted(files)
        for req, files in referenced_requirements().items()
        if req not in known
    }
    assert not dangling, (
        f"测试引用了不存在的需求编号：{dangling}；"
        "请先在 docs/requirements/ 登记需求并加入 ledger.md"
    )


def test_delivered_requirements_are_covered_by_tests():
    references = referenced_requirements()
    uncovered = []
    for req_id, (path, fields) in requirement_files().items():
        if fields["status"] in DELIVERED_STATUSES and not references.get(req_id):
            uncovered.append(f"{req_id}（{fields['status']}，{path.name}）")
    for sub_id, sub in sub_requirements().items():
        if sub["status"] in DELIVERED_STATUSES and not references.get(sub_id):
            uncovered.append(f"{sub_id}（{sub['status']}，{sub['path'].name}）")
    assert not uncovered, (
        "以下需求已交付但没有任何测试引用该编号：\n  "
        + "\n  ".join(uncovered)
        + "\n请在覆盖它的测试文件中加注释 `# 覆盖需求：<REQ 编号>`"
    )


def test_sub_requirements_match_the_ledger():
    """子需求小节与台账「子需求台账」表必须严格一致，且写法合规。"""
    problems = sub_requirement_problems()
    assert not problems, "子需求与台账不一致：\n  " + "\n  ".join(problems)


def test_parent_status_cannot_outrun_its_sub_requirements():
    problems = parent_status_problems()
    assert not problems, "\n  ".join(problems)


# 演示用的假编号：用拼接书写，避免被本文件自己的扫描判成悬空引用。
DEMO_PARENT = "REQ-" + "901"
DEMO_SUB = DEMO_PARENT + ".2"
# 本模块自身：演示数据要 monkeypatch 本模块的 REQUIREMENTS_DIR / LEDGER_PATH 全局量。
traceability = sys.modules[__name__]

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

- **AC-1**：整体可用。

{sub_section}

### {sub} 第一片

- 状态：`{sub_status}`
- 目标：把第一片做完。
- 验收标准：
  - {sub_ac}

- 追溯：`tests/test_demo.py`
{extra_sub}"""

DEMO_LEDGER_DOC = """# 需求台账

## 需求台账

| ID | 标题 | 状态 | 优先级 | Issue | 实现 PR | 关联测试 |
|----|------|------|--------|-------|---------|----------|
| [{parent}]({parent_file}) | 演示大特性 | `{parent_status}` | P2 | N/A | TBD | `tests/test_demo.py` |

## 子需求台账

| ID | 父需求 | 标题 | 状态 | 实现 PR | 关联测试 |
|----|--------|------|------|---------|----------|
{sub_row}
"""


def _demo_tree(tmp_path, monkeypatch, parent_status="verified", sub_status="in-progress",
               sub_ac="**AC-2.1**：切片可用。", ledger_status=None, with_row=True,
               sub_section="## 子需求", extra_sub=""):
    """在临时目录里搭一份「父需求 + 一个子需求 + 台账」的演示数据。"""
    reqs = tmp_path / "requirements"
    reqs.mkdir()
    parent_file = f"{DEMO_PARENT}-demo.md"
    (reqs / parent_file).write_text(
        DEMO_REQ_DOC.format(
            parent=DEMO_PARENT, parent_status=parent_status, sub=DEMO_SUB,
            sub_status=sub_status, sub_ac=sub_ac, sub_section=sub_section,
            extra_sub=extra_sub,
        ),
        encoding="utf-8",
    )
    row = (
        f"| [{DEMO_SUB}]({parent_file}) | {DEMO_PARENT} | 第一片 | "
        f"`{ledger_status or sub_status}` | TBD | `tests/test_demo.py` |"
    )
    ledger = tmp_path / "ledger.md"
    ledger.write_text(
        DEMO_LEDGER_DOC.format(
            parent=DEMO_PARENT, parent_file=parent_file, parent_status=parent_status,
            sub_row=row if with_row else "",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(traceability, "REQUIREMENTS_DIR", reqs)
    monkeypatch.setattr(traceability, "LEDGER_PATH", ledger)
    monkeypatch.setattr(req_registry, "REQUIREMENTS_DIR", reqs)
    return reqs, ledger


def test_sub_requirement_invariants_accept_a_consistent_demo(tmp_path, monkeypatch):
    _demo_tree(tmp_path, monkeypatch, parent_status="in-progress", sub_status="in-progress")
    assert sub_requirement_problems() == []
    assert parent_status_problems() == []


def test_verified_parent_with_unfinished_sub_is_rejected(tmp_path, monkeypatch):
    """回归：父需求不能一边写着 verified，一边有子需求停在 in-progress。"""
    _demo_tree(tmp_path, monkeypatch, parent_status="verified", sub_status="in-progress")
    assert sub_requirement_problems() == []
    problems = parent_status_problems()
    assert any(DEMO_SUB in problem and "不得比" in problem for problem in problems), problems


def test_sub_requirement_missing_from_ledger_is_rejected(tmp_path, monkeypatch):
    _demo_tree(tmp_path, monkeypatch, with_row=False)
    problems = sub_requirement_problems()
    assert any(DEMO_SUB in problem and "未登记" in problem for problem in problems), problems


def test_sub_requirement_status_drift_is_rejected(tmp_path, monkeypatch):
    _demo_tree(tmp_path, monkeypatch, sub_status="in-progress", ledger_status="verified")
    problems = sub_requirement_problems()
    assert any(DEMO_SUB in problem and "不一致" in problem for problem in problems), problems


def test_sub_requirement_ac_numbering_must_match_its_index(tmp_path, monkeypatch):
    _demo_tree(tmp_path, monkeypatch, sub_ac="**AC-3.1**：切片可用。")
    problems = sub_requirement_problems()
    assert any(DEMO_SUB in problem and "AC-2.n" in problem for problem in problems), problems


def test_sub_requirement_outside_the_reserved_section_is_rejected(tmp_path, monkeypatch):
    """回归：`### REQ-NNN.S` 写在别的小节下也要拒——否则 §6.1 的「写在 ## 子需求 里」是空话。"""
    _demo_tree(tmp_path, monkeypatch, sub_section="## 范围")
    problems = sub_requirement_problems()
    assert any(DEMO_SUB in problem and "不在" in problem for problem in problems), problems


def test_duplicate_sub_requirement_headings_are_rejected(tmp_path, monkeypatch):
    """回归：同一个子需求编号写两遍会被 dict 悄悄合并，台账与 AC 只取最后一段。"""
    duplicate = (
        f"\n### {DEMO_SUB} 第一片的副本\n\n- 状态：`in-progress`\n- 目标：重复的一段。\n"
        f"- 验收标准：\n  - **AC-2.9**：重复。\n"
    )
    _demo_tree(tmp_path, monkeypatch, extra_sub=duplicate)
    problems = sub_requirement_problems()
    assert any(DEMO_SUB in problem and "出现了 2 次" in problem for problem in problems), problems


def test_test_layer_registry_is_valid():
    conftest = _load_conftest()
    layers = getattr(conftest, "TEST_LAYERS", None)
    assert layers, "tests/conftest.py 必须定义 TEST_LAYERS 分层表"
    for filename, layer in layers.items():
        assert (TESTS_DIR / filename).exists(), (
            f"conftest.py 的 TEST_LAYERS 指向不存在的测试文件：{filename}"
        )
        assert layer in LAYERS, f"{filename} 的分层 {layer!r} 未在 pytest.ini 注册"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
