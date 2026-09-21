"""插件加载：内置列表 + `--plugins` 目录（仓库外扩展不必 fork 仓库）。

插件契约：模块暴露 `contribute(registry)`，在里面调用六类注册点。

失败策略分两种，是刻意区分的：

- **注册冲突**（同 id 被注册两次）→ **启动即失败**：静默覆盖会让某个功能凭空消失，
  而且没人能查出是谁顶掉的；
- **其他异常**（插件自身代码抛错）→ 只记录，不拖垮面板：一个坏插件不该让整页打不开。
"""

from __future__ import annotations

import importlib
import importlib.util
import traceback
from pathlib import Path

from ..core.errors import RegistrationConflict

# 首版还没有功能插件；随 REQ-009.1（按键）/ .2（视图）/ .4（采集）逐个加一行。
BUILTIN: tuple = ()


class PluginLoadError(RuntimeError):
    """必须让启动失败的错误（注册冲突、内置插件缺失）。"""


def load_plugins(registry, extra_dirs=(), builtin=None) -> list:
    """加载插件，返回 [(来源, 错误信息或 None)]。注册冲突抛 `PluginLoadError`。"""
    builtin = BUILTIN if builtin is None else builtin
    report: list = []
    for name in builtin:
        report.append(_load_builtin(registry, name))
    for directory in extra_dirs or ():
        report.extend(_load_directory(registry, Path(directory)))
    return report


def load_plugin_file(registry, path) -> tuple:
    """加载单个插件文件（测试与 `--plugins` 都走这一条路径，避免两套加载逻辑）。"""
    return _load_path(registry, Path(path))


def _contribute(registry, module, origin: str) -> None:
    if not hasattr(module, "contribute"):
        raise AttributeError(f"插件 {origin!r} 没有 contribute(registry) 函数")
    with registry.contribution_from(origin):
        module.contribute(registry)


def _load_builtin(registry, name: str) -> tuple:
    package = __package__ or "webui.plugins"
    origin = f"builtin:{name}"
    module = importlib.import_module(f"{package}.{name}")
    _contribute(registry, module, origin)
    return origin, None


def _load_directory(registry, directory: Path) -> list:
    results: list = []
    if not directory.is_dir():
        results.append((f"dir:{directory}", f"插件目录不存在：{directory}"))
        return results
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        results.append(_load_path(registry, path))
    return results


def _load_path(registry, path: Path) -> tuple:
    origin = f"file:{path}"
    try:
        spec = importlib.util.spec_from_file_location(f"webui_external_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法从 {path} 构造模块")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _contribute(registry, module, origin)
        return origin, None
    except RegistrationConflict as exc:
        # **只有注册表自己抛的冲突**才致命：插件代码里恰好抛出的同名异常不该致命（D10 / N9）。
        if getattr(exc, "from_registry", False):
            raise PluginLoadError(f"{origin} 注册冲突：{exc}") from exc
        return origin, f"RegistrationConflict: {exc}\n{traceback.format_exc()}"
    except Exception as exc:  # noqa: BLE001
        return origin, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
