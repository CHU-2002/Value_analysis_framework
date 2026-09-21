"""路径模板匹配：`/api/v1/companies/{dir}/charts/{name}`。

为什么自己写而不是用正则拼字符串：参数段统一为「不含 `/` 的一段」并做 URL 解码，
一处实现、一处测试，避免每个插件各写一套解析。
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from .errors import BadRequest

_PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_PARAM_SEGMENT = r"([^/]+)"


class Route:
    """一条已注册的路由。`handler(ctx, **params)` 由内核调用。"""

    __slots__ = ("method", "template", "handler", "name", "_pattern", "_names")

    def __init__(self, method: str, template: str, handler, name: str = ""):
        self.method = method.upper()
        self.template = template
        self.handler = handler
        self._pattern, self._names = compile_template(template)
        self.name = name or template

    def match(self, path: str):
        """匹配成功返回参数字典，失败返回 None。"""
        matched = self._pattern.fullmatch(path)
        if matched is None:
            return None
        return {
            name: unquote(value)
            for name, value in zip(self._names, matched.groups())
        }


def compile_template(template: str):
    """把模板编译成正则；参数段不允许含 `/`、不允许重复名。"""
    names: list[str] = []
    parts: list[str] = []
    cursor = 0
    for matched in _PARAM_RE.finditer(template):
        name = matched.group(1)
        if name in names:
            raise BadRequest(f"路由模板 {template!r} 里参数名重复：{name}")
        names.append(name)
        parts.append(re.escape(template[cursor:matched.start()]))
        parts.append(_PARAM_SEGMENT)
        cursor = matched.end()
    parts.append(re.escape(template[cursor:]))
    # 未闭合的花括号（如 `/a/{b`）会被原样转义 —— 显式报错，避免注册出一个永远匹配不上的路由
    if "{" in template[cursor:] or "}" in template[cursor:]:
        raise BadRequest(f"路由模板 {template!r} 的花括号不配对")
    return re.compile("".join(parts)), tuple(names)


def find_route(routes, method: str, path: str):
    """返回 (route, params)；没匹配上返回 (None, None)。

    方法不匹配也算「没匹配上」：本面板没有需要区分 404/405 的场景，
    少一个分支就少一处会写错的地方。
    """
    method = method.upper()
    for route in routes:
        params = route.match(path)
        if params is not None and route.method == method:
            return route, params
    return None, None
