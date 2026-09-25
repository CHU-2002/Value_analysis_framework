#!/usr/bin/env python3
"""Named sub-excerpt references over the immutable evidence index.

一个索引块可以带多条摘录（块自身的 ``quote`` 与 ``alternative_quotes``）。
``<evidence_id>#<n>`` 把「第 n 条摘录」变成可单独引用的名字，于是同一个块里的不同数值
——资本支出行 vs 经营现金流行、ROE 行 vs 净利率行——可以被**分别引用**，
而不是只能从 ``alternative_quotes`` 里选一条（REQ-006.2 AC-2.5 / 发现 F25）。

单独成一个模块是为了让 ``evidence.py`` 与 ``schema.py`` 都能用同一套解析，
而不互相 import（两者本来就是包内平级模块）。
"""

from __future__ import annotations

#: 具名子段引用的分隔符（REQ-006.2 AC-2.5 / 发现 F25）：``<evidence_id>#<n>`` 表示该索引块
#: 的第 ``n`` 条摘录（``n=0`` 是块自身的 ``quote``，``n>0`` 是 ``alternative_quotes[n-1]``）。
#: 一个块里的不同数值（资本支出行 vs 经营现金流行）因此可以被**分别引用**，
#: 而不是只能从 ``alternative_quotes`` 里选一条、把另一个数值留成无摘录。
SUB_EXCERPT_SEPARATOR = "#"


def split_evidence_reference(reference: str) -> tuple[str, int | None]:
    """把 ``market_data:12:001#2`` 拆成 ``("market_data:12:001", 2)``。

    只把**末尾的纯数字**后缀当子段名，且 ``#`` 前必须还有内容；否则整串按块 id 处理
    （索引 id 本身若含 ``#`` 也不会被误拆）。
    """

    if not isinstance(reference, str) or SUB_EXCERPT_SEPARATOR not in reference:
        return (reference if isinstance(reference, str) else "", None)
    base, _, suffix = reference.rpartition(SUB_EXCERPT_SEPARATOR)
    if not base or not suffix.isdigit():
        return reference, None
    return base, int(suffix)


def evidence_reference_base(reference: str) -> str:
    """An evidence reference's chunk id, ignoring any sub-excerpt suffix."""

    return split_evidence_reference(reference)[0]


def format_evidence_reference(evidence_id: str, quote_index: int) -> str:
    """Name one excerpt of a chunk so several values from it stay separately citable."""

    if quote_index <= 0:
        return evidence_id
    return f"{evidence_id}{SUB_EXCERPT_SEPARATOR}{quote_index}"
