"""`python -m scripts.webui`：启动本地控制台。

退出码：`0` 正常停止；`2` 配置/用法错误或插件加载失败；`3` 端口被占用。
端口被占用**不静默换端口**——否则你会以为服务跑在别的端口上（AC-3.1）。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import webbrowser

from . import __version__
from .config import ConfigError, is_loopback, load_config
from .core.errors import PortInUse
from .core.registry import build_registry
from .core.routes import install_core_routes
from .core.server import WebUIServer
from .datastore import DataStore
from .plugins import load_plugins


def build_application(config) -> tuple:
    """装配：空注册表 → 核心路由 → 数据层 → 插件。返回 (registry, 插件加载报告)。

    顺序说明：数据层只依赖注册表的**查询**方法（`dataset_spec`），而数据集是按需惰性解析的，
    所以插件注册数据集与建数据层谁先谁后都不影响。
    """
    registry = build_registry()
    install_core_routes(registry, config)
    registry.config = config
    registry.datastore = DataStore(config, spec_lookup=registry.dataset_spec)
    report = load_plugins(registry, config.plugins)
    return registry, report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.webui",
        description="本地图形化控制台（只监听本机；不联网拉数据）",
    )
    parser.add_argument("--config", help="配置文件路径（默认仓库根的 webui.config.json）")
    parser.add_argument("--host", help="监听地址，只允许环回（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, help="端口，0 = 随机（默认 8765）")
    parser.add_argument("--cache-dir", help="派生缓存目录（默认 output/.webui_cache）")
    parser.add_argument("--archive-root", help="原始存档根（默认 ~/turtle_archive，仓库之外）")
    parser.add_argument(
        "--plugins", action="append", default=[], help="额外插件目录（可重复；仓库外扩展）"
    )
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="清空派生缓存后退出（只丢「算出来的」数据，不动源文件，也不联网）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只装配（配置 + 插件 + 注册表），打印摘要后退出，不绑定端口",
    )
    parser.add_argument("--version", action="version", version=f"turtle-webui {__version__}")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    env = dict(os.environ)
    if args.port is not None:
        env["WEBUI_PORT"] = str(args.port)
    if args.cache_dir:
        env["WEBUI_CACHE_DIR"] = args.cache_dir
    if args.archive_root:
        env["WEBUI_ARCHIVE_ROOT"] = args.archive_root
    if args.plugins:
        env["WEBUI_PLUGINS"] = os.pathsep.join(args.plugins)
    if args.no_browser:
        env["WEBUI_NO_BROWSER"] = "1"

    if args.host and not is_loopback(args.host):
        print(
            f"host={args.host!r} 不是环回地址：本面板只允许监听本机"
            "（远程访问与鉴权属于另一个需求）。",
            file=sys.stderr,
        )
        return 2

    try:
        config = load_config(args.config, env=env)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    if args.host:
        config = dataclasses.replace(config, host=args.host)

    try:
        registry, report = build_application(config)
    except Exception as exc:  # noqa: BLE001（启动期把话说清楚，比traceback 更有用）
        print(f"插件加载失败：{exc}", file=sys.stderr)
        return 2

    for origin, error in report:
        if error:
            print(f"⚠️ 插件 {origin} 加载失败（已跳过，不影响其他功能）：{error.splitlines()[0]}")

    if args.clear_cache:
        removed = registry.datastore.invalidate()
        print(f"已清空派生缓存：{removed} 个文件（下次访问会自动重建；源数据与原始存档均未受影响）")
        return 0

    if args.check:
        print(
            json.dumps(
                {
                    "version": __version__,
                    "host": config.host,
                    "port": config.port,
                    "output_root": str(config.output_root),
                    "cache_dir": str(config.cache_dir),
                    "archive_root": str(config.archive_root),
                    "nav": [item.id for item in registry.nav_items()],
                    # 用注册表的公开方法，不再自己解包 origins() 的 (kind, key) 元组——
                    # 上一版就是在这里解包写错、导致 panels 恒为空（独立验收 D2）。
                    "panels": registry.panel_ids(),
                    "datasets": registry.datasets(),
                    "routes": [
                        f"{route.method} {route.template}" for route in registry.routes()
                    ],
                    "plugins": [{"origin": o, "error": bool(e)} for o, e in report],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    try:
        server = WebUIServer(config, registry)
    except PortInUse as exc:
        print(f"启动失败：{exc}\n提示：{exc.hint}", file=sys.stderr)
        return 3

    print(f"面板已启动：{server.base_url}（Ctrl-C 停止；只监听本机）")
    if config.open_browser:
        try:
            webbrowser.open(server.base_url)
        except Exception:  # noqa: BLE001（打不开浏览器不该让服务起不来）
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
