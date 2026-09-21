#!/usr/bin/env python
"""维护项目整体测试 scope：生成登记表、校验预算与漂移。

为什么不是「按 PR 裁剪测试范围」：CI 对每个 PR 都跑全量，
成本要靠**长期维护整体 scope**来控制，而不是让每个 PR 只跑自己那几个文件
——后者会让回归风险随分支数增长，且漏跑无法被发现。所以：

  python scripts/test_scope.py --report   打印 scope 与预算使用情况
  python scripts/test_scope.py --write    重写 docs/TEST_SCOPE.md（登记表）
  python scripts/test_scope.py --check    CI：登记表是否最新 + 是否超预算

登记表是 scope 的单一事实来源：新增测试文件必须重新生成并提交，
删除测试文件必须同步移除登记行，否则 `--check` 失败。
"""

import argparse
import ast
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import req_registry

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
SCOPE_PATH = ROOT / "docs" / "TEST_SCOPE.md"
REQUIREMENTS_DIR = ROOT / "docs" / "requirements"
CONFTEST_PATH = TESTS_DIR / "conftest.py"

# 预算：控制 CI 总量。上调必须在本文件与 docs/TEST_SCOPE.md 里写明理由，
# 且优先考虑合并/删除冗余测试，而不是放宽上限。
#
# 2026-09-21 上调（40→48 文件、1600→1800 用例），经需求 owner CHU-2002 书面批准：
#   理由：使用者已声明会持续给图形化控制台（REQ-009）追加新需求；REQ-009 的四个切片合计
#     新增 4 个测试文件 / ≤64 条用例，原上限在 REQ-009 做完后只剩 14 条余量，
#     会让后续每个 GUI 需求都卡在门禁上。使用者在会话中明确选择「改 AC-7 并上调」，
#     而不是先做用例清理。
#   代价：用例数上限 +12.5%，CI 墙钟时间随之增加（上调前实测：1519 passed、并行 28.45s）。
#     这是本仓库第一次上调 scope 预算，**不构成先例**——下次接近上限仍按
#     docs/TESTING.md §5「先清理，再谈上调」处理。
#   留痕：REQ-006 的 AC-7 变更记录与任务清单 T7、docs/TESTING.md §5、docs/DEVELOPMENT.md §14。
MAX_TEST_FILES = 48
MAX_COLLECTED_CASES = 1800
LAYERS = ("unit", "contract", "e2e", "integration")
# 归属列可以写父需求 REQ-NNN，也可以写子需求 REQ-NNN.S（README.md §6.1）
REQ_RE = req_registry.REQ_ID_RE
ROW_RE = re.compile(r"^\|\s*`(tests/[^`]+)`\s*\|(.*)\|\s*$", re.MULTILINE)


def load_layers() -> dict:
    """从 tests/conftest.py 读取分层登记表（保持单一来源）。"""
    spec = importlib.util.spec_from_file_location("_scope_conftest", CONFTEST_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return dict(getattr(module, "TEST_LAYERS", {})), getattr(module, "DEFAULT_LAYER", "unit")


def count_test_functions(path: Path) -> int:
    """静态统计测试函数个数（不含 parametrize 展开）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return 0
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )


def collect_scope() -> list:
    """返回每支测试文件的 scope 条目。"""
    layers, default_layer = load_layers()
    entries = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        reqs = sorted(set(REQ_RE.findall(text)))
        target = ROOT / "scripts" / path.name[len("test_"):]
        entries.append(
            {
                "file": f"tests/{path.name}",
                "reqs": ", ".join(reqs) if reqs else "基线",
                "layer": layers.get(path.name, default_layer),
                "target": f"`scripts/{target.name}`" if target.exists() else "—",
                "cases": count_test_functions(path),
            }
        )
    return entries


def collect_cases_via_pytest() -> int:
    """用 pytest 收集一次，拿到含 parametrize 展开的真实用例数。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"pytest 收集失败：\n{proc.stdout}\n{proc.stderr}")
    return sum(1 for line in proc.stdout.splitlines() if "::" in line)


def render(entries: list, collected: int) -> str:
    files = len(entries)
    lines = [
        "# 测试 Scope 登记表",
        "",
        "> 本文件由 `python scripts/test_scope.py --write` 生成，**请勿手工编辑**。",
        "> CI 用 `python scripts/test_scope.py --check` 校验它与实际测试文件一致，并检查预算。",
        "",
        "## 为什么要有这张表",
        "",
        "CI 对每个 PR 都跑**全量**测试：只有全量才能发现「新功能踩坏别处」。",
        "因此控制 CI 成本的方向不是裁剪单个 PR 的测试范围，而是**长期维护整体 scope**——",
        "每支测试文件都要能说清它为什么存在、归属哪条需求；冗余的合并掉，过时的删掉。",
        "预算见下一节：涨到接近上限时，先清理，而不是直接调高。",
        "",
        "## 预算",
        "",
        "| 项 | 当前 | 上限 | 使用率 |",
        "|----|------|------|--------|",
        f"| 测试文件数 | {files} | {MAX_TEST_FILES} | {files / MAX_TEST_FILES:.0%} |",
        f"| 收集到的用例数 | {collected} | {MAX_COLLECTED_CASES} | {collected / MAX_COLLECTED_CASES:.0%} |",
        "",
        "（用例数含 `parametrize` 展开，由 `pytest --collect-only` 统计；函数数见下表末列，仅作参考。）",
        "",
        "## 登记表",
        "",
        "| 测试文件 | 归属需求 | 层 | 被测对象 | 测试函数数 |",
        "|----------|----------|----|----------|------------|",
    ]
    for entry in entries:
        lines.append(
            f"| `{entry['file']}` | {entry['reqs']} | `{entry['layer']}` | "
            f"{entry['target']} | {entry['cases']} |"
        )
    lines += [
        "",
        "归属为「基线」的测试覆盖需求体系建立前就已交付的能力，见",
        "[`docs/requirements/ledger.md`](requirements/ledger.md) 的「已交付基线」小节。",
        "把它们补齐到具体需求属于 Inbox 事项。",
        "归属列可以写父需求 `REQ-NNN`，也可以写子需求 `REQ-NNN.S`，"
        "见 [`docs/requirements/README.md`](requirements/README.md) §6.1。",
        "",
    ]
    return "\n".join(lines)


