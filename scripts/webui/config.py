"""配置：默认值 + `webui.config.json` + 环境变量覆盖。

两个刻意的设计决定：

1. **不依赖 `tomllib`**（3.11+ 才有）：本仓库声明支持 Python 3.10，配置文件用 JSON。
2. **不提供把服务暴露到非环回地址的开关**：`host` 只能填环回地址，填别的直接拒绝启动
   （AC-3.1）。远程访问/鉴权将来**另开需求**，不能靠改一个配置项悄悄放开安全边界。

环境变量（全部可选）：`WEBUI_PORT`、`WEBUI_OUTPUT_ROOT`、`WEBUI_CACHE_DIR`、
`WEBUI_ARCHIVE_ROOT`、`WEBUI_PLUGINS`（`os.pathsep` 分隔）、`WEBUI_NO_BROWSER`。
**没有 `WEBUI_HOST`**：见上一条。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILENAME = "webui.config.json"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"


class ConfigError(ValueError):
    """配置非法。启动前就要失败，绝不带着坏配置把服务跑起来。"""


def is_loopback(host: str) -> bool:
    """只认环回地址。空值不算。"""
    return (host or "").strip().lower() in LOOPBACK_HOSTS


@dataclass(frozen=True)
class Config:
    """运行配置。全部字段在启动时定稿（不可变，避免运行中被改）。"""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    output_root: Path = REPO_ROOT / "output"
    cache_dir: Path = REPO_ROOT / "output" / ".webui_cache"
    # 原始存档是「花钱换来的」资产，默认放仓库之外（AC-4.3）。
    archive_root: Path = Path.home() / "turtle_archive"
    max_concurrent_jobs: int = 3
    job_log_tail: int = 200
    plugins: tuple = ()
    open_browser: bool = True

    def to_json(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "output_root": str(self.output_root),
            "cache_dir": str(self.cache_dir),
            "archive_root": str(self.archive_root),
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "job_log_tail": self.job_log_tail,
            "plugins": list(self.plugins),
            "open_browser": self.open_browser,
        }


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_config_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # 配置文件写坏了必须报错，不能静默用默认值
        raise ConfigError(f"配置文件 {path} 不是合法 JSON：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"配置文件 {path} 顶层必须是对象（JSON object）")
    return raw


def load_config(path: Path | str | None = None, env: dict | None = None) -> Config:
    """按「默认值 → 配置文件 → 环境变量」的顺序取值，并校验。"""
    env = dict(os.environ if env is None else env)
    config_path = Path(path) if path else REPO_ROOT / CONFIG_FILENAME
    raw = _read_config_file(config_path)

    values: dict = {}

    if "host" in raw:
        values["host"] = str(raw["host"]).strip()
    if "port" in raw:
        values["port"] = raw["port"]
    for key in ("output_root", "cache_dir", "archive_root"):
        if key in raw:
            values[key] = Path(str(raw[key])).expanduser()
    for key in ("max_concurrent_jobs", "job_log_tail"):
        if key in raw:
            values[key] = raw[key]
    if "open_browser" in raw:
        values["open_browser"] = bool(raw["open_browser"])
    if "plugins" in raw:
        plugins = raw["plugins"]
        if not isinstance(plugins, list):
            raise ConfigError("配置项 plugins 必须是数组")
        values["plugins"] = tuple(str(item) for item in plugins)

    # 环境变量覆盖（优先级最高）
    if env.get("WEBUI_PORT", "").strip():
        values["port"] = env["WEBUI_PORT"].strip()
    for key, var in (
        ("output_root", "WEBUI_OUTPUT_ROOT"),
        ("cache_dir", "WEBUI_CACHE_DIR"),
        ("archive_root", "WEBUI_ARCHIVE_ROOT"),
    ):
        if env.get(var, "").strip():
            values[key] = Path(env[var].strip()).expanduser()
    if env.get("WEBUI_PLUGINS", "").strip():
        values["plugins"] = tuple(
            item for item in env["WEBUI_PLUGINS"].split(os.pathsep) if item.strip()
        )
    if env.get("WEBUI_NO_BROWSER", "").strip():
        values["open_browser"] = not _truthy(env["WEBUI_NO_BROWSER"])

    try:
        port = int(values.get("port", DEFAULT_PORT))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"port 必须是整数：{values.get('port')!r}") from exc
    host = str(values.get("host", DEFAULT_HOST)).strip() or DEFAULT_HOST
    if not is_loopback(host):
        raise ConfigError(
            f"host={host!r} 不是环回地址：本面板只允许监听本机（127.0.0.1 / localhost / ::1）。"
            "远程访问与鉴权属于另一个需求，不在本期范围内。"
        )
    if not 0 <= port <= 65535:
        raise ConfigError(f"port 必须在 0..65535 之间（0 = 随机端口）：{port}")
    try:
        max_jobs = int(values.get("max_concurrent_jobs", 3))
        log_tail = int(values.get("job_log_tail", 200))
    except (TypeError, ValueError) as exc:
        raise ConfigError("max_concurrent_jobs / job_log_tail 必须是整数") from exc
    if max_jobs < 1:
        raise ConfigError(f"max_concurrent_jobs 至少为 1：{max_jobs}")
    if log_tail < 10:
        raise ConfigError(f"job_log_tail 至少为 10（否则日志没法看）：{log_tail}")

    return Config(
        host=host,
        port=port,
        output_root=Path(values.get("output_root", Config.output_root)),
        cache_dir=Path(values.get("cache_dir", Config.cache_dir)),
        archive_root=Path(values.get("archive_root", Config.archive_root)),
        max_concurrent_jobs=max_jobs,
        job_log_tail=log_tail,
        plugins=tuple(values.get("plugins", ())),
        open_browser=bool(values.get("open_browser", True)),
    )
