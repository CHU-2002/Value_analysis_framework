"""接口清单**从代码扫出来**（`DATA_LAYER_PLAN` §5.2 的 D3）。

手抄一份接口清单必然漂移：新写一处取数却忘了让它进拉取范围，后果是**静默**的
——离线重建时某个小节永远是「数据缺失」，没人会立刻发现。所以清单由 AST 扫
`_safe_call("xxx", …)` / `_cached_basic_call("xxx", …)` 得到，再由测试断言
「注册表声明齐全」与「档位覆盖扫描集合」。

扫描范围**只有取数链**（`tushare_collector.py` + `tushare_modules/`）：
那是 `DataAccess` 的收口点，也是唯一「漏一处就静默缺数据」的地方。
`scripts/screener_core.py` 有它**自己**的客户端与 `_safe_call`，并入收口点是
`REQ-011` §17 的演进项（触发条件：选股器要与数据包用同一份行情），不在本期。
"""

from __future__ import annotations

import ast
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
COLLECTOR_MODULE = "tushare_collector.py"
MIXIN_PACKAGE = "tushare_modules"

# 收口点上的取数入口：前者是通用接口调用，后者是带缓存的基本信息调用。
CALL_NAMES = ("_safe_call", "_cached_basic_call")

# 与 `webui/plugins/commands.py::scan_cli_params` 同理：扫代码，不手抄。
PYTHON_GLOB = "*.py"

# 扫描结果按「文件 + mtime + 大小」缓存：一次目标枚举会问几百次「这个接口的字段并集是什么」，
# 每次重新解析取数链要 1 秒以上，而取数编排本身应该是纯计算（`estimate` 号称零成本）。
_SCAN_CACHE: dict = {}


def _cache_key(root: Path):
    files = collector_files(root)
    return (str(root), tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size)
                             for path in files))


def collector_files(scripts_dir: Path | None = None) -> list[Path]:
    """取数链的文件清单（相对路径可复现，排序稳定）。"""

    root = Path(scripts_dir) if scripts_dir else SCRIPTS_DIR
    files = []
    collector = root / COLLECTOR_MODULE
    if collector.is_file():
        files.append(collector)
    for path in sorted((root / MIXIN_PACKAGE).glob(PYTHON_GLOB)):
        if path.name == "__init__.py":
            continue
        files.append(path)
    return files


def scan_safe_calls(scripts_dir: Path | None = None) -> dict[str, list[dict]]:
    """``{接口名: [调用点, …]}``；每个调用点带文件、行号与关键字参数名。

    结果按「文件 + mtime + 大小」缓存：目标枚举会反复问「这个接口的字段并集是什么」，
    而 `estimate` 号称零成本，不该每次都重新解析整条取数链。
    """

    root = Path(scripts_dir) if scripts_dir else SCRIPTS_DIR
    key = _cache_key(root)
    cached = _SCAN_CACHE.get(key)
    if cached is not None:
        return cached
    found: dict[str, list[dict]] = {}
    for path in collector_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in CALL_NAMES:
                continue
            if not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            name = first.value
            literal_fields = [
                keyword.value.value for keyword in node.keywords
                if keyword.arg == "fields" and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ]
            param_literals = {
                keyword.arg: keyword.value.value for keyword in node.keywords
                if keyword.arg and keyword.arg not in NON_PARAM_KEYS
                and isinstance(keyword.value, ast.Constant)
            }
            found.setdefault(name, []).append({
                "file": path.relative_to(root.parent).as_posix(),
                "line": node.lineno,
                "call": func.attr,
                "kwargs": sorted(
                    keyword.arg for keyword in node.keywords if keyword.arg is not None
                ),
                # 字面量的 fields= 值：拉取用的字段并集由它推出来（见 union_fields）。
                "fields": literal_fields,
                # 字面量的其它语义入参（type="P" / curve_type="0" …）。
                "param_literals": param_literals,
            })
    result = {name: sorted(sites, key=lambda site: (site["file"], site["line"]))
              for name, sites in sorted(found.items())}
    # 只留最新一份：缓存是给「同一进程里反复问」用的，不是历史归档。
    _SCAN_CACHE.clear()
    _SCAN_CACHE[key] = result
    return result


