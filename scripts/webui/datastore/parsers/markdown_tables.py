"""通用 Markdown 表格解析（空格容忍）——数据包里的表格都靠它。

刻意**不做**的事：列数不一致时补齐空列。数据包的格式一变（少一列、多一列），
补齐会把「解析错了」伪装成「数据正常」；这里宁可报 `PARSE_COLUMNS_MISMATCH`
并指出是第几行、期望几列、实际几列。
"""

from __future__ import annotations

import re
from pathlib import Path

from ...core.errors import ArtifactMissing, ParseColumnsMismatch

_SECTION_RE = re.compile(r"^(#{2,3})[ \t]*(.+?)[ \t]*$", re.MULTILINE)
_SEPARATOR_CELL_RE = re.compile(r"^:?-{2,}:?$")
_NUMBER_RE = re.compile(r"^-?\d[\d,]*\.?\d*$")


def split_sections(text: str) -> list:
    """返回 [(标题, 正文)]，标题不含 `##` 前缀。"""
    matches = list(_SECTION_RE.finditer(text))
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(2).strip(), text[match.end():end]))
    return sections


def find_section(text: str, prefix: str) -> str:
    """按标题前缀找小节正文（如 `12.` 命中 `## 12. 关键财务指标`）。"""
    for title, body in split_sections(text):
        if title.startswith(prefix):
            return body
    raise ArtifactMissing(
        f"找不到小节：{prefix!r}",
        hint=f"文件里有这些小节：{[title for title, _ in split_sections(text)][:20]}",
    )


def parse_rows(body: str) -> list:
    """把一个 Markdown 表格切成「单元格列表的列表」；跳过 `---` 分隔行。"""
    rows = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and all(_SEPARATOR_CELL_RE.match(cell) for cell in cells if cell):
            continue
        rows.append(cells)
    return rows


def parse_number(value):
    """`'1,234.5'` → 1234.5；`'12.3%'` → 12.3；`'—'`/`''` → None；其他原样返回字符串。"""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "—", "-", "N/A", "n/a", "暂无"):
        return None
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    if _NUMBER_RE.match(text):
        number = float(text)
        return int(number) if number.is_integer() and "." not in text else number
    return value


def table_as_records(rows: list, *, numeric: bool = False) -> dict:
    """第一行当表头，其余当数据；列数不一致直接报错（不补齐）。"""
    if not rows:
        raise ArtifactMissing("表格里没有任何行")
    header = rows[0]
    if len(header) < 2:
        raise ParseColumnsMismatch(
            f"表头只有 {len(header)} 列，看起来不是表格",
            hint="期望形如 `| 指标 | 2026H1 | … |`。",
        )
    records = []
    for offset, row in enumerate(rows[1:], start=2):
        if len(row) != len(header):
            raise ParseColumnsMismatch(
                f"第 {offset} 行有 {len(row)} 列，表头有 {len(header)} 列",
                hint="表格结构变了：请确认数据包格式，或更新解析器版本（parser_version +1）。",
            )
        record = {}
        for column, cell in zip(header, row):
            record[column] = parse_number(cell) if numeric else cell
        records.append(record)
    return {"columns": _column_specs(header, records, numeric), "rows": records}


def _column_specs(header: list, records: list, numeric: bool) -> list:
    """列描述：数值列右对齐。

    形状必须与**表格面板的渲染契约**一致（`[{key,title,align}]`）——
    复验 N2：早先返回 `["指标", …]` 字符串列表，直接喂给 `render_table` 会 `AttributeError`
    并被降级成 INTERNAL，而仓库测试靠 provider 里手写适配绕过了这个坑。
    """
    specs = []
    for name in header:
        values = [record.get(name) for record in records]
        is_numeric = numeric and any(isinstance(value, (int, float)) for value in values)
        specs.append({"key": name, "title": name, "align": "right" if is_numeric else "left"})
    return specs


def _read_first(sources: list) -> tuple:
    if not sources:
        raise ArtifactMissing("没有可读的源文件")
    path = Path(sources[0])
    return path.name, path.read_text(encoding="utf-8")


def first_table(sources: list, params: dict) -> dict:
    """第一个源文件里的第一张表。`params` 可带 `numeric=True`。"""
    name, text = _read_first(sources)
    for _, body in split_sections(text):
        rows = parse_rows(body)
        if rows:
            result = table_as_records(rows, numeric=bool((params or {}).get("numeric")))
            result["source"] = name
            return result
    rows = parse_rows(text)
    if not rows:
        raise ArtifactMissing(f"{name} 里没有表格")
    result = table_as_records(rows, numeric=bool((params or {}).get("numeric")))
    result["source"] = name
    return result


def named_table(sources: list, params: dict) -> dict:
    """指定小节的表格：`params={"section": "12.", "numeric": True}`。"""
    section = (params or {}).get("section")
    if not section:
        return first_table(sources, params)
    name, text = _read_first(sources)
    rows = parse_rows(find_section(text, section))
    result = table_as_records(rows, numeric=bool((params or {}).get("numeric")))
    result["source"] = name
    result["section"] = section
    return result
