#!/usr/bin/env python
"""PR 描述守卫：该填的栏必须填实，不能留空或占位。

流程要求（见 docs/DEVELOPMENT.md）：

- 所有 PR：**必须填「需求编号」小节**（它是「本批需求」的唯一权威来源，正文提及
  不能替代），并填「研发自测（手工）」。
  自动化测试只能证明既有的断言，新功能的行为是否符合预期必须有人手工验过，
  并把步骤与观察结果写下来。子需求写完整编号 `REQ-NNN.S`（见
  docs/requirements/README.md §6.1）。

独立验收报告**不在这里要求**：评审按子需求/大特性收口，只在 PR 把编号状态推进到
`verified` 时才需要报告，那条判定在 `scripts/acceptance_gate.py`（它需要看 diff，
本脚本只有 PR 描述）。`--base` 参数保留以便 CI 不变。

CI 用本脚本拦住空栏；判断的是「有没有认真填」，不是「填得好不好」。
"""

import argparse
import re
from pathlib import Path

SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
REQ_RE = re.compile(r"REQ-\d{3}(?:\.\d+)?")
PLACEHOLDERS = {"", "-", "tbd", "无", "n/a", "na", "待填", "略", "不适用", "待补"}

SELFTEST_HEADING = "研发自测（手工）"
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


def evaluate(body: str, base: str = None) -> list:
    """返回问题列表；空列表表示通过。

    base 只作兼容保留（CI 仍传目标分支名）；独立验收报告的判定在 acceptance_gate。
    """
    problems = []
    requirements = section(body, REQUIREMENT_HEADING)
    if not meaningful(requirements, 5):
        problems.append(
            f"「## {REQUIREMENT_HEADING}」为空或只有占位：必须真的填上本 PR 服务的 REQ-NNN"
            "（子任务也要写它服务的需求；子需求写完整编号 REQ-NNN.S）"
            "——该小节是「本批需求」的唯一权威来源，"
            "正文其它地方提到编号不能替代；尚未登记请先读 docs/requirements/README.md"
        )

    selftest = section(body, SELFTEST_HEADING)
    if not meaningful(selftest, 20):
        problems.append(
            f"「## {SELFTEST_HEADING}」为空或只有占位：请写明手工验了什么、怎么验、"
            "看到什么结果（命令 + 观察到的输出）。只跑自动化测试不算研发自测。"
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 PR 描述必填栏")
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--base", default=None, help="PR 目标分支名（保留参数，报告判定已移到 acceptance_gate）")
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