def scan_dynamic_calls(scripts_dir: Path | None = None) -> list[dict]:
    """第一参数不是字符串字面量的调用点（如 ``self._safe_call(api, …)``）。

    它们**不**进接口清单（静态扫不出名字），但要能列出来，否则「扫到的就是全部」
    这句话就成了谎话；由 `--check` 与测试展示。
    """

    root = Path(scripts_dir) if scripts_dir else SCRIPTS_DIR
    found = []
    for path in collector_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in CALL_NAMES:
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                continue
            found.append({
                "file": path.relative_to(root.parent).as_posix(),
                "line": node.lineno,
                "call": func.attr,
                "argument": ast.unparse(node.args[0]) if node.args else "",
            })
    return sorted(found, key=lambda site: (site["file"], site["line"]))


# 这些入参不进「目标参数」：期次由期次范围决定，字段并集单独算（见 union_fields），
# 时间窗口由注册表声明（`registry.window_params`）。
# 注意 `ts_code` **在**这里：`yc_cb` 这类市场级接口的调用点把 ts_code 写成了字面量
# （`ts_code="1001.CB"`），拉取必须照抄，否则拉回来的记录挂在错的标的上、读取永远不命中。
NON_PARAM_KEYS = ("period", "fields", "start_date", "end_date", "limit")


def union_params(dataset: str, scripts_dir: Path | None = None) -> dict:
    """某接口在代码里出现过的**字面量**语义入参（如 `type="P"`、`curve_type="0"`）。

    值本身是变量的（如 `report_type=report_type`）扫不出来，由注册表的 `variants` 声明。
    """

    params: dict = {}
    for name, sites in scan_safe_calls(scripts_dir).items():
        if name != dataset:
            continue
        for site in sites:
            for key, value in site.get("param_literals", {}).items():
                params.setdefault(key, value)
    return params


def param_conflicts(scripts_dir: Path | None = None) -> list[dict]:
    """同一接口的同一入参在代码里出现了**不同字面量**的情形。

    这类冲突要显式处理（要么进注册表 variants，要么改代码）——静默取第一个会让
    「拉全」变成「拉到其中一种」，而缺的那种在离线重建里只会显示成「数据缺失」。
    """

    conflicts = []
    for name, sites in scan_safe_calls(scripts_dir).items():
        seen: dict = {}
        for site in sites:
            for key, value in site.get("param_literals", {}).items():
                if key in seen and str(seen[key]) != str(value):
                    conflicts.append({"dataset": name, "key": key, "values": [seen[key], value],
                                      "file": site["file"], "line": site["line"]})
                seen.setdefault(key, value)
    return conflicts


def missing_declarations(scripts_dir: Path | None = None) -> list[str]:
    """扫到但注册表没声明的接口（CI 直接判红）。"""

    from .registry import DATASETS

    return sorted(name for name in scan_safe_calls(scripts_dir) if name not in DATASETS)


def union_fields(dataset: str, scripts_dir: Path | None = None) -> str:
    """某接口在代码里请求过的字段**并集**（首见顺序）。

    拉取用并集、读取按请求投影。这样「调用点要的字段」与「仓里存的字段」不会因为
    各自写一份 `fields=` 而对不上——对不上的后果是离线重建静默少列。
    代码里没有 `fields=` 的接口返回空串（= 不限制字段）。
    """

    seen: list[str] = []
    for name, sites in scan_safe_calls(scripts_dir).items():
        if name != dataset:
            continue
        for site in sites:
            for fields in site.get("fields", ()):
                for field in str(fields).split(","):
                    field = field.strip()
                    if field and field not in seen:
                        seen.append(field)
    return ",".join(seen)


def fields_covered(dataset: str, fields, scripts_dir: Path | None = None) -> bool:
    """代码请求的字段是否都被 ``fields`` 覆盖（拉取参数的自检）。"""

    union = union_fields(dataset, scripts_dir)
    if not union:
        return True
    available = {item.strip() for item in str(fields or "").split(",") if item.strip()}
    return all(item.strip() in available for item in union.split(","))
