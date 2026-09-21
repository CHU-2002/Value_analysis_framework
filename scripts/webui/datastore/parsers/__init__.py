"""解析器注册表：数据集用名字指向一个解析函数。

解析器签名：`parser(sources: list[Path], params: dict) -> JSON 可序列化的数据`。
插件可以注册自己的解析器（`register_parser`），所以数据源扩展同样不需要改核心。
"""

from __future__ import annotations

from ...core.errors import NotFound

_PARSERS: dict = {}


def register_parser(name: str, parser, *, replace: bool = False) -> None:
    if name in _PARSERS and not replace:
        raise ValueError(f"解析器 {name!r} 已注册；要覆盖请显式传 replace=True")
    _PARSERS[name] = parser


def get_parser(name: str):
    parser = _PARSERS.get(name)
    if parser is None:
        raise NotFound(
            f"没有注册过解析器 {name!r}",
            hint=f"已注册：{sorted(_PARSERS)}；解析器由插件在 contribute() 里注册。",
        )
    return parser


def parser_names() -> tuple:
    return tuple(sorted(_PARSERS))


def reset_parsers() -> None:
    """清空注册表（测试隔离用；正常运行时不该调用）。"""
    _PARSERS.clear()


def register_builtin_parsers() -> None:
    """注册框架自带解析器。幂等：测试重置注册表后可以再调一次恢复。"""
    from . import markdown_tables

    register_parser("markdown_tables.first_table", markdown_tables.first_table, replace=True)
    register_parser("markdown_tables.named_table", markdown_tables.named_table, replace=True)


register_builtin_parsers()
