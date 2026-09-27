"""Token lookup and fingerprinting. Plain credentials are never persisted."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8] if token else ""


def resolve_token(env=None, dotenv_path=None) -> str:
    env = os.environ if env is None else env
    token = str(env.get("TUSHARE_TOKEN", "")).strip()
    if token:
        return token
    path = Path(dotenv_path) if dotenv_path else Path(__file__).resolve().parents[3] / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ""
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == "TUSHARE_TOKEN":
            return value.strip().strip("\"'")
    return ""
