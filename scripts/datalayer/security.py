"""凭据处理：只存指纹、错误原文先脱敏、token 永不落盘（`REQ-011` 的 `AC-3` / `AC-4.7`）。

`redact` / `token_fingerprint` **复用** `scripts/webui/core/security.py` 里的实现——那份是
`REQ-009.4` 的 `AC-4.7` 已经验收过的判据，再抄一份就是两条会漂移的脱敏路径，
而脱敏一旦漂移就是凭据泄漏。这里只补数据层自己要用的 `resolve_token`。
"""

from __future__ import annotations

import os
from pathlib import Path

from webui.core.security import redact, token_fingerprint  # noqa: F401  （对外转出）

__all__ = ["redact", "resolve_token", "token_fingerprint"]


def resolve_token(env=None, dotenv_path=None) -> str:
    """从环境变量或项目 `.env` 读 token（口令**不经过命令行参数**）。

    与 `webui/archive/token.py::resolve_token` 同一口径：`TUSHARE_TOKEN` 优先，
    其次仓库根的 `.env`。
    """

    source = os.environ if env is None else env
    token = str(source.get("TUSHARE_TOKEN", "")).strip()
    if token:
        return token
    path = Path(dotenv_path) if dotenv_path else Path(__file__).resolve().parents[2] / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return ""
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip() == "TUSHARE_TOKEN":
            return value.strip().strip("\"'")
    return ""
