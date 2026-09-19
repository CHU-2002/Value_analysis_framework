#!/usr/bin/env python
"""PR 描述守卫：该填的栏必须填实，不能留空或占位。

流程要求（见 docs/DEVELOPMENT.md）：

- 所有 PR：写明「需求编号」，并填「研发自测（手工）」。
  自动化测试只能证明既有的断言，新功能的行为是否符合预期必须有人手工验过，
  并把步骤与观察结果写下来。
- 合入 `main` 的 PR：额外填「验收报告」，指向 docs/verification/ 下的报告文件。

CI 用本脚本拦住空栏；判断的是「有没有认真填」，不是「填得好不好」。
"""

import argparse
import re
import sys
from pathlib import Path

SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
REQ_RE = re.compile(r"REQ-\d{3}")
PLACEHOLDERS = {"", "-", "tbd", "无", "n/a", "na", "待填", "略", "不适用", "待补"}

SELFTEST_HEADING = "研发自测（手工）"
VERIFICATION_HEADING = "验收报告"
REQUIREMENT_HEADING = "需求编号"


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub("", text or "")


def section(body: str, title: str):
    """返回标题为 title 的小节正文；找不到返回 None。"""
    text = strip_comments(body)
    headings = list(SECTION_RE.finditer(text))
    for index, match in enumerate(headings):
        if match.group(1).strip() == title:
            start = match.end()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            return text[start:end].strip()
    return None


def meaningful(text, min_chars: int = 15) -> bool:
    """去掉占位与空白后是否还有实质内容。"""
    if not text:
        return False
    kept = [
        line.strip()
        for line in text.splitlines()
        if line.strip().lower() not in PLACEHOLDERS
    ]
    return len(re.sub(r"\s", "", "".join(kept))) >= min_chars


def evaluate(body: str, base: str) -> list:
    """返回问题列表；空列表表示通过。"""
    problems = []
    requirements = section(body, REQUIREMENT_HEADING)
    if not meaningful(requirements, 5) and not REQ_RE.search(strip_comments(body)):
        problems.append(
            f"「## {REQUIREMENT_HEADING}」为空：请填 REQ-NNN；"
            "尚未登记请先读 docs/requirements/README.md"
        )

    selftest = section(body, SELFTEST_HEADING)
    if not meaningful(selftest, 20):
        problems.append(
            f"「## {SELFTEST_HEADING}」为空或只有占位：请写明手工验了什么、怎么验、"
            "看到什么结果（命令 + 观察到的输出）。只跑自动化测试不算研发自测。"
        )

    if base == "main":
        verification = section(body, VERIFICATION_HEADING)
        if not meaningful(verification, 10) or "docs/verification/" not in (verification or ""):
            problems.append(
                f"合入 main 的 PR 必须填「## {VERIFICATION_HEADING}」并链接 "
                "docs/verification/ 下的独立验收报告（模板见 docs/verification/TEMPLATE.md）"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 PR 描述必填栏")
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--base", required=True, help="PR 目标分支名，如 develop / main")
    args = parser.parse_args()

    body = Path(args.body_file).read_text(encoding="utf-8")
    problems = evaluate(body, args.base)
    if problems:
        print("PR 描述校验未通过：\n")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"PR 描述校验通过（目标分支 {args.base}）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
