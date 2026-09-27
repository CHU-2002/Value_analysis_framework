"""安全 Markdown → HTML（AC-2.3）：**先转义，再渲染**。

顺序是刻意的：如果先渲染再转义，报告里的 `<script>` 已经被当成标签插进 DOM 了，
转义只能事后补救；先转义则原始 HTML 永远只是文本，渲染出来的标签全部由**本模块**
生成。所以这里只认一小撮 Markdown 语法，其余一律当文本。

不引第三方 Markdown 库（P6 零新增依赖，CI 只装 `requirements-test.txt`），
也刻意不支持内嵌 HTML——报告作者不需要它，而它正是 XSS 的入口。
"""

from __future__ import annotations

import re
from html import escape

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_FENCE_RE = re.compile(r"^\s*```(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
_SAFE_SCHEMES = ("http://", "https://", "mailto:")
_CODE_PLACEHOLDER = "\x00code{}\x00"


def _safe_href(url: str) -> str:
    """只允许 http(s)/mailto 与站内相对链接；`javascript:` 之类一律拒绝。

    `url` 已经过 `html.escape`，所以属性里不会提前闭合引号。
    """
    lowered = url.strip().lower()
    if _SCHEME_RE.match(lowered) and not lowered.startswith(_SAFE_SCHEMES):
        return ""
    return url.strip()


def _inline(text: str) -> str:
    """行内语法。**输入必须是已经转义过的文本**，输出才安全。"""
    codes: list = []

    def stash(match):
        codes.append(match.group(1))
        return _CODE_PLACEHOLDER.format(len(codes) - 1)

    text = _CODE_RE.sub(stash, text)

    def link(match):
        label, url = match.group(1), _safe_href(match.group(2))
        if not url:
            return label or escape(match.group(2))
        return f'<a href="{url}" rel="noreferrer noopener">{label or url}</a>'

    text = _LINK_RE.sub(link, text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)

    def restore(match):
        return f"<code>{codes[int(match.group(1))]}</code>"

    return re.sub(r"\x00code(\d+)\x00", restore, text)


def _split_row(line: str) -> list:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _render_table(rows: list) -> str:
    header = _split_row(rows[0])
    body_rows = rows[2:] if len(rows) > 1 and _TABLE_SEP_RE.match(rows[1]) else rows[1:]
    head_html = "".join(f"<th>{_inline(escape(cell))}</th>" for cell in header)
    body_html = "".join(
        "<tr>" + "".join(f"<td>{_inline(escape(cell))}</td>" for cell in row) + "</tr>"
        for row in body_rows
        if row
    )
    return f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"


def render(text: str) -> str:
    """把 Markdown 文本渲染成 HTML 片段（无 `<script>`、无原始 HTML）。"""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list = []
    paragraph: list = []
    list_items: list = []
    list_ordered = False
    table_rows: list = []
    code_lines: list = []
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{_inline(escape(' '.join(paragraph).strip()))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            tag = "ol" if list_ordered else "ul"
            items = "".join(f"<li>{_inline(escape(item))}</li>" for item in list_items)
            out.append(f"<{tag}>{items}</{tag}>")
            list_items.clear()

    def flush_table() -> None:
        if table_rows:
            out.append(_render_table(table_rows))
            table_rows.clear()

    def flush_all() -> None:
        flush_paragraph()
        flush_list()
        flush_table()

    for line in lines:
        if in_code:
            if _FENCE_RE.match(line):
                in_code = False
                out.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
            else:
                code_lines.append(line)
            continue
        if _FENCE_RE.match(line):
            flush_all()
            in_code = True
            code_lines = []
            continue
        if not line.strip():
            flush_all()
            continue
        if _TABLE_ROW_RE.match(line):
            flush_paragraph()
            flush_list()
            table_rows.append(line)
            continue
        flush_table()
        heading = _HEADING_RE.match(line)
        if heading:
            flush_all()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(escape(heading.group(2).strip()))}</h{level}>")
            continue
        if _HR_RE.match(line):
            flush_all()
            out.append("<hr>")
            continue
        quoted = _QUOTE_RE.match(line)
        if quoted:
            flush_all()
            out.append(f"<blockquote>{_inline(escape(quoted.group(1)))}</blockquote>")
            continue
        item = _LIST_RE.match(line)
        if item:
            flush_paragraph()
            ordered = item.group(2)[0].isdigit()
            if list_items and ordered != list_ordered:
                flush_list()
            list_ordered = ordered
            list_items.append(item.group(3))
            continue
        flush_list()
        paragraph.append(line.strip())

    if in_code and code_lines:  # 未闭合的代码围栏：按代码块收尾，不吞内容
        out.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
    flush_all()
    return "".join(out)


__all__ = ["render"]
