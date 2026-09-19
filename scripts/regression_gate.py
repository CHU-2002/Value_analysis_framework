#!/usr/bin/env python
"""main 批量回归门禁：攒够 N 条功能合入就必须补一次全量回归。

流程要求（见 docs/DEVELOPMENT.md）：main 不要求每次合并都跑全量回归，
但**累积到阈值**就必须跑一次，并把结果留成记录放到 docs/regression/。
没补记录，下一次 develop → main 的合并会被本门禁卡住。

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


def feature_commits(since: str, main_ref: str = "origin/main") -> list:
    proc = subprocess.run(
        ["git", "log", "--pretty=%s", f"{since}..{main_ref}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"git log 失败：{proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if FEATURE_RE.match(line)]


def evaluate(records: list, subjects: list, threshold: int = THRESHOLD) -> list:
    """返回问题列表；空列表表示通过。"""
    if len(subjects) < threshold:
        return []
    record = latest_record(records)
    detail = "\n  ".join(subjects)
    if record is None:
        return [
            f"main 已累积 {len(subjects)} 条功能合入（阈值 {threshold}），但 docs/regression/ 下没有任何回归记录。\n"
            f"  累积的功能合入：\n  {detail}\n"
            "  请先跑一次全量回归并留档：python scripts/regression_gate.py --new"
        ]
    return [
        f"自上次回归记录（{record['path'].name}，覆盖到 {record.get('covered-until')}）以来，"
        f"main 又累积了 {len(subjects)} 条功能合入（阈值 {threshold}）。\n"
        f"  累积的功能合入：\n  {detail}\n"
        "  请补一次全量回归并留档：python scripts/regression_gate.py --new，"
        "记录放入 docs/regression/"
    ]


def draft(main_ref: str) -> str:
    sha = subprocess.run(
        ["git", "rev-parse", "--short", main_ref],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.strip()
    records = load_records()
    if records:
        subjects = feature_commits(latest_record(records)["covered-until"], main_ref)
    else:
        first = subprocess.run(
            ["git", "rev-list", "--max-parents=0", main_ref],
            cwd=ROOT, capture_output=True, text=True,
        ).stdout.split()[0]
        subjects = feature_commits(first, main_ref)
    listing = "\n".join(f"- {subject}" for subject in subjects) or "- （无）"
    return (
        "---\n"
        f"date: {subprocess.run(['date', '+%F'], capture_output=True, text=True).stdout.strip()}\n"
        f"covered-until: {sha}\n"
        "reviewer: TBD\n"
        "full-suite: TBD（形如 1389 passed / 3 skipped，覆盖率 76.77%）\n"
        "coverage: TBD\n"
        "---\n"
        "\n"
        "# main 全量回归记录\n"
        "\n"
        "## 覆盖范围\n"
        "\n"
        f"覆盖到 `{sha}`，自上次记录以来累积的功能合入：\n\n{listing}\n"
        "\n"
        "## 全量测试\n"
        "\n"
        "```bash\nmake verify\n```\n"
        "\n"
        "结果：（粘贴 passed / skipped / 覆盖率）\n"
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
    since = record["covered-until"] if record else subprocess.run(
        ["git", "rev-list", "--max-parents=0", args.main_ref],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.split()[0]
    problems = evaluate(records, feature_commits(since, args.main_ref), args.threshold)
    if problems:
        print("main 批量回归门禁未通过：\n")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"main 批量回归门禁通过（自 {since} 起的功能合入未达阈值 {args.threshold}）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
