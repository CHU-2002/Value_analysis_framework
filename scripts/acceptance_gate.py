#!/usr/bin/env python
"""特性分支 → main 的独立验收门禁。

合入 `main` 之前必须有一次**独立**验收：由没有参与实现的人（或独立 agent）
跑全量测试，并逐条核对需求的验收标准，产出一份报告放到 docs/verification/。

本脚本校验「报告确实存在、确实覆盖了本批需求、每条 AC 都打勾、全量测试结果有记录」。
独立性靠流程保证（报告里必须声明 reviewer 与「未参与实现」），CI 只能校验留痕。
模板见 docs/verification/TEMPLATE.md。
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_DIR = ROOT / "docs" / "requirements"
VERIFICATION_DIR = ROOT / "docs" / "verification"
REQ_RE = re.compile(r"REQ-\d{3}")
AC_RE = re.compile(r"\*\*AC-(\d+)\*\*")
FRONT_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
PASSED_RE = re.compile(r"\d+\s+passed")
REQUIRED_FIELDS = ("reviewer", "independence", "requirements", "full-suite")
# 只动这些路径的 PR 属于「台账/文档回填」，不要求独立验收报告。
DOCS_ONLY_PREFIXES = ("docs/", "CHANGELOG.md")


def parse_front_matter(text: str) -> dict:
    match = FRONT_RE.match(text)
    if not match:
        return {}
    fields = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip().strip("\"'")
    return fields


def requirement_ac_ids(req_id: str) -> list:
    """从需求条目里取出 AC 编号，如 ['1', '2']。"""
    matches = sorted(REQUIREMENTS_DIR.glob(f"{req_id}-*.md"))
    if not matches:
        return []
    return AC_RE.findall(matches[0].read_text(encoding="utf-8"))


def checked(body: str, ac: str) -> bool:
    # 允许 `- [x] AC-1` 与 Markdown 粗体 `- [x] **AC-1**` 两种写法
    return re.search(rf"-\s*\[[xX]\]\s*\*{{0,2}}AC-{ac}\b", body) is not None


def unchecked(body: str, ac: str) -> bool:
    return re.search(rf"-\s*\[\s\]\s*\*{{0,2}}AC-{ac}\b", body) is not None


def changed_files(base: str, head: str) -> list:
    """本 PR 改动的全部文件（相对仓库根）。"""
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"git diff 失败：{proc.stderr.strip()}")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def is_docs_only(paths: list) -> bool:
    """是否只改了台账/文档（这类 PR 不需要功能验收报告）。"""
    return bool(paths) and all(path.startswith(DOCS_ONLY_PREFIXES) for path in paths)


def added_reports(base: str, head: str) -> list:
    """本 PR 新增/修改的验收报告（排除模板）。"""
    return [
        ROOT / name
        for name in changed_files(base, head)
        if name.startswith("docs/verification/") and Path(name).name != "TEMPLATE.md"
    ]


def evaluate(body: str, reports: list, paths=None) -> list:
    """返回问题列表；空列表表示通过。

    paths 为本 PR 改动的文件列表；只改台账/文档时跳过验收报告要求。
    多份报告按**并集**覆盖本批需求与验收标准（一个批次可以由多份报告组成）。
    """
    if paths and is_docs_only(paths):
        return []

    problems = []
    batch = sorted(set(REQ_RE.findall(body)))
    if not batch:
        problems.append("验收 PR 必须写明本批包含的 REQ-NNN（写在「## 需求编号」里）")
        return problems
    if not reports:
        problems.append(
            "本 PR 没有新增或修改 docs/verification/ 下的验收报告；"
            "请先由独立评审者跑全量测试并产出报告（模板见 docs/verification/TEMPLATE.md）"
        )
        return problems

    texts = {}
    for report in reports:
        text = report.read_text(encoding="utf-8")
        try:
            rel = report.relative_to(ROOT)
        except ValueError:
            rel = report
        texts[rel] = text
        fields = parse_front_matter(text)
        for field in REQUIRED_FIELDS:
            if not fields.get(field):
                problems.append(f"{rel} 的 front matter 缺少 `{field}`（模板见 docs/verification/TEMPLATE.md）")
        if fields.get("independence") and fields["independence"].lower() not in {
            "independent",
            "yes",
            "true",
        }:
            problems.append(f"{rel} 的 `independence` 必须是 independent（独立评审者未参与实现）")

    merged = "\n".join(texts.values())
    declared = set(REQ_RE.findall(merged))
    missing = [req for req in batch if req not in declared]
    if missing:
        problems.append(f"验收报告未覆盖本批需求：{', '.join(missing)}（需由独立评审者补验）")

    if not PASSED_RE.search(merged):
        problems.append("验收报告没有记录全量测试结果（需写明形如「1433 passed」的结果）")

    for req in batch:
        for ac in requirement_ac_ids(req):
            if any(unchecked(text, ac) for text in texts.values()):
                problems.append(f"{req} 的 AC-{ac} 在验收报告中未打勾（仍是 `- [ ]`）")
            elif not any(checked(text, ac) for text in texts.values()):
                problems.append(f"{req} 的 AC-{ac} 缺少结论（需写 `- [x] AC-{ac} …`）")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="特性分支 → main 的独立验收门禁")
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--base", default="origin/main", help="基线 git ref")
    parser.add_argument("--head", default="HEAD", help="当前 git ref")
    args = parser.parse_args()

    body = Path(args.body_file).read_text(encoding="utf-8")
    paths = changed_files(args.base, args.head)
    problems = evaluate(body, added_reports(args.base, args.head), paths)
    if not problems and is_docs_only(paths):
        print("独立验收门禁通过（本 PR 只改台账/文档，不需要功能验收报告）。")
        return 0
    if problems:
        print("独立验收门禁未通过：\n")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("独立验收门禁通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