def registered_requirement_ids() -> set:
    """已登记的需求编号：父需求 REQ-NNN 与其子需求 REQ-NNN.S（见 req_registry）。"""
    return req_registry.registered_ids()


def ownership_problems(entries: list) -> list:
    """归属列里出现的 REQ-NNN / REQ-NNN.S 必须已登记（曾出现过 REQ-999 悬空编号）。"""
    registered = registered_requirement_ids()
    problems = []
    for entry in entries:
        for req_id in sorted(set(REQ_RE.findall(entry["reqs"]))):
            if req_id not in registered:
                problems.append(
                    f"{entry['file']} 的归属编号 {req_id} 未在 docs/requirements/ 登记；"
                    "请先登记需求（或修正编号），再重新生成登记表"
                )
    return problems


def ownership_drift_problems(entries: list, text: str) -> list:
    """登记表的归属列内容是否与 collect_scope() 的计算值一致。

    只比对**已登记**的文件：缺登记与多登记由其它检查负责。此前只校验归属列的编号
    是否已登记、不比对内容，于是「补了测试标注却忘记 make scope-write」不会被拦
    （REQ-007 收尾时实测漂移）。
    """
    declared = {}
    for match in ROW_RE.finditer(text):
        cells = [cell.strip() for cell in match.group(2).split("|")]
        declared[match.group(1)] = cells[0] if cells else ""
    problems = []
    for entry in entries:
        got = declared.get(entry["file"])
        if got is not None and got != entry["reqs"]:
            problems.append(
                f"{entry['file']} 的归属列已过期：登记表写 `{got}`，实际应为 `{entry['reqs']}`；"
                "请运行 make scope-write 重新生成"
            )
    return problems


def registered_files(text: str) -> set:
    return {match.group(1) for match in ROW_RE.finditer(text)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="维护整体测试 scope")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--report", action="store_true", help="打印 scope 与预算使用情况")
    group.add_argument("--write", action="store_true", help="重写 docs/TEST_SCOPE.md")
    group.add_argument("--check", action="store_true", help="校验登记表与预算（CI 用）")
    args = parser.parse_args(argv)

    entries = collect_scope()
    actual = {entry["file"] for entry in entries}
    problems = []

    if args.report:
        collected = collect_cases_via_pytest()
        by_layer = {}
        for entry in entries:
            by_layer[entry["layer"]] = by_layer.get(entry["layer"], 0) + 1
        print(f"测试文件 {len(entries)}/{MAX_TEST_FILES}，用例 {collected}/{MAX_COLLECTED_CASES}")
        print("分层：" + "，".join(f"{k} {v}" for k, v in sorted(by_layer.items())))
        for entry in entries:
            print(f"  {entry['file']:<45} {entry['reqs']:<22} {entry['layer']:<11} {entry['cases']:>3}")
        return 0

    if args.write:
        SCOPE_PATH.write_text(render(entries, collect_cases_via_pytest()), encoding="utf-8")
        print(f"已写入 {SCOPE_PATH.relative_to(ROOT)}（{len(entries)} 支文件）")
        return 0

    # --check
    if not SCOPE_PATH.exists():
        problems.append(f"缺少 {SCOPE_PATH.relative_to(ROOT)}，请运行 python scripts/test_scope.py --write")
    else:
        scope_text = SCOPE_PATH.read_text(encoding="utf-8")
        registered = registered_files(scope_text)
        missing = sorted(actual - registered)
        stale = sorted(registered - actual)
        if missing:
            problems.append("以下测试文件未登记（新增测试后请重新生成）：\n  " + "\n  ".join(missing))
        if stale:
            problems.append("登记表里有已不存在的测试文件（删除后请重新生成）：\n  " + "\n  ".join(stale))
    if len(entries) > MAX_TEST_FILES:
        problems.append(f"测试文件数 {len(entries)} 超过预算 {MAX_TEST_FILES}：请先合并/清理冗余测试")
    problems.extend(ownership_problems(entries))
    if SCOPE_PATH.exists():
        problems.extend(ownership_drift_problems(entries, SCOPE_PATH.read_text(encoding="utf-8")))
    collected = collect_cases_via_pytest()
    if collected > MAX_COLLECTED_CASES:
        problems.append(
            f"用例数 {collected} 超过预算 {MAX_COLLECTED_CASES}：请先合并/清理冗余测试，"
            "确需上调请在 scripts/test_scope.py 与 docs/TEST_SCOPE.md 写明理由"
        )

    if problems:
        print("测试 scope 检查未通过：\n")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"测试 scope 检查通过：{len(entries)} 支文件、{collected} 个用例，均在预算内。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
