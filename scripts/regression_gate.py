#!/usr/bin/env python
"""main 批量回归门禁：每累积 N 个特性合入 main，就必须补一次全量回归。

分支模型（见 docs/DEVELOPMENT.md）：特性分支攒够子 PR 后**直接合入 main**，
不为集成单独维护一条长期分支。代价是 main 会持续变化，所以约定：

- 每个特性合入 main 时，由独立评审者跑全量测试并逐条核对验收标准（门②）；
- main 不要求每次都做批量全量回归，但**每累积 3 个特性**必须补一次，
  并把结果留成记录放到 docs/regression/。没补记录，下一个特性分支合 main 的 PR 会被卡住。

一个「特性合入」= main 上的一条 squash 后的 `feat(...)` 提交，或一条 merge 提交。

  python scripts/regression_gate.py --check         CI：是否需要补回归记录
  python scripts/regression_gate.py --new           打印一份可直接填写的记录草稿
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGRESSION_DIR = ROOT / "docs" / "regression"
FRONT_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
FEATURE_RE = re.compile(r"^feat(\(|:|!)")
MERGE_RE = re.compile(r"^Merge pull request")
THRESHOLD = 3


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


def load_records(directory: Path = REGRESSION_DIR) -> list:
    records = []
    if not directory.exists():
        return records
    for path in sorted(directory.glob("*.md")):
        if path.name in {"TEMPLATE.md", "README.md"}:
            continue
        fields = parse_front_matter(path.read_text(encoding="utf-8"))
        if fields.get("covered-until"):
            records.append({"path": path, **fields})
    return records


def latest_record(records: list):
    if not records:
        return None
    return max(records, key=lambda record: record.get("date", ""))


def select_features(subjects: list) -> list:
    """从提交标题里挑出「特性合入」：squash 的 feat 提交，或 merge 提交。"""
    return [s for s in subjects if FEATURE_RE.match(s) or MERGE_RE.match(s)]


def count_features(subjects: list) -> int:
    return len(select_features(subjects))


def commit_subjects(since: str, main_ref: str = "origin/main") -> list:
    proc = subprocess.run(
        ["git", "log", "--pretty=%s", f"{since}..{main_ref}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"git log 失败：{proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def evaluate(records: list, subjects: list, threshold: int = THRESHOLD) -> list:
    """返回问题列表；空列表表示通过。subjects 为「自上次记录以来」的提交标题。"""
    features = select_features(subjects)
    if len(features) < threshold:
        return []
    detail = "\n  ".join(features)
    record = latest_record(records)
    where = (
        f"自上次回归记录（{record['path'].name}，覆盖到 {record.get('covered-until')}）以来，"
        if record
        else "至今为止，"
    )
    return [
        f"{where}main 已累积 {len(features)} 个特性合入（阈值 {threshold}），但没有对应的全量回归记录。\n"
        f"  累积的特性：\n  {detail}\n"
        "  请由独立评审者在 main 上跑一次全量回归并留档："
        "python scripts/regression_gate.py --new，记录放入 docs/regression/"
    ]


def draft(main_ref: str) -> str:
    sha = subprocess.run(
        ["git", "rev-parse", "--short", main_ref],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.strip()
    date = subprocess.run(["date", "+%F"], capture_output=True, text=True).stdout.strip()
    records = load_records()
    if records:
        since = latest_record(records)["covered-until"]
    else:
        since = subprocess.run(
            ["git", "rev-list", "--max-parents=0", main_ref],
            cwd=ROOT, capture_output=True, text=True,
        ).stdout.split()[0]
    features = select_features(commit_subjects(since, main_ref))
    listing = "\n".join(f"- {subject}" for subject in features) or "- （无）"
    return (
        "---\n"
        f"date: {date}\n"
        f"covered-until: {sha}\n"
        "reviewer: TBD（必须是没有参与本批实现的独立评审者）\n"
        "independence: independent\n"
        "requirements: TBD（本批涉及的 REQ-NNN，逗号分隔）\n"
        f"full-suite: TBD（形如 1433 passed / 3 skipped，覆盖率 76.30%）\n"
        "coverage: TBD\n"
        "---\n"
        "\n"
        "# main 批量全量回归记录\n"
        "\n"
        "## 覆盖范围\n"
        "\n"
        f"覆盖到 `{sha}`，自上次记录以来累积的特性合入：\n\n{listing}\n"
        "\n"
        "## 全量测试\n"
        "\n"
        "```bash\nmake verify\n```\n"
        "\n"
        "结果：（粘贴 passed / skipped / 覆盖率）\n"
        "\n"
        "## 逐条验收\n"
        "\n"
        "<!-- 本批涉及的每条需求，逐条 AC 给结论；不通过用 - [ ] 并附现象与复现命令 -->\n"
        "\n"
        "### REQ-00X <标题>\n"
        "\n"
        "- [x] **AC-1**：\n"
        "\n"
        "## 结论\n"
        "\n"
        "（通过 / 发现的问题与处理）\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="main 批量回归门禁")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--new", action="store_true", help="打印回归记录草稿")
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--threshold", type=int, default=THRESHOLD)
    args = parser.parse_args()

    if args.new:
        print(draft(args.main_ref))
        return 0

    records = load_records()
    record = latest_record(records)
    if record:
        since = record["covered-until"]
    else:
        since = subprocess.run(
            ["git", "rev-list", "--max-parents=0", args.main_ref],
            cwd=ROOT, capture_output=True, text=True,
        ).stdout.split()[0]
    problems = evaluate(records, commit_subjects(since, args.main_ref), args.threshold)
    if problems:
        print("main 批量回归门禁未通过：\n")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(
        f"main 批量回归门禁通过（自 {since} 起累积的特性合入未达阈值 {args.threshold}）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
