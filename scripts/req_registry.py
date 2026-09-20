#!/usr/bin/env python
"""需求编号注册表：父需求 `REQ-NNN` 与子需求 `REQ-NNN.S` 的唯一解析来源。

规则见 docs/requirements/README.md §3 与 §6.1：

- 父需求（一个大特性）占一个文件：`docs/requirements/REQ-NNN-*.md`；
- 子需求写在父需求文件的「## 子需求」小节里，形如 `### REQ-NNN.S <标题>`，**不单独成文件**；
- 子需求有自己的验收标准，编号 `AC-S.n`；父需求自己的验收标准仍是 `AC-n`。

为什么集中在这里：验收门禁、测试 scope 归属、追溯门禁都要回答同一组问题
（这个编号登记了吗？它归哪个文件？它有哪些 AC？）。此前三处各写一份 `REQ-\\d{3}`
正则，加一层编号必然漏改其中一处——那正是本轮要修的毛病。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_DIR = ROOT / "docs" / "requirements"

# 完整编号：父需求 REQ-009、子需求 REQ-009.2
REQ_ID_RE = re.compile(r"REQ-\d{3}(?:\.\d+)?")
# 子需求小节标题，如 "### REQ-009.2 摘要缓存"
SUB_HEADING_RE = re.compile(r"^###[ \t]*(REQ-\d{3}\.\d+)\b[^\n]*$", re.MULTILINE)
# 小节边界：1-3 级标题（子需求正文到下一个 ## / ### 标题为止）
SECTION_BOUNDARY_RE = re.compile(r"^#{1,3}[ \t]+\S", re.MULTILINE)
# 子需求小节里的 `- 状态：`in-progress`` 与 `- 目标：…`
SUB_STATUS_RE = re.compile(r"^[-*][ \t]*状态[：:][ \t]*`?([A-Za-z-]+)`?[ \t]*$", re.MULTILINE)
SUB_GOAL_RE = re.compile(r"^[-*][ \t]*目标[：:][ \t]*(\S.*)$", re.MULTILINE)


def parent_of(req_id: str) -> str:
    """`REQ-009.2` → `REQ-009`；父需求返回自身。"""
    return req_id.split(".", 1)[0]


def is_sub(req_id: str) -> bool:
    return "." in req_id


def entry_path(req_id: str):
    """该编号所属的需求文件；父需求未登记时返回 None（子需求随父需求判定）。"""
    if not REQ_ID_RE.fullmatch(req_id):
        return None
    matches = sorted(REQUIREMENTS_DIR.glob(f"{parent_of(req_id)}-*.md"))
    return matches[0] if matches else None


def sub_sections(text: str) -> dict:
    """返回 {子需求编号: 小节正文}（不含标题行，到下一个 1-3 级标题为止）。"""
    matches = list(SUB_HEADING_RE.finditer(text))
    sections = {}
    for index, match in enumerate(matches):
        rest = text[match.end() :]
        end = len(text)
        if index + 1 < len(matches):
            end = matches[index + 1].start()
        boundary = SECTION_BOUNDARY_RE.search(rest)
        if boundary:
            end = min(end, match.end() + boundary.start())
        sections[match.group(1)] = text[match.end() : end]
    return sections


def sub_ids() -> dict:
    """{子需求编号: 父需求文件路径}，供追溯门禁比对台账。"""
    found = {}
    for path in sorted(REQUIREMENTS_DIR.glob("REQ-*.md")):
        for sub_id in sub_sections(path.read_text(encoding="utf-8")):
            found[sub_id] = path
    return found


def registered_ids() -> set:
    """已登记的父需求与子需求编号。父需求以文件名前缀为准（台账另有校验）。"""
    found = {path.name[:7] for path in REQUIREMENTS_DIR.glob("REQ-*.md")}
    found.update(sub_ids())
    return found


def ac_ids(req_id: str) -> list:
    """该编号**自己**的验收标准编号。

    父需求只取父级 `**AC-n**`（子需求的 `AC-S.n` 带小数点，不会误匹配）；
    子需求只取自己小节里的 `**AC-S.n**`。返回的编号不含 `AC-` 前缀，
    与既有门禁的写法保持一致（父需求 `['1']`、子需求 `['2.1', '2.2']`）。
    """
    if not REQ_ID_RE.fullmatch(req_id):
        return []
    path = entry_path(req_id)
    if path is None:
        return []
    text = path.read_text(encoding="utf-8")
    if is_sub(req_id):
        body = sub_sections(text).get(req_id)
        if body is None:
            return []
        index = re.escape(req_id.split(".", 1)[1])
        return re.findall(rf"\*\*AC-({index}\.\d+)\*\*", body)
    return re.findall(r"\*\*AC-(\d+)\*\*", text)


def sub_status(req_id: str):
    """子需求小节里声明的状态；父需求或未登记返回 None。"""
    if not is_sub(req_id):
        return None
    path = entry_path(req_id)
    if path is None:
        return None
    body = sub_sections(path.read_text(encoding="utf-8")).get(req_id)
    if body is None:
        return None
    match = SUB_STATUS_RE.search(body)
    return match.group(1) if match else None
