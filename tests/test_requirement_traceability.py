#!/usr/bin/env python
"""需求、测试与开发治理的一致性门禁。

本文件把「流程约定」变成 CI 可执行的检查，避免文档与实现各说各话。
强制的不变量：

1. `docs/requirements/REQ-*.md` 的 front matter 合法，`id` 与文件名前缀一致；
2. 需求台账 `ledger.md` 与 `REQ-*.md` 文件集合**严格一致**（不重不漏）；
3. 台账中的状态 / 优先级取值合法，链接指向真实存在的文件，且与条目内状态一致；
4. 逐条需求都有「验收标准」，且至少一条 `AC-n`；
5. 测试里引用的 `REQ-NNN` 必须真实存在（无悬空引用）；
6. 状态为 `implemented` / `verified` 的需求必须被至少一个测试文件引用；
7. `tests/conftest.py` 的测试分层表不指向已删除的文件。

约定：测试用注释 `# 覆盖需求：REQ-NNN` 声明归属，关键条款写 `AC-n`。
详见 docs/requirements/README.md 与 docs/TESTING.md。
"""

import importlib.util
import re
from pathlib import Path

import pytest

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
REQ_ID_RE = re.compile(r"REQ-\d{3}")
AC_RE = re.compile(r"\*\*AC-\d+\*\*")


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
    known = set(requirement_files())
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
    assert not uncovered, (
        "以下需求已交付但没有任何测试引用该编号：\n  "
        + "\n  ".join(uncovered)
        + "\n请在覆盖它的测试文件中加注释 `# 覆盖需求：<REQ 编号>`"
    )


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
